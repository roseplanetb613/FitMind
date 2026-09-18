/**
 * 曲库管理面板（主界面「音乐」入口）。
 *
 * 数据全注入（`MusicLibrary`），本文件只做：导入按钮 change 转发、列表渲染
 * （名/组徽标/时长/大小）、徽标点击改组、删除、清空。时长/大小格式化在
 * 这里（纯展示），不改底层数据。
 */
import type { MusicGroup, MusicLibrary, StoredSong } from '../data/music-library'

export interface MusicLibraryPanelDeps {
  root: HTMLElement
  library: MusicLibrary
  onClose?: () => void
  /** 清空确认（测试注入 `() => true`；生产默认 `window.confirm`） */
  confirm?: (msg: string) => boolean
}

export interface MusicLibraryPanel {
  open(): Promise<void>
  close(): void
}

export function formatDuration(sec: number | null): string {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${Math.round(n / 1024)} KB`
}

export function createMusicLibraryPanel(deps: MusicLibraryPanelDeps): MusicLibraryPanel {
  const root = deps.root
  const confirm = deps.confirm ?? ((msg: string) => window.confirm?.(msg) ?? false)

  function el<K extends keyof HTMLElementTagNameMap>(
    tag: K, cls: string, text?: string,
  ): HTMLElementTagNameMap[K] {
    const n = document.createElement(tag)
    n.className = cls
    if (text !== undefined) n.textContent = text
    return n
  }

  function clear(): void {
    root.innerHTML = ''
    const kids = (root as unknown as { children?: unknown[] }).children
    if (Array.isArray(kids)) kids.length = 0
  }

  function makeImportInput(): HTMLInputElement {
    const imp = el('input', 'music-import')
    imp.setAttribute('type', 'file')
    imp.setAttribute('accept', 'audio/*')
    imp.setAttribute('multiple', '')
    imp.addEventListener('change', () => {
      const files = (imp as unknown as { files: FileList | null }).files
      if (files?.length) void deps.library.importFiles(Array.from(files)).then(() => render())
    })
    return imp
  }

  function makeImportZone(labelText: string): HTMLElement {
    const zone = el('div', 'music-import-zone')
    const imp = makeImportInput()
    const label = el('span', 'music-import-label', labelText)
    label.addEventListener('click', () => imp.click())
    zone.append(imp, label)
    return zone
  }

  function row(node: StoredSong): HTMLElement {
    const r = el('div', 'music-row')
    const badge = el('button', 'music-badge-toggle', node.group === 'work' ? '练时' : '间歇')
    badge.addEventListener('click', () => {
      const next: MusicGroup = node.group === 'work' ? 'rest' : 'work'
      void deps.library.setGroup(node.id, next).then(() => render())
    })
    const nameEl = el('span', 'music-name', node.name)
    const dur = el('span', 'music-dur', formatDuration(node.durationSec))
    const size = el('span', 'music-size', fmtSize(node.size))
    const del = el('button', 'music-del', '删除')
    del.addEventListener('click', () => { void deps.library.remove(node.id).then(() => render()) })
    r.append(badge, nameEl, dur, size, del)
    return r
  }

  let renderSeq = 0

  async function render(): Promise<void> {
    const seq = ++renderSeq
    clear()
    const list = await deps.library.list()
    if (seq !== renderSeq) return        // 有更新的渲染接管，丢弃这次（防止并发互叠）

    const head = el('div', 'music-head')
    const title = el('span', 'music-title', `曲库（${list.length}）`)
    const closeBtn = el('button', 'music-close', '关闭')
    closeBtn.addEventListener('click', () => deps.onClose?.())
    head.append(title, closeBtn)
    root.appendChild(head)

    if (!list.length) {
      root.appendChild(el('p', 'music-empty-hint', '还没有歌曲 —— 先导入几首吧。'))
      root.appendChild(makeImportZone('导入音乐'))
      return
    }

    root.appendChild(makeImportZone('＋ 导入音乐'))
    for (const s of list) root.appendChild(row(s))

    const foot = el('div', 'music-foot')
    const clearBtn = el('button', 'music-clear', '清空全部')
    clearBtn.addEventListener('click', () => {
      if (confirm('要清空整个曲库吗？')) void deps.library.clear().then(() => render())
    })
    foot.appendChild(clearBtn)
    root.appendChild(foot)
  }

  return {
    open: async () => {
      // 面板根带 `.plan-modal`（styles.css 里默认 display:none，`is-open` 才显示）。
      // open/close 必须自己管显隐 —— 否则内容渲染了、层却永远藏在地底下。
      root.classList.add('is-open')
      await render()
    },
    close: () => {
      root.classList.remove('is-open')
      renderSeq += 1
      clear()
    },
  }
}