/**
 * 播放引擎：Web Audio 增益控制（规格 §4.3）。
 *
 * ## 结构
 *
 * 常驻一个 `AudioContext`，练时/间歇各一个 `<audio>` 元素 + `GainNode`。
 * 交叉淡化用 `setTargetAtTime` automation —— 音频线程驱动，**不依赖 JS 定时器**
 * （手机节流定时器会让手动阶梯调音量卡顿，automation 不会）。
 *
 * ## 簿记规则（node 可测的部分）
 *
 * · `play(game, url)`：该组已载同 URL 且暂停 → `play()` 续播（位置冻结自上次淡出）；
 *   否则换源（revoke 旧 objectURL）从头播。
 * · 换组：另一组 gain 淡到 0，`FADE_MS` 后 `pause()`（冻结位置，供"切回续播"）。
 * · 曲目自然播完：元素 `ended` → `setOnEnded` 上抛，调度器决定下一首。
 * · `stopAll()`：两组全停 + 全 revoke + src 清空 + gain 归零。
 *
 * ⚠ 生产需要用户手势：`AudioContext` 在跟练入口点击时 `resume()`（Task 7）。
 */
export type PlayGame = 'work' | 'rest'

export interface AudioEngine {
  play(game: PlayGame, url: string): void
  setPaused(paused: boolean): void
  setVolume(v: number): void
  stopAll(): void
  setOnEnded(fn: (game: PlayGame) => void): void
}

/** 交叉淡化时长（毫秒）。淡出的 `pause` 用同一个数做延迟。 */
export const FADE_MS = 1000
/** automation 时间常数：目标衰减到 ~37% 时长（1/4 淡化时长）。 */
const TC = FADE_MS / 1000 / 4

export interface AudioEngineDeps {
  ctx: AudioContext
  /** 每组一个元素。宿主注入（生产是 `<audio>`；测试是假元素）。 */
  gameElements: Record<PlayGame, HTMLAudioElement>
  makeGain: () => GainNode
  createObjectURL: (b: Blob) => string
  revokeObjectURL: (u: string) => void
}

interface TrackState {
  el: HTMLAudioElement
  gain: GainNode
  url: string | null
}

export function createAudioEngine(deps: AudioEngineDeps): AudioEngine {
  const { ctx } = deps
  const tracks: Record<PlayGame, TrackState> = {
    work: { el: deps.gameElements.work, gain: deps.makeGain(), url: null },
    rest: { el: deps.gameElements.rest, gain: deps.makeGain(), url: null },
  }
  let volume = 1
  let paused = false
  let active: PlayGame | null = null
  let onEnded: ((game: PlayGame) => void) | null = null

  function fadeTo(game: PlayGame, target: number): void {
    // 真正的 automation 挂在 GainNode 的 `.gain`（AudioParam）上 —— 直接给
    // `setTargetAtTime`，而不是拿 `GainNode` 冒充（那在真浏览器里会炸）。
    tracks[game].gain.gain.setTargetAtTime(target, ctx.currentTime, TC)
  }

  function pauseTrack(game: PlayGame): void {
    const t = tracks[game]
    if (!t.el.paused) t.el.pause()
  }

  function play(game: PlayGame, url: string): void {
    const t = tracks[game]
    if (t.url === url) {
      // 同源续播：位置被淡出的 pause 冻结，直接恢复
      if (t.el.paused) void t.el.play()
    } else {
      if (t.url) deps.revokeObjectURL(t.url)
      t.url = url
      t.el.src = url
      void t.el.play()
    }
    const prev = active && active !== game ? active : null
    if (prev) fadeTo(prev, 0)
    fadeTo(game, paused ? 0 : volume)
    active = game
    if (prev) {
      // 淡出侧的 pause 不能立刻做（gain 还在往下走）。定时器被节流只让
      // "静音元素多跑一会儿"（无害），不会让淡入淡出的声音变样。
      setTimeout(() => pauseTrack(prev), FADE_MS)
    }
  }

  for (const game of ['work', 'rest'] as PlayGame[]) {
    tracks[game].el.addEventListener('ended', () => {
      if (active === game) onEnded?.(game)
    })
  }

  return {
    play,
    setPaused(p): void {
      paused = p
      if (active) {
        if (p) tracks[active].el.pause()
        else if (tracks[active].el.paused) void tracks[active].el.play()
      }
    },
    setVolume(v): void {
      volume = Math.min(1, Math.max(0, v))
      if (active) fadeTo(active, paused ? 0 : volume)
    },
    stopAll(): void {
      for (const game of ['work', 'rest'] as PlayGame[]) {
        const t = tracks[game]
        t.el.pause()
        if (t.url) { deps.revokeObjectURL(t.url); t.url = null }
        t.el.src = ''
        fadeTo(game, 0)
      }
      active = null
      paused = false
    },
    setOnEnded(fn): void { onEnded = fn },
  }
}