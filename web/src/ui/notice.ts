/**
 * 加载失败提示。**幂等**：失败时复用同一个元素（只更新文本），成功时移除它。
 *
 * 必要性：refresh 由 `setInterval(..., 60_000)` 驱动。若每次失败都往容器里追加一条，
 * 后端持续不可用一小时就是 60 条同样的红字，且恢复后永不撤除——非幂等的错误渲染
 * 配上轮询 = DOM 泄漏 + 视觉噪声。
 *
 * 用闭包持有元素引用、而不是每次 `querySelector` 找它：本仓的 DOM stub 里
 * `querySelector` 永远返回同一个非空元素（不解析 HTML），靠它判"在不在"会把
 * 首次创建也当成"已存在"。闭包引用是真实 DOM 与 stub 都成立的那个判据。
 */
export interface LoadErrorNotice {
  /** 展示/更新提示（同一个元素，不追加） */
  show(message: string): void
  /** 撤下提示；没有提示时是空操作 */
  clear(): void
}

export function createLoadErrorNotice(container: HTMLElement): LoadErrorNotice {
  let el: HTMLElement | null = null
  return {
    show(message): void {
      if (!el) {
        el = document.createElement('div')
        el.className = 'legend-note legend-error'
        container.appendChild(el)
      }
      el.textContent = message
    },
    clear(): void {
      if (!el) return
      el.remove()
      el = null // 置空才会在下次失败时重新创建；否则会复用已摘下的元素
    },
  }
}
