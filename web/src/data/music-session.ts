/**
 * 切歌调度器 —— 音乐怎么跟着训练走（纯逻辑，node 可测）。
 *
 * ## 决策表（规格 §4.4）
 *
 * | 事件 | 决策 |
 * |---|---|
 * | `session.start` | 练时组第 0 首起播；两套游标归 0 |
 * | `rest.start` | 间歇组当前曲**从头播**（间歇短，每次从同一首开始） |
 * | `rest.end` | 切回练时组当前曲 —— 引擎对同 URL 续播，位置是淡出时冻结的 |
 * | `session.end` | 全停、相位归 off、游标归 0 |
 * | 活动组 `ended` / 用户点「下一首」 | 该组游标 +1（循环），从新曲播 |
 * | 间歇组为空 | `rest.start`/`rest.end` 都**不切**（继续练时组，不猜、不静音） |
 * | 练时组为空 | 什么都不播（相位 off，UI 提示去建曲库） |
 *
 * ⚠ 只挂事件，不引音乐代码进 `workout-session.ts`；本文件也不 import 引擎实现，
 * 只按 `AudioEngine` 接口调用（依赖倒置，测试喂 spy）。
 */
import type { SessionEvent } from './workout-session'
import type { AudioEngine, PlayGame } from '../media/audio-engine'

export type MusicPhase = 'off' | 'work' | 'rest'

export interface MusicTrack {
  id: string
  name: string
  /** 引擎播放用的 URL（宿主把曲库 blob 换好再喂进来） */
  url: string
}

export interface MusicSessionState {
  phase: MusicPhase
  hasWork: boolean
  hasRest: boolean
  /** 当前**活动相位**那首歌（off 或对应组为空时为 null） */
  current: { name: string; group: PlayGame } | null
}

export interface MusicSessionDeps {
  engine: AudioEngine
  work: MusicTrack[]
  rest: MusicTrack[]
}

export interface MusicSession {
  /** 宿主把状态机事件喂进来（main.ts 里 flow 的 onEvent） */
  onEvent(ev: SessionEvent): void
  /** 引擎曲目自然播完 → 组内下一首 */
  onTrackEnded(game: PlayGame): void
  /** 用户点下一首：活动组内游标 +1 */
  userNext(): void
  /** 一轮结束的兜底停（幂等；session.end 已停时 no-op） */
  stop(): void
  state(): MusicSessionState
}

export function createMusicSession(deps: MusicSessionDeps): MusicSession {
  const work = deps.work
  const rest = deps.rest
  let phase: MusicPhase = 'off'
  let workIdx = 0
  let restIdx = 0

  function nextIn(list: MusicTrack[], idx: number): number {
    return (idx + 1) % list.length
  }

  return {
    onEvent(ev): void {
      switch (ev.type) {
        case 'session.start':
          phase = 'off'
          workIdx = 0
          restIdx = 0
          if (work.length) {
            phase = 'work'
            deps.engine.play('work', work[0].url)
          }
          break
        case 'rest.start':
          // 间歇组为空 → 不切（练时组继续）；练时组为空 → 全程 off，什么都不播
          if (!rest.length || !work.length) break
          phase = 'rest'
          deps.engine.play('rest', rest[restIdx].url)
          break
        case 'rest.end':
          // 同上：两组齐全才切回练时组
          if (!rest.length || !work.length) break
          phase = 'work'
          deps.engine.play('work', work[workIdx].url)
          break
        case 'session.end':
          deps.engine.stopAll()
          phase = 'off'
          workIdx = 0
          restIdx = 0
          break
        default:
          break
      }
    },

    onTrackEnded(game): void {
      if (phase === 'off' || game !== phase) return
      if (game === 'work') {
        workIdx = nextIn(work, workIdx)
        deps.engine.play('work', work[workIdx].url)
      } else {
        restIdx = nextIn(rest, restIdx)
        deps.engine.play('rest', rest[restIdx].url)
      }
    },

    userNext(): void {
      if (phase === 'work') {
        workIdx = nextIn(work, workIdx)
        deps.engine.play('work', work[workIdx].url)
      } else if (phase === 'rest') {
        restIdx = nextIn(rest, restIdx)
        deps.engine.play('rest', rest[restIdx].url)
      }
    },

    stop(): void {
      if (phase === 'off') return
      deps.engine.stopAll()
      phase = 'off'
    },

    state(): MusicSessionState {
      let current: MusicSessionState['current'] = null
      if (phase === 'work' && work[workIdx]) {
        current = { name: work[workIdx].name, group: 'work' }
      } else if (phase === 'rest' && rest[restIdx]) {
        current = { name: rest[restIdx].name, group: 'rest' }
      }
      return { phase, hasWork: work.length > 0, hasRest: rest.length > 0, current }
    },
  }
}