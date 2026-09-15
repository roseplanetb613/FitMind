/**
 * 心脏的辉光。
 *
 * **为什么不用后期 bloom（UnrealBloomPass）：**
 *  1. 它是**全屏**的 —— 会把每一块肌肉的自发光一起晕开。而自发光正是恢复度的
 *     语义通道（spec §5.2），糊成一片之后那个通道就读不准了：一块 90% 恢复的
 *     肌肉晕出的光会盖到邻居身上，看起来像邻居也恢复得不错。
 *  2. 用户此前明确报过卡顿。全屏 bloom 需要多一遍全屏渲染 + 多次降采样模糊，
 *     和当初把 `MAX_PIXEL_RATIO` 压到 1.5 的理由直接冲突。
 * 所以改成**局部**的：心脏位置上一张加色混合的光斑。一次 draw call，不影响别处。
 *
 * **贴图用 `DataTexture` 手工生成，不用 canvas。** 两个理由：
 *  · 不需要 DOM，于是本模块在 node 里可测（与 palette.ts / particles.ts 同一约定）
 *  · 不受页面 CSP 限制（canvas 转贴图在部分 CSP 下会被拒）
 *
 * ⚠ 本文件除 `buildHeartGlow` 外均为纯函数，可在 node 里断言。
 */
import * as THREE from 'three'
import { HEART_GLOW_COLOR } from './palette'

/** 辉光贴图的边长（像素）。64 够用 —— 它会被拉成几十个世界单位，看不到锯齿。 */
export const GLOW_TEX_SIZE = 64

/**
 * 辉光相对心脏包围盒最长边的放大倍数。1 = 与心脏同大。
 *
 * **2.4 实测太"贴"了**：贴图的 alpha 是三次方衰减，等效可见半径只有
 * sprite 半径的 ~1/3，所以 2.4 倍画出来只在心脏边缘外多出一点点红，
 * 看着像心脏边缘脏了，不像发光。3.2 之后可见光晕才真正探出心脏轮廓。
 *
 * 也别再往上加：心脏 12.7cm 时 sprite 已达 41cm，超过胸腔宽度，
 * 再大就变成"整个上半身在发光"，把临近肌肉的恢复度颜色也糊掉。
 */
export const GLOW_SCALE = 3.2

/** 辉光光斑的不透明度。加色混合，所以这个值直接决定"亮多少"。 */
export const GLOW_OPACITY = 0.75

/** 画在所有东西之后（肌肉 0 / other -1 / 外壳 -2）。见 buildHeartGlow 的 depthTest 说明。 */
export const GLOW_RENDER_ORDER = 10

/**
 * 呼吸周期（毫秒）。3.4 秒一次完整的明暗循环。
 *
 * **刻意慢、幅度刻意小**（原来是 4s / ±12%）。这是压着整屏解剖数据看的
 * 装饰性动效，忽快忽闪会抢注意力；《Web 内容无障碍指南》对闪烁的阈值也远低于
 * 这个频率。3.4s 仍在"安静的呼吸"区间里 —— 平均心率 60 的搏动是 1s，
 * 那才叫闪，不是呼吸。
 */
export const PULSE_PERIOD_MS = 3400

/**
 * 呼吸幅度（相对值）：0.25 即尺寸与亮度在 ±25% 之间摆动。
 *
 * 原值 0.12 在实测里"看不出来在呼吸"（用户报的），所以放大一倍多。
 * **上限受不透明度约束**：`pulseGlow` 里 `opacity = GLOW_OPACITY × k` 会在 1 处
 * 截断，一旦 `GLOW_OPACITY × (1 + 幅度) > 1`，波峰就被削平 —— 亮度变成
 * "涨上去、卡一会儿、掉下来"，读起来是卡顿而不是呼吸。0.75 × 1.25 = 0.9375，
 * 留了余量，改这两个常量时请一起算。
 */
export const PULSE_AMPLITUDE = 0.25

/**
 * 一张径向衰减的白色贴图，用作光斑的形状。
 *
 * alpha 走**三次方**衰减（不是线性）：线性衰减的光斑看起来像一块发白的圆盘，
 * 三次方把能量集中到中心，外圈干净地收掉，才像"发光"。
 * RGB 恒为白 —— 颜色交给 `SpriteMaterial.color` 去染，贴图只管形状。
 */
