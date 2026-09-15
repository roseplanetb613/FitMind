import * as THREE from 'three'
import { muscleState, type MuscleMapData } from '../data/types'
import {
  MAX_STARS,
  SIZE_VAR_FLOOR,
  SIZE_VAR_SPAN,
  TWINKLE_AMPLITUDE,
  TWINKLE_SPEED,
  starSpec,
} from './particles'
import { STAR_COLOR } from './palette'

/**
 * 星场：每块肌肉表面浮着一层星点，**密度/尺寸/亮度随恢复度**。
 *
 * 这是恢复度的第二条编码通道，补上 `emissive` 那条的缺口（渲染亮度在零线归零、
 * 不是单调函数）。语义映射本身在 `particles.ts` 的纯函数里，**本文件只负责画**。
 *
 * 形态选择：
 *   · **加性混合**（AdditiveBlending）—— 星点叠在一起会更亮，像真的星云
 *   · `depthWrite: false` —— 星点不写深度，所以不会挡住彼此或肌肉本体
 *   · **圆形柔光核**：`PointsMaterial` 不设 `map` 时每个点是**方块**（WebGL 的
 *     `gl_PointSize` 就是个方形 quad），放大看是硬边小方块，像噪点不像星。
 *     这里用一个极小的注入把 `gl_PointCoord` 转成径向衰减（见 `injectStarShader`）
 *   · **每颗星独立尺寸 + 独立相位闪烁**：材质是每肌群一个，`size`/`opacity` 是
 *     **每材质**一个值 —— 不注入的话同一条前臂上的 249 颗星大小亮度完全一致。
 *     属性在 `starVaryings` 里生成（确定性伪随机，可复现）
 *   · 星场是**独立的 Group**（`scene.add(stars)`），**不在 `body.children` 里** ——
 *     这一点是承重的：`interactiveMeshes` 与 `applyStates` 都只遍历 `body.children`，
 *     所以它们天然看不到星点。否则点选会命中一个没有实体的点云，
 *     而 `applyStates` 会试图往 `PointsMaterial` 上设 `wireframe` 这种它没有的属性。
 *     星点自己带 `userData.muscleId`，那是给 `applyStars` 找目标用的，不是给点选的。
 */

/** 确定性伪随机（同 seed 同序列）—— 星点位置抖动必须可复现，不能用 Math.random */
function rng(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s ^= s << 13
    s ^= s >>> 17
    s ^= s << 5
    return ((s >>> 0) % 100000) / 100000
  }
}

