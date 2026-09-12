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