export function glowTexture(size: number = GLOW_TEX_SIZE): THREE.DataTexture {
  const data = new Uint8Array(size * size * 4)
  const c = (size - 1) / 2
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      // 归一化到 [0,1]：0 = 正中心，1 = 边缘
      const d = Math.min(1, Math.hypot(x - c, y - c) / c)
      const a = 1 - d
      const i = (y * size + x) * 4
      data[i] = 255
      data[i + 1] = 255
      data[i + 2] = 255
      data[i + 3] = Math.round(255 * a * a * a)
    }
  }
  const tex = new THREE.DataTexture(data, size, size)
  tex.needsUpdate = true
  return tex
}

export interface GlowPlacement {
  /** 世界坐标下的中心 */
  center: [number, number, number]
  /** 世界单位下的边长；不可用时为 0 */
  size: number
}

/**
 * 心脏 mesh → 辉光的世界位置与尺寸。
 *
 * 抽成纯函数是为了能在 node 里断言 —— `buildHeartGlow` 要建 Sprite（需要 GPU 侧的
 * 材质），而"位置算得对不对"这件事不该因此失去守卫。
 *
 * **接受一组对象，取并集包围盒。** 源 FBX（`muscle-map.json` 里的 `cardio_system`）
 * 把心脏拆成 20+ 个网格：心室/心房/瓣膜/主动脉根/冠状动脉…。`build-muscles.mjs`
 * 会把它们合并，所以**当前装配结果只有 1 块**，收数组与收单个等价。
 *
 * 之所以仍然收数组：辉光的正确性不该依赖"构建后恰好只剩一块"这个巧合。
 * 合并策略一改（或换模型），单对象版本会静默照着"心脏的某一小瓣"摆光斑 ——
 * 尺寸和位置都不对，而且不报任何错。多写六行换掉这个隐患是划算的。
 *
 * 尺寸取包围盒**最长边**：心脏是立体的，按 X 定尺寸会在某些角度露出光斑边界。
 */
export function heartGlowPlacement(
  heart: THREE.Object3D | readonly THREE.Object3D[],
  scale = GLOW_SCALE,
): GlowPlacement {
  const parts = (Array.isArray(heart) ? heart : [heart]) as THREE.Object3D[]
  const b = new THREE.Box3()
  for (const p of parts) {
    p.updateWorldMatrix(true, true)
    // 并集：空盒 ∪ 任一盒 = 那个盒，所以首轮不需要特判
    b.union(new THREE.Box3().setFromObject(p))
  }
  if (b.isEmpty()) return { center: [0, 0, 0], size: 0 }
  const c = b.getCenter(new THREE.Vector3())
  const extent = Math.max(b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z)
  return {
    center: [c.x, c.y, c.z],
    // 空包围盒 / 退化几何会给出 0 或 NaN，调用方据此跳过（见 buildHeartGlow）
    size: Number.isFinite(extent) ? extent * scale : 0,
  }
}

/**
 * 时间 → 呼吸系数，围绕 1 在 `[1-amplitude, 1+amplitude]` 之间摆动。
 *
 * **用正弦而不是三角波**：三角波在波峰波谷有折点，视觉上像"卡了一下"；
 * 正弦处处光滑，且在峰值附近按平方趋近（比三角波的线性趋近"停"得干脆），
 * 读起来才是呼吸。`pulse.test` 里有一条专门断言"峰值附近足够平"来守住这点。
 *
 * **用 sin 而不是 cos**：`sin` 在 t=0 处取到基准值 1，于是
 *   · 页面刚打开时辉光就是调好的那个亮度，不会一上来顶到最亮
 *   · 每过一个周期确实**回到基准**（`pulseAt(t + period) === pulseAt(t)`，
 *     且 `pulseAt(0) === 1`）—— `cos` 的话每个周期边界都停在**最大值**上，
 *     "回到基准"根本不成立，只有 1/4 相位才经过 1。
 *
 * 纯函数（不读时钟、不碰 three）—— 于是"它到底摆不摆、摆多大、均不均值"
 * 这几件事都能在 node 里断言，而不是只能靠眼睛看。
 */
