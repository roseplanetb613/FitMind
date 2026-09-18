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
  /**
   * 播放**没能起来**的状态翻转回调（`true` = 起不来）。触发源：浏览器按自动播放
   * 策略拒绝 `play()`（`NotAllowedError`）、元素解码失败等。只在**翻转时**调一次。
   * 宿主据此在播放条上如实提示 —— 吞掉它的话，界面上"在放"和"被拦住"长得一模一样。
   */
  setOnBlocked(fn: (blocked: boolean) => void): void
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

  /**
   * 元素 → `MediaElementAudioSourceNode` → `GainNode` → destination（规格 §4.3）。
   *
   * ⚠ 少了中间这两根线，gain 就是个**空转的孤儿**：元素会绕开 Web Audio 直接出声，
   *   于是 `setVolume`、交叉淡化全都作用在一条没人听的支路上 —— 界面上的音量滑块
   *   因此是句谎话（拖到底也照样响）。
   * ⚠ 反过来，元素一旦被 source 接管，输出就**只**走这张图：`ctx` 没 resume
   *   就是静音。所以 `play()` 的失败必须如实上报（`setOnBlocked`），不能 `void` 掉。
   */
  function makeTrack(game: PlayGame): TrackState {
    const el = deps.gameElements[game]
    const gain = deps.makeGain()
    ctx.createMediaElementSource(el).connect(gain)
    gain.connect(ctx.destination)
    return { el, gain, url: null }
  }

  const tracks: Record<PlayGame, TrackState> = { work: makeTrack('work'), rest: makeTrack('rest') }
  let volume = 1
  let paused = false
  let active: PlayGame | null = null
  let onEnded: ((game: PlayGame) => void) | null = null
  let blocked = false
  let onBlocked: ((blocked: boolean) => void) | null = null

  function setBlocked(next: boolean): void {
    if (blocked === next) return
    blocked = next
    onBlocked?.(next)
  }

  /**
   * 起播并要求**知道结果**。`play()` 返回的 promise 是浏览器按自动播放策略
   * 放行/拒绝的唯一回执 —— `void` 掉就等于把"没响"和"在响"抹成一样。
   * 老浏览器可能返回 undefined：无从观察，按不降级处理（不猜）。
   */
  function startTrack(t: TrackState): void {
    const p: Promise<void> | undefined = t.el.play()
    if (typeof p?.then !== 'function') { setBlocked(false); return }
    void p.then(() => setBlocked(false), () => setBlocked(true))
  }

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
      if (t.el.paused) startTrack(t)
    } else {
      if (t.url) deps.revokeObjectURL(t.url)
      t.url = url
      t.el.src = url
      startTrack(t)
    }
    const prev = active && active !== game ? active : null
    if (prev) fadeTo(prev, 0)
    fadeTo(game, paused ? 0 : volume)
    active = game
    if (prev) {
      // 淡出侧的 pause 不能立刻做（gain 还在往下走）。定时器被节流只让
      // "静音元素多跑一会儿"（无害），不会让淡入淡出的声音变样。
      // ⚠ 定时器触发时该组可能已被切回重新激活 —— 此时绝不能 pause 它
      //   （否则会把正在播放的组静音掉）。用活动组做守卫即可。
      setTimeout(() => { if (active !== prev) pauseTrack(prev) }, FADE_MS)
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
        else if (tracks[active].el.paused) startTrack(tracks[active])
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
      // 停表把"上一次播放失败"这件事一并了结 —— 不复位的话，下一轮开练前
      // 播放条会一直挂着上一轮留下的降级提示
      setBlocked(false)
    },
    setOnEnded(fn): void { onEnded = fn },
    setOnBlocked(fn): void { onBlocked = fn },
  }
}