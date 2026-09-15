import { palette } from '../render/palette'

/**
 * 色阶的 CSS 渐变（纯函数，可单测）：0% → 100% 共 21 个色标，
 * 中间那站是 palette(0.5) 的中性灰——即双色调的零线基准（spec §5）。
 */
export function legendGradient(): string {
  const stops: string[] = []
  for (let i = 0; i <= 20; i++) {
    const r = i / 20
    stops.push(`${palette(r).emissive} ${r * 100}%`)
  }
  return `linear-gradient(to right, ${stops.join(', ')})`
}

/** 色阶图例：显式画出 0.5 零线基准，否则双色调的方向用户读不出来。
 *
 * ⚠ 方向读法（2026-09-15 反转后）：**左端 0% 是暖金、右端 100% 是冷蓝**。
 * 这行文字是**承重的** —— 它写反了，整条色轴就会被反过来读（而这不会有任何报错）。
 * `tests/legend.test.ts` 钉住了它。 */
export function createLegend(container: HTMLElement): HTMLElement {
  const box = document.createElement('div')
  box.className = 'legend'
  box.innerHTML = `
    <div class="legend-title">恢复度</div>
    <div class="legend-bar"></div>
    <div class="legend-axis">
      <span>0%</span><span>50% 基准</span><span>100%</span>
    </div>
    <div class="legend-note">冷 = 恢复好 · 暖 = 需休息</div>
    <div class="legend-note">* 按次数估算（缺组次数据）</div>
    <div class="legend-note">线框 = 无记录，不等于已恢复</div>
  `
  const bar = box.querySelector('.legend-bar') as HTMLElement
  bar.style.background = legendGradient()
  container.appendChild(box)
  return box
}