export function pulseAt(
  ms: number,
  period: number = PULSE_PERIOD_MS,
  amplitude: number = PULSE_AMPLITUDE,
): number {
  // 周期非法（0 / 负数 / NaN）时退回"不摆动"，而不是产生 NaN 把 scale 传坏
  if (!Number.isFinite(period) || period <= 0) return 1
  const phase = (((ms % period) + period) % period) / period // 负数 ms 也要落在 [0,1)
  return 1 + amplitude * Math.sin(2 * Math.PI * phase)
}

/**
 * 用户是否要求"减少动效"。有动画的地方就应当尊重它。
 *
 * node 里没有 `matchMedia`，此时返回 false（=照常动画），于是本函数可在
 * 无 DOM 环境下直接调用而不用加守卫。
 */
export function prefersReducedMotion(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * 把呼吸应用到光斑上。**每帧调用。**
 *
 * 尺寸一律从 `userData.baseSize` 重算，**绝不在当前 scale 上乘** ——
 * 后者每帧复合一次，几秒钟就指数发散（0.88^n → 0），而画面只是"慢慢没了"，
 * 不会报任何错。这是本函数唯一容易写错的地方。
 */
export function pulseGlow(sprite: THREE.Sprite, ms: number): void {
  const base = sprite.userData.baseSize as number | undefined
  if (!(typeof base === 'number' && base > 0)) return
  const k = pulseAt(ms)
  sprite.scale.set(base * k, base * k, 1)
  const mat = sprite.material as THREE.SpriteMaterial
  mat.opacity = Math.min(1, GLOW_OPACITY * k)
}

/**
 * 在心脏位置生成一张加色光斑。
 *
 * **`depthTest: false` 是必须的**，不是可选的美化：心脏在胸腔里、外面裹着半透明
 * 肌肉，而 `transparent` 材质**默认仍然写深度** —— 开着深度测试的话，光斑会被
 * 前面肌肉的深度挡掉，表现就是"加了辉光但完全看不见"。关掉之后它恒可见，
 * 这也符合"光源"的物理直觉（光会从组织里透出来）。
 *
 * 几何不可用时返回 `null` 而不是抛错：辉光是装饰，不该让整个场景挂掉。
 */
export function buildHeartGlow(
  heart: THREE.Object3D | readonly THREE.Object3D[],
): THREE.Sprite | null {
  const { center, size } = heartGlowPlacement(heart)
  if (!(size > 0)) return null
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: glowTexture(),
      color: HEART_GLOW_COLOR,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      opacity: GLOW_OPACITY,
    }),
  )
  sprite.position.set(center[0], center[1], center[2])
  sprite.scale.set(size, size, 1) // Sprite 的 scale.z 不参与绘制
  // 呼吸时每帧要按基准尺寸重算，所以基准必须存下来（见 pulseGlow 的说明）
  sprite.userData.baseSize = size
  sprite.renderOrder = GLOW_RENDER_ORDER
  sprite.name = 'heart-glow' // 供测试与调试定位
  return sprite
}

/**
 * 让**心脏本体**的自发光与光斑同相呼吸。**每帧调用。**
 *
 * 为什么不能只让光斑呼吸：光斑是加色的，心脏本体那颗基色红是恒定的，
 * 光斑扫过去只是"外面亮一圈" —— 看起来像有人在心脏后面晃手电，
 * 不像心脏自己在搏动。把本体自发光一起摆，整个心才跟着明暗。
 *
 * `base` 由调用方传入（`scene.ts` 的 `NON_MUSCLE_EMISSIVE_INTENSITY`），
 * **刻意不 import 进来**：那个常量住在 `scene.ts`，而 `scene.ts` 已经 import 本
 * 模块，反向引会成环 —— 与 `NON_MUSCLE_COLOR` 当时搬去 `palette.ts` 同一个理由。
 *
 * 与 `pulseGlow` 一样，**基准值由调用方给、每帧重算**，绝不在当前值上累乘。
 */
export function pulseHeartEmissive(
  meshes: readonly THREE.Object3D[],
  ms: number,
  base: number,
): void {
  if (!(Number.isFinite(base) && base > 0)) return
  const v = base * pulseAt(ms)
  for (const m of meshes) {
    const mat = (m as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined
    // 材质可能被换过 / 不是 Standard（例如加载中的占位）——跳过而不是抛
    if (mat && 'emissiveIntensity' in mat) mat.emissiveIntensity = v
  }
}
