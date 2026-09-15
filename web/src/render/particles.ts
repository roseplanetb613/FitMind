/**
 * 星光粒子 —— 恢复度的**第二条编码通道**。
 *
 * 为什么需要它：现有语义走 `emissive`，而 `emissiveIntensity = |t|` 在零线归零，
 * 所以**渲染亮度并不是恢复度的单调函数**（实测 1.0 > 0.0 > 0.5，V 形）。
 * 结果是「亮 = 恢复好」只在 0.5 以上成立，丢色相（黑白截图）时读不出顺序。
 *
 * 粒子**密度**补上了这个缺口：星越多 = 恢复越好，**严格单调**，与色相无关。
 *
 * ⚠ **本文件是纯函数，零 three.js 依赖**（与 `palette.ts` 同一约定）——
 * 语义映射必须能在 node 里断言，否则"星多少"这件事就没有守卫。
 */

/** 单块肌肉最多多少颗星。256 在 28 块肌肉上是 ~7k 点，GPU 无压力。 */
export const MAX_STARS = 256

export interface StarSpec {
  /** 实际渲染的星数（0..maxCount）。**这是单调通道** */
  count: number
  /** 星点尺寸，**世界单位**（`PointsMaterial.sizeAttenuation: true`）。
   *  相机 fov 38°、距离 ~2.6 时，1 世界单位 ≈ 450 px —— 所以 1~6 px 的星
   *  对应 0.004~0.013。写大了会变成一团白斑。 */
  size: number
  /** 不透明度（0..1） */
  opacity: number
}

/**
 * 恢复度 → 星点参数。
 *
 * 契约（改动会让 `particles.test.ts` 红）：
 *   1. **单调**：`recovery` 越大 `count` 不减 —— 这是它作为冗余通道的全部价值
 *   2. **`null`（无记录）→ 0 颗星**，不是"中等数量"。未知不得与任何数值混淆
 *      （spec §5.4），而"中等数量的星"恰好会被读成"恢复一半"
 *   3. `0` 仍能有极少几颗（不是 0）—— 让"刚练完"与"没有记录"在**视觉上**可分：
 *      前者是"很暗但确实有"，后者是"什么都没有"
 *   4. 端点：1.0 → 满星且最亮
 */
export function starSpec(recovery: number | null, maxCount: number = MAX_STARS): StarSpec {
  if (recovery === null || !Number.isFinite(recovery)) {
    // 无记录：一颗都不给。见契约 2
    return { count: 0, size: 0, opacity: 0 }
  }

  const r = Math.min(1, Math.max(0, recovery))

  // 下限 3 颗：与"0 颗"（无记录）区分开。上不封顶到 maxCount
  const count = Math.round(3 + (maxCount - 3) * r)

  // 尺寸与不透明度也随恢复度走，让单调性在**三个维度上冗余成立** ——
  // 即便将来有人把 count 的曲线改平，size/opacity 仍能读出方向
  const size = 0.002 + 0.005 * r
  const opacity = 0.35 + 0.65 * r

  return { count, size, opacity }
}

/**
 * 闪烁：每颗星在**自己的相位**上明暗摆动。
 *
 * 为什么加：星点原来是全静止的 —— 心脏在搏动而星点纹丝不动，它是屏幕上唯一
 * 静止的动态元素，读起来像"贴上去的噪点"而不是"星"。每颗星独立相位之后，
 * 一小片星点会呈现流动的明暗，这才是"星云"感的来源。
 *
 * ⚠ 幅度上限由**加法混合**约束：屏幕贡献 ≈ 背景 + color × opacity × twinkle，
 * 而 `opacity` 的端点已经到 1.0（`starSpec(1)`）。所以 `1 + 幅度` 一旦越过 1，
 * 峰值就被 framebuffer 削平成纯白 —— 星多的地方会"整片闪到发白"。
 * 0.22 下峰值最高 1.20（97% 恢复那几块），亮核略偏白但不糊。
 *
 * ⚠ 摆动**围绕 1 对称**，不是"只向下"：对称时时间平均恰为 1，于是
 * `prefersReducedMotion` 的用户（幅度归 0）看到的亮度，与动态用户的时间平均一致。
 * 改成只向下调制的话，这两类人的绝对亮度会差 幅度/2 —— 那种不一致很难查。
 */
export const TWINKLE_AMPLITUDE = 0.22

/**
 * 闪烁角速度（弧度/秒）→ 周期 2π/2.6 ≈ 2.4 s。
 *
 * 比心脏呼吸（3400 ms）快一档：两者周期接近会读成"整屏同频闪"，很怪。
 * 每颗星还会各自乘一个 0.7~1.3 的系数（见 star-field.ts），避免整片同步。
 */
export const TWINKLE_SPEED = 2.6

/**
 * 每颗星**尺寸系数**的分布：`FLOOR + SPAN · u²`（u 为均匀随机数）→ 范围 [0.5, 2.0]。
 *
 * 用平方而**不是均匀分布**：均匀分布得到一片大小雷同的点，读起来仍像"沙粒"；
 * 真实星场是**少数大星 + 大量小星**。平方让 u 偏小的一侧堆积，正好是那个形状。
 *
 * ⚠ 这个分布必须**均值恰为 1**（u 均匀 ⟹ E[u²] = 1/3 ⟹ 0.5 + 1.5/3 = 1.0）。
 * 均值一旦不为 1，它挪动的是 `size` 通道的**绝对量级**，那就不只是"个体差异"了。
 */
export const SIZE_VAR_FLOOR = 0.5
export const SIZE_VAR_SPAN = 1.5
