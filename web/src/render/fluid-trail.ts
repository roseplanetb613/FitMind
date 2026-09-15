/**
 * 拖拽尾流：把 RosePlanet 作品页那颗 `SplashCursor`（Navier-Stokes 流体）搬到
 * 3D 视图的**背景层**。
 *
 * 原效果在 `E:\RosePlanet\src\components\SplashCursor.tsx`，是一份全屏覆盖的
 * GPU 流体：curl → vorticity → divergence → pressure（20 次 Jacobi 迭代）→
 * advection，每帧把"染料"（dye）按速度场推进，鼠标经过/按下时往里打一发 splat。
 * 那份实现本身派生自 Pavel Dobryakov 的 WebGL-Fluid-Simulation，做法是行业通用解，
 * 这里只做三件事：**降到一半量级、搬到背景层、按本仓的降级与动效约定收口**。
 *
 * ## 相对原效果的三处调小（用户口径：「不要这么大和稠密，缩小一半」）
 *
 * | 参数 | 原值 | 现值 | 作用 |
 * |---|---|---|---|
 * | `SPLAT_RADIUS` | 0.2 | **0.1** | 每一发的**大小**，直接砍半 |
 * | `DYE_RESOLUTION` | 1440 | **720** | 染料纹理边长，砍半即"不那么稠密"，顺带省 3/4 显存与填充 |
 * | `DENSITY_DISSIPATION` | 3.5 | **4.0** | 染料衰减快一点，尾迹更短更轻，不至于糊成一片 |
 *
 * ## 为什么它是**独立的一块 canvas**、而不是塞进 three 的场景里
 *
 * 塞进场景要自建 FBO 管线并和 three 的渲染循环、材质系统缠在一起，而三处
 * 依赖（`applyStates` / `setHover` / `interactiveMeshes` 都遍历 `scene.children`）
 * 会立刻变复杂。独立 canvas 只要**DOM 顺序排在 `#stage` 之前**就在它下面，
 * 代价是 three 那边必须开 `alpha` 且不设 `scene.background`（见 render/scene.ts）。
 *
 * ⚠ 两个 WebGL context 是两套独立的 GL 状态机，互不干扰；浏览器上限通常 8~16 个，
 * 用掉 2 个无风险。
 *
 * ## 降级（本仓"全降级"传统）
 *
 * · `getContext` 拿不到 WebGL（旧机器 / 被禁用）→ 返回 `null`，页面照常跑
 * · 浮点渲染纹理格式一个都不支持 → 同样返回 `null`，不抛
 * · 用户开了"减少动效" → 不创建（与心脏辉光的处理一致）
 * 三种情况**都不抛错、都不留半截状态**。
 */
import { prefersReducedMotion } from './glow'

/** 每一发 splat 的半径。原效果 0.2，按用户口径砍半。 */
export const SPLAT_RADIUS = 0.1

/**
 * 染料纹理边长。原效果 1440（≈2K 宽），砍半到 720。
 *
 * 这个值同时决定"多稠密"和"多贵"：纹理面积是平方关系，1440→720 让每帧的
 * advection 填充量降到 1/4。背景动效不值当用 2K 的染料。
 */
export const DYE_RESOLUTION = 720

/** 速度场纹理边长。保持原值 —— 流体**形状**靠它，本来就不大（128）。 */
export const SIM_RESOLUTION = 128

/** 染料耗散。原效果 3.5；调到 4.0 让尾迹收得快一点，不糊满整屏。 */
export const DENSITY_DISSIPATION = 4.0

/** 速度耗散。原值。 */
export const VELOCITY_DISSIPATION = 2

/** 涡量强度。原值 —— 它决定"丝状感"，调低会变成一团糊。 */
export const CURL = 3

/** 压力迭代次数。原值。调低会让流体发散（出现明显压缩感）。 */
export const PRESSURE_ITERATIONS = 20

/** splat 的推力。原值。 */
export const SPLAT_FORCE = 6000