function hashSeed(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/**
 * 在 mesh 表面采样 `MAX_STARS` 个点，坐标是 **world 空间**。
 *
 * 直接跳着取顶点 —— 减面后的肌群网格顶点数远多于 MAX_STARS，
 * 均匀步长采样就足够散开，不需要真的做面积加权。
 */
export function sampleSurface(mesh: THREE.Mesh, maxCount = MAX_STARS): Float32Array {
  const pos = mesh.geometry.attributes.position
  const n = pos.count
  const take = Math.min(maxCount, n)
  const out = new Float32Array(take * 3)
  const v = new THREE.Vector3()
  const step = n / take
  const jitter = rng(hashSeed(mesh.userData.muscleId ?? mesh.name ?? 'x'))
  for (let i = 0; i < take; i++) {
    const idx = Math.min(n - 1, Math.floor(i * step))
    v.fromBufferAttribute(pos, idx)
    // 极小的抖动：避免多个星点正好落在同一顶点上（会看起来像一个大点）
    v.x += (jitter() - 0.5) * 0.004
    v.y += (jitter() - 0.5) * 0.004
    v.z += (jitter() - 0.5) * 0.004
    // **必须用 matrixWorld，不能用 matrix。** 星场是 body 的**兄弟**节点，
    // 而 `normalizeToBodyHeight` 把缩放加在 body 上 —— 用局部坐标采样的话，
    // 星点会比渲染出来的人体大 95 倍、整个飞出画面（实测：肌肉 World y 0~1.785，
    // 星点 0~170）。用 world 空间采样，星场放在原点就与人体对齐。
    v.applyMatrix4(mesh.matrixWorld)
    out[i * 3] = v.x
    out[i * 3 + 1] = v.y
    out[i * 3 + 2] = v.z
  }
  return out
}

export interface StarVaryings {
  /** 尺寸系数。分布见 particles.ts 的 SIZE_VAR_*，**均值恒为 1** */
  aSize: Float32Array
  /** 闪烁相位（0..1，转弧度在 shader 里做） */
  aPhase: Float32Array
  /** 闪烁速度系数（0.7~1.3）—— 让星不整片同频，但也不至于有的快有的慢 */
  aSpeedVar: Float32Array
}

/**
 * 每颗星的独立属性。`take` 必须与 `sampleSurface` 采到的点数一致。
 *
 * 用与位置抖动**不同的 seed**（异或一个常量）派生：同一颗星的位置抖动与它的
 * 尺寸/相位应当互不相关 —— 共用一个序列会让"抖得多的星恰好也更大"，
 * 虽然很微妙，但没有理由是那样。
 *
 * 确定性伪随机（不用 `Math.random`）—— 同 seed 同结果，否则每次刷新星云都会
 * 重新洗牌，用户会看到"闪一下换了张图"。
 */
export function starVaryings(seed: number, take: number): StarVaryings {
  const rand = rng(seed)
  const aSize = new Float32Array(take)
  const aPhase = new Float32Array(take)
  const aSpeedVar = new Float32Array(take)
  for (let i = 0; i < take; i++) {
    const u = rand()
    // 幂律式：少数大星 + 大量小星。均值 = FLOOR + SPAN/3 = 1（见 particles.ts 的推导）
    aSize[i] = SIZE_VAR_FLOOR + SIZE_VAR_SPAN * u * u
    aPhase[i] = rand()
    aSpeedVar[i] = 0.7 + 0.6 * rand()
  }
  return { aSize, aPhase, aSpeedVar }
}

/**
 * 星点的**初始态 = 无数据态**。见下方 PointsMaterial 处的说明：写死一个"好看"的
 * 默认值会在数据到达前糊满屏幕。
 */
const INITIAL = starSpec(null)

/** 星场的全局 uniform。**所有星点材质共享同一批对象**，所以每帧只更新一处。 */
export interface StarUniforms {
  /** 秒。由 `tickStars` 推进 */
  uTime: { value: number }
  /** 闪烁幅度；`prefersReducedMotion` 时归 0（星点仍在，只是不闪） */
  uTwinkleAmp: { value: number }
  uTwinkleSpeed: { value: number }
}

/**
 * 往 three 的 points 着色器注入两件事：**每颗星自己的尺寸/闪烁**、**圆形柔光核**。
 *
 * 为什么用 `onBeforeCompile` 注入而不是整体换 `ShaderMaterial`：透视衰减
 * （`gl_PointSize *= scale / -mvPosition.z`，`scale` 由 three 按视口高写入）、
 * 色彩空间转换、混合模式**全部由 three 保证正确**。自己写 ShaderMaterial 就得逐项
 * 复刻这些（`size` uniform 要乘 pixelRatio、`scale` 取 CSS 高度的一半……见
 * three 的 `WebGLMaterials.refreshUniformsPoints`），任何一处算错，星点大小会
 * 突然变一档 —— 而这次改动的本意是**只改质感、不改语义**。
 *
 * ⚠ 注入靠匹配 three 的着色器文本，所以这是**隐式契约**：three 升级改了模板就会
 * 静默失效（星点照常显示，只是不再闪、也不再是圆的）。`star-field.test.ts` 里有一条
 * 拿 `ShaderLib.points` 的**真模板**做的守卫 —— 模板一变那条就红，不至于静默。
 *
 * ⚠ 必须设 `customProgramCacheKey`：不设的话，星点材质与场景里任何其它
 * `PointsMaterial` 会算出同一个 cache key 而**共用 program** —— 两边一个注入过、
 * 一个没有，谁先用谁说了算，症状是随机出现的"星点忽然变方/忽然不闪"。
 */
export function injectStarShader(mat: THREE.PointsMaterial, u: StarUniforms): void {
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = u.uTime
    shader.uniforms.uTwinkleAmp = u.uTwinkleAmp
    shader.uniforms.uTwinkleSpeed = u.uTwinkleSpeed

    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
        attribute float aSize;
        attribute float aPhase;
        attribute float aSpeedVar;
        uniform float uTime;
        uniform float uTwinkleAmp;
        uniform float uTwinkleSpeed;
        varying float vTwinkle;`,
      )
      // three 在这里写的正是 `gl_PointSize = size;`，紧接着才是它自己的 sizeAttenuation。
      // 我们乘上去的个体差异会被它随后正确地做透视衰减 —— 不必碰那两行。
      .replace(
        'gl_PointSize = size;',
        `float tw = 1.0 + uTwinkleAmp * sin( uTime * uTwinkleSpeed * aSpeedVar + aPhase * 6.283185307 );
        vTwinkle = tw;
        gl_PointSize = size * aSize * tw;`,
      )

    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        varying float vTwinkle;`)
      // 方形 → 圆形柔光核。`gl_PointCoord` 是点内的 0..1 坐标，中心在 (0.5, 0.5)。
      // 平方衰减让中心更集中、边缘更软；四角（r2 ≥ 1）衰减到 0，靠 alpha=0 自然消失，
      // **不用 discard** —— 加色混合下 alpha 为 0 的贡献就是 0，而 discard 会让边缘变硬。
      .replace(
        'vec4 diffuseColor = vec4( diffuse, opacity );',
        `vec2 d = gl_PointCoord - vec2( 0.5 );
        float r2 = dot( d, d ) * 4.0;
        float core = pow( max( 0.0, 1.0 - r2 ), 2.0 );
        vec4 diffuseColor = vec4( diffuse, clamp( opacity * core * vTwinkle, 0.0, 1.0 ) );`,
      )
  }
  mat.customProgramCacheKey = () => 'star-twinkle'
}

