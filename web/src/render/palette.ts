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

const BASE = '#8a8f96' // 中性灰蓝，固定
const AMBER_HUE = 38
const CYAN_HUE = 196

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