/**
 * 尾流颜色。**用本仓的强调色青绿 `#5fd4c4`，不是原效果的绿 `#00ff88`** ——
 * 3D 视图、工具栏、卡片全是这套青绿，换一个绿会在深底上显得像另一个产品。
 * 想还原成 RosePlanet 那个绿，把这里改回 `#00ff88` 即可（只此一处）。
 */
export const TRAIL_COLOR = '#5fd4c4'

/**
 * 像素比上限。与 `render/scene.ts` 的 `MAX_PIXEL_RATIO` 取同一个值：那是 4K +
 * 高 DPI 下压住填充量的既定结论，背景层没理由比主画面更清晰。
 */
export const PIXEL_RATIO_CAP = 1.5

export interface FluidTrailHandle {
  dispose: () => void
}

type GL = WebGLRenderingContext | WebGL2RenderingContext

interface FBO {
  texture: WebGLTexture
  fbo: WebGLFramebuffer
  width: number
  height: number
  texelSizeX: number
  texelSizeY: number
  attach: (id: number) => number
}

interface DoubleFBO {
  width: number
  height: number
  texelSizeX: number
  texelSizeY: number
  read: FBO
  write: FBO
  swap: () => void
}

interface Pointer {
  texcoordX: number
  texcoordY: number
  prevTexcoordX: number
  prevTexcoordY: number
  deltaX: number
  deltaY: number
  moved: boolean
}

interface Formats {
  formatRGBA: { internalFormat: number; format: number } | null
  formatRG: { internalFormat: number; format: number } | null
  formatR: { internalFormat: number; format: number } | null
  halfFloatTexType: number
  supportLinearFiltering: boolean
}

/** 程序 + 它的 uniform 位置表。抽出来是为了不必给每个 uniform 单独存字段。 */
class Program {
  readonly program: WebGLProgram
  readonly uniforms: Record<string, WebGLUniformLocation | null>

  constructor(private readonly gl: GL, vs: WebGLShader, fs: WebGLShader) {
    const p = gl.createProgram()
    if (!p) throw new Error('createProgram 返回 null')
    gl.attachShader(p, vs)
    gl.attachShader(p, fs)
    gl.linkProgram(p)
    this.program = p
    this.uniforms = {}
    const count = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS) as number
    for (let i = 0; i < count; i++) {
      const info = gl.getActiveUniform(p, i)
      if (!info) continue
      this.uniforms[info.name] = gl.getUniformLocation(p, info.name)
    }
  }

  bind(): void {
    this.gl.useProgram(this.program)
  }
}

/**
 * 把尾流挂到一张 canvas 上。**拿不到 WebGL 就返回 null**（降级，不抛）。
 *
 * 调用方负责把 canvas 放在 3D 画布的**下层**（DOM 顺序在前），并给它
 * `pointer-events: none` —— 否则它会吃掉 OrbitControls 的拖拽。
 */