/**
 * 为 `body` 里每个肌群 mesh 建一层星点，返回一个 Group。
 * **只在加载时建一次**（与几何同一条约定：数据只驱动材质/绘制范围，不重建几何）。
 *
 * ⚠ 建出来的星场是**不可见**的（见 INITIAL）—— 要等第一次 `applyStars` 才亮。
 */
export function buildStarField(body: THREE.Group, maxCount = MAX_STARS): THREE.Group {
  const field = new THREE.Group()
  field.name = 'star-field'
  // 全局 uniform 挂在 Group 的 userData 上，而**不是模块级变量**：同一页面若真出现
  // 两个星场，它们各闪各的；测试之间也不会互相污染。
  const uniforms: StarUniforms = {
    uTime: { value: 0 },
    uTwinkleAmp: { value: TWINKLE_AMPLITUDE },
    uTwinkleSpeed: { value: TWINKLE_SPEED },
  }
  field.userData.uniforms = uniforms
  // `sampleSurface` 用的是 `mesh.matrixWorld`，而它要到第一次渲染 / updateMatrixWorld
  // 才写入。这里显式刷一次，否则采到的点全落在"变换前"的位置上
  // （模型是 glb 来的，各 mesh 的 position 不为零）。
  body.updateMatrixWorld(true)

  for (const child of body.children) {
    const mesh = child as THREE.Mesh
    const id = child.userData.muscleId as string | undefined
    if (!mesh.isMesh || !id) continue

    // 位置与"个体属性"用**不同的 seed** 派生（理由见 starVaryings）。
    const pos = sampleSurface(mesh, maxCount)
    const vary = starVaryings(hashSeed(`${id}#vary`), pos.length / 3)

    const geo = new THREE.BufferGeometry()
      .setAttribute('position', new THREE.BufferAttribute(pos, 3))
      .setAttribute('aSize', new THREE.BufferAttribute(vary.aSize, 1))
      .setAttribute('aPhase', new THREE.BufferAttribute(vary.aPhase, 1))
      .setAttribute('aSpeedVar', new THREE.BufferAttribute(vary.aSpeedVar, 1))

    const mat = new THREE.PointsMaterial({
      color: STAR_COLOR,
      // **初始材质就是"还没有数据"那一态**（starSpec(null)：0 颗 / 尺寸 0 / 不透明 0）。
      //
      // 早先这里写死 `size: 3, opacity: 1` 且不设 drawRange，后果实测是刷新时
      // **一道白闪**：`buildStarField` 到第一次 `applyStars` 之间隔着一次网络请求，
      // 那几帧里每块肌肉都画出**全部 256 颗**、每颗直径 3 世界单位
      // （applyStars 给的真值是 0.002~0.007，差约 1000 倍），再叠上加色混合 ——
      // 28 块 ≈ 7000 个白团糊满屏幕。
      //
      // 用 `starSpec(null)` 而不是另写一组 0，是为了让"初始态 = 无数据态"这件事
      // 只有一个来源：改 particles.ts 的契约，这里自动跟着走。
      size: INITIAL.size,
      sizeAttenuation: true,
      transparent: true,
      opacity: INITIAL.opacity,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    injectStarShader(mat, uniforms)

    const points = new THREE.Points(geo, mat)
    // **必须同时把 drawRange 归零**：材质再透明，256 颗几何也照画不误 ——
    // 加色混合下"画了但透明"和"不画"的开销不一样，而且这里是白闪的另一半成因。
    points.geometry.setDrawRange(0, INITIAL.count)
    points.userData.muscleId = id
    points.userData.isStars = true
    points.renderOrder = 2 // 画在肌肉与外壳之上
    points.frustumCulled = false
    field.add(points)
  }
  return field
}

/**
 * 数据 → 星点。**只改 `drawRange` / 尺寸 / 不透明度，不重建几何**。
 *
 * 无记录的肌群 `starSpec(null).count === 0` → `drawRange` 为 0 → 一颗不画，
 * 于是"没有记录"与"恢复 0%"在视觉上分得开（后者仍有极少几颗暗星）。
 */
export function applyStars(field: THREE.Group | null, data: MuscleMapData): void {
  if (!field) return
  for (const child of field.children) {
    const id = child.userData.muscleId as string | undefined
    if (!id) continue
    const points = child as THREE.Points
    const mat = points.material as THREE.PointsMaterial
    const state = muscleState(data, id)
    const spec = starSpec(state && state.has_record ? state.recovery : null)

    points.geometry.setDrawRange(0, spec.count)
    mat.size = spec.size
    mat.opacity = spec.opacity
    // 这里**不设** `mat.needsUpdate = true`。size/opacity 是内置 uniform，
    // three 每次渲染都会从材质刷新（`WebGLMaterials.refreshUniformsPoints`），
    // 不需要重编译。设了反而会把 `material.version` 推高、让渲染器重新查一次
    // program 缓存 —— 代价不大但没有理由付。
  }
}

/**
 * 每帧推进闪烁。`elapsedMs` 用 `performance.now()` 那样的单调时钟。
 *
 * 星场为 null 时静默返回 —— 体块回落路径没有星场（同 `applyStars`）。
 */
export function tickStars(field: THREE.Group | null, elapsedMs: number): void {
  if (!field) return
  const u = field.userData.uniforms as StarUniforms | undefined
  if (!u) return
  u.uTime.value = elapsedMs / 1000
}

/**
 * 开关闪烁。`prefersReducedMotion` 的用户传 `false` ——
 * **星点仍然显示**（静态星云一样能读出密度与尺寸），只是不闪。
 * 这与心脏辉光的处理同一条约定：减少动效时辉光仍在，只是不呼吸。
 *
 * 归零的是**幅度**而不是停掉 `tickStars`：shader 里 `tw = 1.0 + 0` 恒为 1，
 * 等价于"没有这条通道"，不必为两种状态维护两条渲染路径。
 * 幅度围绕 1 对称，所以此时看到的亮度 = 动态用户的时间平均（见 particles.ts）。
 */
export function setStarMotion(field: THREE.Group | null, enabled: boolean): void {
  if (!field) return
  const u = field.userData.uniforms as StarUniforms | undefined
  if (!u) return
  u.uTwinkleAmp.value = enabled ? TWINKLE_AMPLITUDE : 0
}
