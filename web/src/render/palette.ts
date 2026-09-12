// 恢复度 → 材质参数（纯函数，零 three.js 依赖）。
//
// 语义（spec §5）：基色固定中性、不参与语义；语义走自发光。
// 以 0.5 为零线沿 蓝↔琥珀 轴发散——这条轴与红绿轴不重叠，红绿色盲（最常见色觉障碍）仍可区分。
// 亮度**字段**（lightness）跨整条轴单调不减（32 → 50 → 64），是叠在色相上的冗余通道。
// 注意：实际渲染亮度 = 受光基色 + emissive×emissiveIntensity，后者随 |t| 增长、
// **在零线归零**，故整条轴上的渲染亮度并非单调（1.0 > 0.0 > 0.5）。
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

  const t = clamp((recovery - 0.5) / 0.5, -1, 1)
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
