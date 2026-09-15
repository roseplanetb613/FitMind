// 恢复度 → 材质参数（纯函数，零 three.js 依赖）。
//
// 语义（spec §5）：基色固定中性、不参与语义；语义走自发光。
// 以 0.5 为零线沿 蓝↔琥珀 轴发散——这条轴与红绿轴不重叠，红绿色盲（最常见色觉障碍）仍可区分。
//
// **轴向（2026-09-15 按用户口径反转）**：恢复度**越高越冷（蓝 196）**、
// **越低越暖（琥珀 38）**。反转前是反的（高=金、低=青），与 2D 视图"蓝 210 = 恢复好"
// 的约定相左 —— 规格 §5.2 里那条"3D 有意分歧"随之取消，两侧口径统一。
//
// 亮度**字段**（lightness）跨整条轴单调**不增**（64 → 50 → 32），是叠在色相上的冗余通道。
// 注意：实际渲染亮度 = 受光基色 + emissive×emissiveIntensity，后者随 |t| 增长、
// **在零线归零**，故整条轴上的渲染亮度并非单调（0.0 > 1.0 > 0.5）。
// 也就是说：黑白截图**不能**用来排序恢复度——这是本设计的已知性质，不是缺陷。
// 对红绿色盲的有效性由蓝↔琥珀轴承担，不依赖亮度单调。
//
// 暴露 hue/saturation/lightness 是为了让 tests/palette.test.ts 能精确断言，
// 而不是去解析 hex 字符串。

export interface PaletteEntry {
  /** 固定中性基色，不参与语义 */
  baseColor: string
  emissive: string
  emissiveIntensity: number
  opacity: number
  style: 'solid' | 'wireframe'
  /** 未知态（style === 'wireframe'）时下面三个轴字段无意义——
   *  消费方必须检查 style 或 hue === null，不得直接读 lightness 做色阶。 */
  hue: number | null
  saturation: number
  lightness: number
}

const AMBER_HUE = 38
const CYAN_HUE = 196

/** 中性灰蓝，固定。**导出是给悬停高亮用的**：palette 的三个分支都返回它，
 *  所以"还原"就是把它写回去，不需要为悬停保存任何快照。 */
export const BASE_COLOR = '#8a8f96'
const BASE = BASE_COLOR

/** 悬停高亮的基色（实测 HSL ≈ 212°, 51%, 94%）。挑它的理由：
 *  - 比 BASE（≈ 215°, 5%, 57%）亮得多，是同一色相上的一次"提亮"，不引入新语义；
 *  - 与两支语义色都可区分：它们是明度 32%（冷 / CYAN 196°）～64%（暖 / AMBER 38°）
 *    的高饱和彩度色，色相也都不在 212°；
 *  - 在**未知态**上也看得见——高亮走基色这一路，不走自发光，因为未知态
 *    的 emissiveIntensity 恒为 0（palette(null)），"乘个系数"高亮不了线框块；
 *    线框块画的边线取自同一份材质，基色变亮它就变亮。 */
export const HOVER_COLOR = '#e6eef7'

/**
 * 非骨骼肌块（心脏）的固定色。**不参与恢复度色轴** —— 后端对 `cardio_system`
 * 给的是 `null`（没有"心脏恢复度"这种东西），走 palette(null) 会得到线框 + 极低
 * 不透明度，实测根本看不见（用户报过）。
 *
 * 放在 palette.ts 而不是 scene.ts：它就是一个颜色，这是颜色的家；而且
 * `glow.ts`（心脏辉光）也要用它，放在 scene.ts 会形成 scene ⇄ glow 的循环 import。
 *
 * ⚠ 取舍：红偏离了原设计"避开红绿"的色觉友好原则。心脏与恢复度色轴（蓝↔金）
 * 色相差足够大，加上标签与位置两条冗余线索，故接受 —— 这是看过实际效果后
 * 明确要求的。
 */
export const NON_MUSCLE_COLOR = '#d93a2b'
/** 心脏的自发光：暗红，让它在半透明肌肉后面透出来。 */
export const NON_MUSCLE_EMISSIVE = '#6e1008'

