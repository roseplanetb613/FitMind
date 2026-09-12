import type { MuscleState } from '../data/types'

/**
 * 详情浮层的行数据（纯函数，可单测）。
 * 字段边界见 spec §6.3：**只用 API 提供的字段**，没有"练过哪些动作"这个数据面，不臆造。
 *
 * 「恢复度」那一行**必须**直接用 `state.describe`（后端用 `recovery.describe()` 算好的），
 * 绝不在这里自己拼百分比——措辞单源（spec §5.3）。
 */
export function detailRows(state: MuscleState): Array<[string, string]> {
  return [
    ['恢复度', state.describe],
    ['置信度', state.confidence === 'high' ? '高（有组次数据）' : '低（按次数估算）'],
    ['近期训练次数', String(state.sessions)],
    ['最近一次', new Date(state.last_trained).toLocaleString('zh-CN')],
  ]
}

/** 详情浮层。字段边界见 spec §6.3：只用 API 提供的字段，不臆造动作列表。 */
export function showDetail(
  container: HTMLElement,
  name: string,
  state: MuscleState | null,
): void {
  container.innerHTML = ''
  container.classList.add('is-open')

  const h = document.createElement('h3')
  h.textContent = name
  container.appendChild(h)

  if (!state || !state.has_record) {
    const p = document.createElement('p')
    p.className = 'detail-empty'
    p.textContent = '还没有记录'
    container.appendChild(p)
    return
  }

  const dl = document.createElement('dl')
  for (const [k, v] of detailRows(state)) {
    const dt = document.createElement('dt')
    dt.textContent = k
    const dd = document.createElement('dd')
    dd.textContent = v
    dl.append(dt, dd)
  }
  container.appendChild(dl)
}

export function hideDetail(container: HTMLElement): void {
  container.classList.remove('is-open')
  container.innerHTML = ''
}