export function attachFluidTrail(canvas: HTMLCanvasElement): FluidTrailHandle | null {
  // 系统里开了"减少动效"就不做这个动效。与心脏辉光同一条约定。
  if (prefersReducedMotion()) return null

  const ctx = getGL(canvas)
  if (!ctx) return null
  // **显式标注 `GL`，不要写成 `const gl = ctx`。** `ctx` 的声明类型含 null，
  // 而下面几十处在**嵌套函数声明**（blit/step/render/splat…）里闭包引用它 ——
  // TS 不会把外层 guard 的收窄带进嵌套函数，于是逐处报 `possibly null`。
  // 重新声明成一个非空类型是这里唯一干净的收口（改回隐式推断会让 tsc 挂掉）。
  const gl: GL = ctx
  const ext = getFormats(gl)
  // 没有任何可用的浮点渲染纹理格式 → 这台的驱动跑不了流体，静默放弃
  if (!ext.formatRGBA || !ext.formatRG) return null

  const dpr = Math.min(window.devicePixelRatio || 1, PIXEL_RATIO_CAP)
  const pointer: Pointer = {
    texcoordX: 0, texcoordY: 0, prevTexcoordX: 0, prevTexcoordY: 0,
    deltaX: 0, deltaY: 0, moved: false,
  }

  // ---------------------------------------------------------------- 着色器
  const vs = compile(gl, gl.VERTEX_SHADER, `
    precision highp float;
    attribute vec2 aPosition;
    varying vec2 vUv, vL, vR, vT, vB;
    uniform vec2 texelSize;
    void main() {
      vUv = aPosition * 0.5 + 0.5;
      vL = vUv - vec2(texelSize.x, 0.0);
      vR = vUv + vec2(texelSize.x, 0.0);
      vT = vUv + vec2(0.0, texelSize.y);
      vB = vUv - vec2(0.0, texelSize.y);
      gl_Position = vec4(aPosition, 0.0, 1.0);
    }
  `)
  const clearFs = compile(gl, gl.FRAGMENT_SHADER,
    'precision mediump float; precision mediump sampler2D; varying highp vec2 vUv;' +
    ' uniform sampler2D uTexture; uniform float value;' +
    ' void main() { gl_FragColor = value * texture2D(uTexture, vUv); }')
  const displayFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying vec2 vUv, vL, vR, vT, vB;
    uniform sampler2D uTexture;
    uniform vec2 texelSize;
    void main() {
      vec3 c = texture2D(uTexture, vUv).rgb;
      vec3 lc = texture2D(uTexture, vL).rgb, rc = texture2D(uTexture, vR).rgb;
      vec3 tc = texture2D(uTexture, vT).rgb, bc = texture2D(uTexture, vB).rgb;
      float dx = length(rc) - length(lc), dy = length(tc) - length(bc);
      vec3 n = normalize(vec3(dx, dy, length(texelSize)));
      c *= clamp(dot(n, vec3(0.0, 0.0, 1.0)) + 0.7, 0.7, 1.0);
      gl_FragColor = vec4(c, max(c.r, max(c.g, c.b)));
    }
  `)
  const splatFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D; varying vec2 vUv;
    uniform sampler2D uTarget; uniform float aspectRatio; uniform vec3 color;
    uniform vec2 point; uniform float radius;
    void main() {
      vec2 p = vUv - point.xy; p.x *= aspectRatio;
      vec3 splat = exp(-dot(p, p) / radius) * color;
      gl_FragColor = vec4(texture2D(uTarget, vUv).xyz + splat, 1.0);
    }
  `)
  const advectionFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D; varying vec2 vUv;
    uniform sampler2D uVelocity; uniform sampler2D uSource;
    uniform vec2 texelSize; uniform vec2 dyeTexelSize;
    uniform float dt; uniform float dissipation;
    vec4 bilerp(sampler2D s, vec2 uv, vec2 ts) {
      vec2 st = uv / ts - 0.5, iuv = floor(st), fuv = fract(st);
      vec4 a = texture2D(s, (iuv + vec2(0.5, 0.5)) * ts);
      vec4 b = texture2D(s, (iuv + vec2(1.5, 0.5)) * ts);
      vec4 c = texture2D(s, (iuv + vec2(0.5, 1.5)) * ts);
      vec4 d = texture2D(s, (iuv + vec2(1.5, 1.5)) * ts);
      return mix(mix(a, b, fuv.x), mix(c, d, fuv.x), fuv.y);
    }
    void main() {
      #ifdef MANUAL_FILTERING
        vec2 coord = vUv - dt * bilerp(uVelocity, vUv, texelSize).xy * texelSize;
        vec4 result = bilerp(uSource, coord, dyeTexelSize);
      #else
        vec2 coord = vUv - dt * texture2D(uVelocity, vUv).xy * texelSize;
        vec4 result = texture2D(uSource, coord);
      #endif
      gl_FragColor = result / (1.0 + dissipation * dt);
    }
  `, ext.supportLinearFiltering ? null : ['MANUAL_FILTERING'])
  const divergenceFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv, vL, vR, vT, vB; uniform sampler2D uVelocity;
    void main() {
      float L = texture2D(uVelocity, vL).x, R = texture2D(uVelocity, vR).x;
      float T = texture2D(uVelocity, vT).y, B = texture2D(uVelocity, vB).y;
      vec2 C = texture2D(uVelocity, vUv).xy;
      if (vL.x < 0.0) L = -C.x; if (vR.x > 1.0) R = -C.x;
      if (vT.y > 1.0) T = -C.y; if (vB.y < 0.0) B = -C.y;
      gl_FragColor = vec4(0.5 * (R - L + T - B), 0.0, 0.0, 1.0);
    }
  `)
  const curlFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv, vL, vR, vT, vB; uniform sampler2D uVelocity;
    void main() {
      float L = texture2D(uVelocity, vL).y, R = texture2D(uVelocity, vR).y;
      float T = texture2D(uVelocity, vT).x, B = texture2D(uVelocity, vB).x;
      gl_FragColor = vec4(0.5 * (R - L - T + B), 0.0, 0.0, 1.0);
    }
  `)
  const vorticityFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision highp float; precision highp sampler2D;
    varying highp vec2 vUv, vL, vR, vT, vB;
    uniform sampler2D uVelocity, uCurl; uniform float curl, dt;
    void main() {
      float L = texture2D(uCurl, vL).x, R = texture2D(uCurl, vR).x;
      float T = texture2D(uCurl, vT).x, B = texture2D(uCurl, vB).x;
      float C = texture2D(uCurl, vUv).x;
      vec2 force = 0.5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
      force /= length(force) + 0.0001;
      force *= curl * C;
      force.y *= -1.0;
      vec2 vel = texture2D(uVelocity, vUv).xy + force * dt;
      gl_FragColor = vec4(min(max(vel, -1000.0), 1000.0), 0.0, 1.0);
    }
  `)
  const pressureFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv, vL, vR, vT, vB;
    uniform sampler2D uPressure, uDivergence;
    void main() {
      float L = texture2D(uPressure, vL).x, R = texture2D(uPressure, vR).x;
      float T = texture2D(uPressure, vT).x, B = texture2D(uPressure, vB).x;
      gl_FragColor = vec4((L + R + B + T - texture2D(uDivergence, vUv).x) * 0.25, 0.0, 0.0, 1.0);
    }
  `)
  const gradientSubtractFs = compile(gl, gl.FRAGMENT_SHADER, `
    precision mediump float; precision mediump sampler2D;
    varying highp vec2 vUv, vL, vR, vT, vB;
    uniform sampler2D uPressure, uVelocity;
    void main() {
      float L = texture2D(uPressure, vL).x, R = texture2D(uPressure, vR).x;
      float T = texture2D(uPressure, vT).x, B = texture2D(uPressure, vB).x;
      vec2 v = texture2D(uVelocity, vUv).xy - vec2(R - L, T - B);
      gl_FragColor = vec4(v, 0.0, 1.0);
    }
  `)

  const clearProgram = new Program(gl, vs, clearFs)
  const displayProgram = new Program(gl, vs, displayFs)
  const splatProgram = new Program(gl, vs, splatFs)
  const advectionProgram = new Program(gl, vs, advectionFs)
  const divergenceProgram = new Program(gl, vs, divergenceFs)
  const curlProgram = new Program(gl, vs, curlFs)
  const vorticityProgram = new Program(gl, vs, vorticityFs)
  const pressureProgram = new Program(gl, vs, pressureFs)
  const gradientProgram = new Program(gl, vs, gradientSubtractFs)

  // 全屏四边形。**依赖 aPosition 落在 attribute 0** —— 这是该流体实现的通行前提
  //（原始出处 Pavel Dobryakov 的 WebGL-Fluid-Simulation 亦如此），刻意不为它
  // 每个 program 各查一次 getAttribLocation。
  const quad = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, quad)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, -1, 1, 1, 1, 1, -1]), gl.STATIC_DRAW)
  const index = gl.createBuffer()
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index)
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW)
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
  gl.enableVertexAttribArray(0)

  function blit(target: FBO | null): void {
    gl.viewport(0, 0, target ? target.width : gl.drawingBufferWidth,
                target ? target.height : gl.drawingBufferHeight)
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fbo : null)
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0)
  }

  // ---------------------------------------------------------------- 纹理
  function createFBO(w: number, h: number, fmt: { internalFormat: number; format: number },
                     type: number, filter: number): FBO {
    gl.activeTexture(gl.TEXTURE0)
    const texture = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texImage2D(gl.TEXTURE_2D, 0, fmt.internalFormat, w, h, 0, fmt.format, type, null)
    const fbo = gl.createFramebuffer()!
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0)
    gl.viewport(0, 0, w, h)
    gl.clear(gl.COLOR_BUFFER_BIT)
    return {
      texture, fbo, width: w, height: h, texelSizeX: 1 / w, texelSizeY: 1 / h,
      attach(id: number) {
        gl.activeTexture(gl.TEXTURE0 + id)
        gl.bindTexture(gl.TEXTURE_2D, texture)
        return id
      },
    }
  }

  function createDouble(w: number, h: number, fmt: { internalFormat: number; format: number },
                       type: number, filter: number): DoubleFBO {
    const a = createFBO(w, h, fmt, type, filter)
    const b = createFBO(w, h, fmt, type, filter)
    return {
      width: w, height: h, texelSizeX: 1 / w, texelSizeY: 1 / h,
      read: a, write: b,
      swap() { const t = this.read; this.read = this.write; this.write = t },
    }
  }

  function resolution(base: number): { width: number; height: number } {
    let aspect = gl.drawingBufferWidth / gl.drawingBufferHeight
    if (aspect < 1) aspect = 1 / aspect
    const min = Math.round(base)
    const max = Math.round(base * aspect)
    return gl.drawingBufferWidth > gl.drawingBufferHeight
      ? { width: max, height: min } : { width: min, height: max }
  }

  let dye: DoubleFBO | null = null
  let velocity: DoubleFBO | null = null
  let divergence: FBO | null = null
  let curl: FBO | null = null
  let pressure: DoubleFBO | null = null

  function initFramebuffers(): void {
    const sim = resolution(SIM_RESOLUTION)
    const dy = resolution(DYE_RESOLUTION)
    const filter = ext.supportLinearFiltering ? gl.LINEAR : gl.NEAREST
    const rgba = ext.formatRGBA!
    const rg = ext.formatRG!
    const r = ext.formatR!

    // 尺寸变了就地重建（改窗口大小会走到这里）。旧纹理由 GC/驱动回收，
    // 这里不逐个 deleteTexture —— 一帧内重建一次的量级不值得那份簿记。
    dye = createDouble(dy.width, dy.height, rgba, ext.halfFloatTexType, filter)
    velocity = createDouble(sim.width, sim.height, rg, ext.halfFloatTexType, filter)
    divergence = createFBO(sim.width, sim.height, r, ext.halfFloatTexType, gl.NEAREST)
    curl = createFBO(sim.width, sim.height, r, ext.halfFloatTexType, gl.NEAREST)
    pressure = createDouble(sim.width, sim.height, r, ext.halfFloatTexType, gl.NEAREST)
  }

  function resizeCanvas(): boolean {
    const w = Math.floor(canvas.clientWidth * dpr)
    const h = Math.floor(canvas.clientHeight * dpr)
    if (w > 0 && h > 0 && (canvas.width !== w || canvas.height !== h)) {
      canvas.width = w
      canvas.height = h
      return true
    }
    return false
  }

  // 先按当前 CSS 尺寸量一次，再建纹理 —— 反过来的话第一帧会用 300×150 的默认
  // 尺寸建一遍又立刻重建。canvas 若此刻还没布局（clientWidth 为 0），就先用
  // 默认尺寸建，帧循环里的 resizeCanvas() 会补上。
  resizeCanvas()
  initFramebuffers()

  // ---------------------------------------------------------------- 输入
  const color = hexToRGB(TRAIL_COLOR)

  function toTexcoord(e: MouseEvent): void {
    pointer.prevTexcoordX = pointer.texcoordX
    pointer.prevTexcoordY = pointer.texcoordY
    pointer.texcoordX = (e.clientX * dpr) / canvas.width
    pointer.texcoordY = 1 - (e.clientY * dpr) / canvas.height
    pointer.deltaX = pointer.texcoordX - pointer.prevTexcoordX
    pointer.deltaY = pointer.texcoordY - pointer.prevTexcoordY
    pointer.moved = pointer.deltaX !== 0 || pointer.deltaY !== 0
  }

  const onMove = (e: MouseEvent): void => toTexcoord(e)
  // 点一下给一发大的，和原效果一致（"戳一下也溅"）
  const onDown = (e: MouseEvent): void => {
    toTexcoord(e)
    splat(pointer.texcoordX, pointer.texcoordY,
          10 * (Math.random() - 0.5), 30 * (Math.random() - 0.5), color, 4)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mousedown', onDown)

  // ---------------------------------------------------------------- 每帧
  function splat(x: number, y: number, dx: number, dy: number,
                 c: { r: number; g: number; b: number }, radiusScale = 1): void {
    if (!velocity || !dye) return
    splatProgram.bind()
    gl.uniform1i(splatProgram.uniforms.uTarget, velocity.read.attach(0))
    gl.uniform1f(splatProgram.uniforms.aspectRatio, canvas.width / canvas.height)
    gl.uniform2f(splatProgram.uniforms.point, x, y)
    gl.uniform3f(splatProgram.uniforms.color, dx, dy, 0)
    gl.uniform1f(splatProgram.uniforms.radius,
                 correctRadius((SPLAT_RADIUS / 100) * radiusScale))
    blit(velocity.write)
    velocity.swap()

    gl.uniform1i(splatProgram.uniforms.uTarget, dye.read.attach(0))
    gl.uniform3f(splatProgram.uniforms.color, c.r, c.g, c.b)
    blit(dye.write)
    dye.swap()
  }

  /** 画布不是正方时把圆形 splat 拉回正圆。 */
  function correctRadius(radius: number): number {
    const aspect = canvas.width / canvas.height
    return aspect > 1 ? radius * aspect : radius
  }

  function step(dt: number): void {
    if (!velocity || !dye || !divergence || !curl || !pressure) return
    gl.disable(gl.BLEND)

    curlProgram.bind()
    gl.uniform2f(curlProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    gl.uniform1i(curlProgram.uniforms.uVelocity, velocity.read.attach(0))
    blit(curl)

    vorticityProgram.bind()
    gl.uniform2f(vorticityProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    gl.uniform1i(vorticityProgram.uniforms.uVelocity, velocity.read.attach(0))
    gl.uniform1i(vorticityProgram.uniforms.uCurl, curl.attach(1))
    gl.uniform1f(vorticityProgram.uniforms.curl, CURL)
    gl.uniform1f(vorticityProgram.uniforms.dt, dt)
    blit(velocity.write)
    velocity.swap()

    divergenceProgram.bind()
    gl.uniform2f(divergenceProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    gl.uniform1i(divergenceProgram.uniforms.uVelocity, velocity.read.attach(0))
    blit(divergence)

    clearProgram.bind()
    gl.uniform1i(clearProgram.uniforms.uTexture, pressure.read.attach(0))
    gl.uniform1f(clearProgram.uniforms.value, 0.1)
    blit(pressure.write)
    pressure.swap()

    pressureProgram.bind()
    gl.uniform2f(pressureProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    gl.uniform1i(pressureProgram.uniforms.uDivergence, divergence.attach(0))
    for (let i = 0; i < PRESSURE_ITERATIONS; i++) {
      gl.uniform1i(pressureProgram.uniforms.uPressure, pressure.read.attach(1))
      blit(pressure.write)
      pressure.swap()
    }

    gradientProgram.bind()
    gl.uniform2f(gradientProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    gl.uniform1i(gradientProgram.uniforms.uPressure, pressure.read.attach(0))
    gl.uniform1i(gradientProgram.uniforms.uVelocity, velocity.read.attach(1))
    blit(velocity.write)
    velocity.swap()

    advectionProgram.bind()
    gl.uniform2f(advectionProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY)
    if (!ext.supportLinearFiltering) {
      gl.uniform2f(advectionProgram.uniforms.dyeTexelSize,
                   velocity.texelSizeX, velocity.texelSizeY)
    }
    gl.uniform1i(advectionProgram.uniforms.uVelocity, velocity.read.attach(0))
    gl.uniform1i(advectionProgram.uniforms.uSource, velocity.read.attach(0))
    gl.uniform1f(advectionProgram.uniforms.dt, dt)
    gl.uniform1f(advectionProgram.uniforms.dissipation, VELOCITY_DISSIPATION)
    blit(velocity.write)
    velocity.swap()

    if (!ext.supportLinearFiltering) {
      gl.uniform2f(advectionProgram.uniforms.dyeTexelSize, dye.texelSizeX, dye.texelSizeY)
    }
    gl.uniform1i(advectionProgram.uniforms.uVelocity, velocity.read.attach(0))
    gl.uniform1i(advectionProgram.uniforms.uSource, dye.read.attach(1))
    gl.uniform1f(advectionProgram.uniforms.dissipation, DENSITY_DISSIPATION)
    blit(dye.write)
    dye.swap()
  }

  function render(): void {
    if (!dye) return
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.enable(gl.BLEND)
    displayProgram.bind()
    gl.uniform2f(displayProgram.uniforms.texelSize,
                 1 / gl.drawingBufferWidth, 1 / gl.drawingBufferHeight)
    gl.uniform1i(displayProgram.uniforms.uTexture, dye.read.attach(0))
    blit(null)
  }

  let last = performance.now()
  let raf = 0
  let alive = true

  function frame(): void {
    if (!alive) return
    raf = requestAnimationFrame(frame)
    const now = performance.now()
    // 上限 16.6ms：切到别的标签页再切回来时 dt 会很大，不夹住会让流体一步炸开
    const dt = Math.min((now - last) / 1000, 0.016666)
    last = now
    if (resizeCanvas()) initFramebuffers()
    if (pointer.moved) {
      pointer.moved = false
      splat(pointer.texcoordX, pointer.texcoordY,
            pointer.deltaX * SPLAT_FORCE, pointer.deltaY * SPLAT_FORCE, color)
    }
    step(dt)
    render()
  }
  frame()

  return {
    dispose(): void {
      alive = false
      cancelAnimationFrame(raf)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mousedown', onDown)
    },
  }
}

// ------------------------------------------------------------------ 工具

/**
 * 取 WebGL。**返回 null 是正常路径**（老机器 / 被策略禁用 / node 里跑测试）。
 *
 * ⚠ `alpha: true` 是必须的：这张 canvas 在 3D 画布**下面**，靠自己的透明区
 * 把底下的页面底色透出来；不透明的话它会变成一块挡住一切的板子。
 */
function getGL(canvas: HTMLCanvasElement): GL | null {
  const params: WebGLContextAttributes = {
    alpha: true, depth: false, stencil: false, antialias: false, preserveDrawingBuffer: false,
  }
  const gl2 = canvas.getContext('webgl2', params)
  const gl = gl2
    ? (gl2 as WebGL2RenderingContext)
    : ((canvas.getContext('webgl', params) as WebGLRenderingContext | null) ?? null)
  // **清成全透明**：这张 canvas 在 3D 画布下面，只有 dye 那一块该被画出来，
  // 其余地方必须是透明的（清成不透明黑会变成一块挡住页面底色的板子）。
  if (gl) gl.clearColor(0, 0, 0, 0)
  return gl
}

/** 挑出这台驱动支持的浮点渲染纹理格式。一个都没有 → 调用方放弃。 */
function getFormats(gl: GL): Formats {
  const isGL2 = typeof WebGL2RenderingContext !== 'undefined'
    && gl instanceof WebGL2RenderingContext
  let halfFloatTexType: number | undefined
  let supportLinearFiltering = false

  if (isGL2) {
    const g2 = gl as WebGL2RenderingContext
    g2.getExtension('EXT_color_buffer_float')
    supportLinearFiltering = !!g2.getExtension('OES_texture_float_linear')
    halfFloatTexType = g2.HALF_FLOAT
  } else {
    const g1 = gl as WebGLRenderingContext
    const hf = g1.getExtension('OES_texture_half_float')
    supportLinearFiltering = !!g1.getExtension('OES_texture_half_float_linear')
    halfFloatTexType = hf ? (hf as { HALF_FLOAT_OES: number }).HALF_FLOAT_OES : undefined
  }
  if (halfFloatTexType === undefined) {
    return {
      formatRGBA: null, formatRG: null, formatR: null,
      halfFloatTexType: 0, supportLinearFiltering: false,
    }
  }

  const g2 = gl as WebGL2RenderingContext
  const pick = (internal: number, format: number) =>
    supports(gl, internal, format, halfFloatTexType!) ? { internalFormat: internal, format } : null

  if (isGL2) {
    return {
      formatRGBA: pick(g2.RGBA16F, g2.RGBA) ?? pick(g2.RGBA, g2.RGBA),
      formatRG: pick(g2.RG16F, g2.RG) ?? pick(g2.RGBA16F, g2.RGBA),
      formatR: pick(g2.R16F, g2.RED) ?? pick(g2.RGBA16F, g2.RGBA),
      halfFloatTexType, supportLinearFiltering,
    }
  }
  const g1 = gl as WebGLRenderingContext
  return {
    formatRGBA: pick(g1.RGBA, g1.RGBA),
    formatRG: pick(g1.RGBA, g1.RGBA),
    formatR: pick(g1.RGBA, g1.RGBA),
    halfFloatTexType, supportLinearFiltering,
  }
}

function supports(gl: GL, internalFormat: number, format: number, type: number): boolean {
  const tex = gl.createTexture()
  if (!tex) return false
  gl.bindTexture(gl.TEXTURE_2D, tex)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, 4, 4, 0, format, type, null)
  const fbo = gl.createFramebuffer()
  if (!fbo) return false
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0)
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE
  gl.deleteFramebuffer(fbo)
  gl.deleteTexture(tex)
  return ok
}

function compile(gl: GL, type: number, source: string, keywords?: string[] | null): WebGLShader {
  const src = keywords ? keywords.map((k) => `#define ${k}\n`).join('') + source : source
  const shader = gl.createShader(type)
  if (!shader) throw new Error('createShader 返回 null')
  gl.shaderSource(shader, src)
  gl.compileShader(shader)
  return shader
}

/**
 * `#rrggbb` → 0~0.15 的 rgb。
 *
 * **乘 0.15 是原效果的做法，不要删**：染料是逐帧累加的（advection 每帧往
 * 同一处叠），按 1.0 的强度喂进去，几帧就烧成纯白 —— 那时颜色就没了，
 * 只剩一团过曝的白斑。0.15 让同一处可以叠十几帧才饱和。
 */
function hexToRGB(hex: string): { r: number; g: number; b: number } {
  let v = hex.replace('#', '')
  if (v.length === 3) v = v[0]! + v[0]! + v[1]! + v[1]! + v[2]! + v[2]!
  const k = 0.15 / 255
  return {
    r: parseInt(v.slice(0, 2), 16) * k,
    g: parseInt(v.slice(2, 4), 16) * k,
    b: parseInt(v.slice(4, 6), 16) * k,
  }
}
