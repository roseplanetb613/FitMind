import type { ExercisePick } from '../data/exercises'
import { mediaUrl } from '../data/exercises'
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

// ────────────────────────────────────────────────────── 动作推荐 ──
// 点一块肌肉 → 看该练什么。数据来自 /v1/muscle-exercises（后端复用
// ExerciseRepo.recommend，本层不做任何推荐逻辑）。

/** 角标文案：主练 or 仅协同。**不臆造"最佳"之类的评价**——数据里只有这个。 */
export function roleLabel(role: 'target' | 'synergist'): string {
  return role === 'target' ? '主练' : '协同'
}

/**
 * 渲染推荐动作列表。
 *
 * **不足 3 条是常态**（`tibialis_anterior` 全库只有 1 个动作、`levator_scapulae` 2 个），所以：
 *   · 有多少画多少，**不补占位**
 *   · `fallback` 为真时明说"已放宽条件" —— 诚实优先于好看
 *   · 加载失败画一行提示，不静默吞掉
 */
export function renderPicks(
  container: HTMLElement,
  picks: ExercisePick[] | null,
  fallback: boolean,
  err?: unknown,
): void {
  const box = document.createElement('div')
  box.className = 'picks'
  container.appendChild(box)

  const h = document.createElement('h4')
  h.textContent = '推荐动作'
  box.appendChild(h)

  if (err) {
    const e = document.createElement('p')
    e.className = 'detail-empty'
    e.textContent = '推荐加载失败'
    box.appendChild(e)
    return
  }
  if (!picks || picks.length === 0) {
    const e = document.createElement('p')
    e.className = 'detail-empty'
    e.textContent = '库里没有这个肌群的动作'
    box.appendChild(e)
    return
  }

  const ul = document.createElement('ul')
  ul.className = 'pick-list'
  for (const p of picks) {
    const li = document.createElement('li')
    li.className = 'pick'

    const gif = mediaUrl(p.gif_url)
    if (gif) {
      const img = document.createElement('img')
      img.src = gif
      img.alt = p.name_zh
      img.loading = 'lazy' // 逐个点肌群，不该一上来就拉一堆 GIF
      li.appendChild(img)
    }

    const meta = document.createElement('div')
    meta.className = 'pick-meta'
    const nm = document.createElement('span')
    nm.className = 'pick-name'
    nm.textContent = p.name_zh
    const tag = document.createElement('span')
    tag.className = `pick-role is-${p.role}`
    tag.textContent = roleLabel(p.role)
    meta.append(nm, tag)
    li.appendChild(meta)

    ul.appendChild(li)
  }
  box.appendChild(ul)

  if (fallback) {
    const n = document.createElement('p')
    n.className = 'legend-note'
    n.textContent = '这个肌群的动作较少，已放宽筛选条件'
    box.appendChild(n)
  }
}