/**
 * 心脏**辉光**的颜色。**刻意比基色亮**，不是 `NON_MUSCLE_COLOR`。
 *
 * 辉光是加色混合的（`AdditiveBlending`）：屏幕值 ≈ 背景 + 颜色 × 不透明度。
 * 用基色那颗中调红（`#d93a2b`，明度约 51%）加出来是一片**暗而浑**的红，
 * 亮部根本提不上去 —— 实测表现就是"加了辉光但看着像心脏边缘脏了一块"，
 * 而不是"心脏在发光"。发光体要的是**亮核**，所以取一个更亮更饱和的红。
 *
 * ⚠ 上限别越过去：再亮就会往粉/白走，那时它既不再读作"红心"，也会和恢复度色轴
 * 的冷端（青）在低饱和区靠近。`#ff5340` 的色相仍在 8° 附近，与基色同族。
 */
export const HEART_GLOW_COLOR = '#ff5340'

/**
 * 星点（恢复度的第二条编码通道）的颜色。
 *
 * ⚠ **色轴反转（2026-09-15）之后，这条色与色轴的"恢复好"端不再不同族。**
 * 它 ≈ HSL(206°, 100%, 88%)，而冷端是 `CYAN_HUE` 196° —— 只差 10°，就是同一族。
 * 反转前"恢复好"在暖金那侧，两者确实分得开；原先那句"与暖金/冷青都不同族"是按
 * 旧轴向写的，**已经不成立**，别再照抄。
 *
 * 之所以仍然保留这个色：语义方向是**一致**的（星多 = 恢复好 = 偏冷），不构成误导；
 * 区分靠**明度**撑（星点 88% vs 青端 32%），实测在青蓝的肌肉上星点仍然跳得出来。
 * 若将来要拉开，往**中性白**推（降饱和）比换色相稳 —— 换色相容易撞上另一端的琥珀。
 */
export const STAR_COLOR = '#bfe3ff'

function hslToHex(h: number, s: number, l: number): string {
  const sn = s / 100
  const ln = l / 100
  const a = sn * Math.min(ln, 1 - ln)
  const f = (n: number): string => {
    const k = (n + h / 30) % 12
    const c = ln - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
    return Math.round(255 * c).toString(16).padStart(2, '0')
  }
  return `#${f(0)}${f(8)}${f(4)}`
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x))
}

export function palette(recovery: number | null): PaletteEntry {
  if (recovery === null || !Number.isFinite(recovery)) {
    return {
      baseColor: BASE,
      emissive: BASE,
      emissiveIntensity: 0,
      opacity: 0.35,
      style: 'wireframe',
      hue: null,
      saturation: 0,
      lightness: 50,
    }
  }

  // **反转就在这一个符号上。** 下游的 warm/hue/sat/light 分支全都按 |t| 与 t 的符号
  // 作用，所以 `(0.5 - recovery)` 等价于 `palette(r) === 反转前的 palette(1 - r)`：
  // 两种颜色的**外观**（琥珀明度 64 / 青明度 32）与色相区间（CVD 守护断言依赖它们）
  // 一个字节都不动，翻转的只有语义。要再翻回去，把这行改回 `(recovery - 0.5)`。
  const t = clamp((0.5 - recovery) / 0.5, -1, 1)
  if (t === 0) {
    return {
      baseColor: BASE,
      emissive: BASE,
      emissiveIntensity: 0,
      opacity: 1,
      style: 'solid',
      hue: null,
      saturation: 0,
      lightness: 50,
    }
  }

  const warm = t > 0
  const hue = warm ? AMBER_HUE : CYAN_HUE
  const saturation = warm ? 90 * t : 70 * Math.abs(t)
  const lightness = warm ? 50 + 14 * t : 50 - 18 * Math.abs(t)

  return {
    baseColor: BASE,
    emissive: hslToHex(hue, saturation, lightness),
    emissiveIntensity: Math.abs(t),
    opacity: 1,
    style: 'solid',
    hue,
    saturation,
    lightness,
  }
}
