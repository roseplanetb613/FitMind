/**
 * 跟练全屏里的迷你播放条。**只渲染与转发**：
 * · 状态来自注入的 `getState()`（宿主拼装：调度器 state + 暂停位）。
 * · 动作转发给 `actions`（宿主绑定到调度器/引擎）。
 * · 本文件不 import 调度器与引擎（依赖注入纪律，不许长面条）。
 */
import type { PlayGame } from '../media/audio-engine'

export interface PlayerView {
  phase: 'off' | 'work' | 'rest'
  songName: string | null
  group: PlayGame
  hasSongs: boolean
  paused: boolean
  /** 最近一次播放没能起来（浏览器拦截自动播放 / 解码失败）。见 `AudioEngine.setOnBlocked` */
  blocked: boolean
}

export interface PlayerActions {
  togglePause(): void
  next(): void
  setVolume(v: number): void
  onOpenLibrary(): void
  /** 重试起播。这一下点击本身就是用户手势 —— 被拦截时它是唯一能解开的钥匙 */
  retry(): void
}

export interface WorkoutPlayerDeps {
  root: HTMLElement
  actions: PlayerActions
  getState: () => PlayerView
}

export interface WorkoutPlayer {
  /** 宿主在事件/状态变化后调用，重绘整条 */
  refresh(): void
}

const GROUP_LABEL: Record<PlayGame, string> = { work: '练时', rest: '间歇' }

export function createWorkoutPlayer(deps: WorkoutPlayerDeps): WorkoutPlayer {
  const root = deps.root
  let vol = 1

  function el<K extends keyof HTMLElementTagNameMap>(
    tag: K, cls: string, text?: string,
  ): HTMLElementTagNameMap[K] {
    const n = document.createElement(tag)
    n.className = cls
    if (text !== undefined) n.textContent = text
    return n
  }

  function clear(node: HTMLElement): void {
    node.innerHTML = ''
    // dom-stub 的 `children` 是数组（真 DOM 里是 live HTMLCollection）—— 置空即可。
    const kids = (node as unknown as { children?: unknown[] }).children
    if (Array.isArray(kids)) kids.length = 0
  }

  function refresh(): void {
    const v = deps.getState()
    clear(root)

    if (!v.hasSongs) {
      const empty = el('button', 'music-empty', '去添加音乐 →')
      empty.addEventListener('click', () => deps.actions.onOpenLibrary())
      root.appendChild(empty)
      return
    }
    if (v.blocked) {
      // 规格 §4.3：如实提示，不打断跟练（训练照走，只是没声）。
      // 给一个重试按钮而不是让用户去猜：被自动播放策略拦下时，一次真实点击
      // 就是解锁的那一下 —— 光提示不给动作，用户只能刷新页面。
      root.appendChild(el('span', 'music-idle', '本页无法播放音乐'))
      const retry = el('button', 'music-retry', '重试')
      retry.addEventListener('click', () => deps.actions.retry())
      root.appendChild(retry)
      return
    }
    if (v.phase === 'off') {
      root.appendChild(el('span', 'music-idle', '音乐未开始（进入训练自动播放）'))
      return
    }

    const toggle = el('button', 'music-toggle', v.paused ? '播放' : '暂停')
    toggle.addEventListener('click', () => deps.actions.togglePause())

    const next = el('button', 'music-next', '下一首')
    next.addEventListener('click', () => deps.actions.next())

    const badge = el('span', 'music-badge', GROUP_LABEL[v.group])
    const track = el('span', 'music-track', `${GROUP_LABEL[v.group]} · ${v.songName ?? '…'}`)

    const volRange = el('input', 'music-volume')
    volRange.setAttribute('type', 'range')
    volRange.setAttribute('min', '0')
    volRange.setAttribute('max', '1')
    volRange.setAttribute('step', '0.05')
    volRange.value = String(vol)
    volRange.addEventListener('input', () => {
      vol = Number((volRange as unknown as { value: string }).value)
      deps.actions.setVolume(vol)
    })

    root.append(toggle, next, badge, track, volRange)
  }

  // 构造时先渲染一次；此后由宿主在状态变化后调 refresh
  refresh()

  return { refresh }
}