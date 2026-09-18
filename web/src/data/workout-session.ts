/**
 * 训练执行台的状态机。**纯逻辑**：不碰 DOM、不碰 WebGL、不碰网络、不读全局时钟。
 *
 * ## 为什么单拎一个文件
 *
 * 本仓已经付过两次学费：`detail-flow.ts` 的模块注释里记着 —— 那段逻辑原先写在
 * `main.ts` 里，而 `main.ts` 依赖 DOM + WebGL，**在 node 里跑不了**，于是它没有任何
 * 测试，代价是先后两次"tsc 过 + 全量测试过"却功能全废的静默失效。
 * 训练执行台的状态比那还多（两条游标 × 五个状态），放 `main.ts` 里同样测不到。
 *
 * ## 五个状态，两条轴
 *
 * ```
 * idle ──start()──> working ──完成一组──> resting ──归零/skipRest──> working
 *                      │                                            （还有组）
 *                      └─ 该动作组走完 → 写回 → exIdx+1 → working / finished
 * ```
 *
 * `exIdx` 是动作游标，`setIdx` 是**已完成的组数**（0-based），于是：
 *   · 界面上的"第 N 组" = `setIdx + 1`
 *   · `setIdx >= ex.sets` ⟺ 该动作做完了
 *
 * ## ⚠ 倒计时用注入的时钟，不是自增计数器
 *
 * `setInterval` 自增（每 1000ms 减 1）在手机上**必然不准**：切后台/锁屏时浏览器会
 * 节流甚至冻结定时器，回来时那个计数器还是进去前的值。所以这里记录的是
 * `restEndsAt`（一个**绝对**时间戳），剩余秒数每次现算 —— 节流多久都不影响结果。
 * 副作用是它天然支持跨刷新恢复（`restEndsAt` 可以直接序列化，见 `data/workout-store.ts`）。
 *
 * ## ⚠ 归零是"惰性迁移"，不依赖定时器频率
 *
 * `resting → working` 的迁移发生在**任何一个读操作**里（`state()` / `restLeft()`），
 * 而不是靠"倒计时 tick 到 0 那一刻"触发。这样界面多久刷新一次都不影响正确性，
 * 也不会出现"定时器被节流了于是状态卡在 resting"。
 */
import type { SessionExercise } from './plan-parse'

export type SessionPhase = 'idle' | 'working' | 'resting' | 'finished'

/**
 * 阶段事件。**这是给后续"动感音乐"模块的接口**（规格 §7）：本文件只产出事件，
 * 不引入任何音乐相关代码 —— 音乐模块挂上来时不该需要改这里一行。
 */
export type SessionEventType =
  | 'session.start'
  | 'session.end'
  | 'exercise.start'
  | 'set.start'
  | 'set.end'
  | 'rest.start'
  | 'rest.end'

export interface SessionEvent {
  type: SessionEventType
  /** 事件发生时的动作游标 */
  exIdx: number
  /** 事件发生时的**已完成组数**（0-based） */
  setIdx: number
  /** 注入时钟的读数（毫秒） */
  at: number
}

export interface WorkoutSessionState {
  phase: SessionPhase
  exIdx: number
  /** 已完成的组数（0-based）。界面显示"第 N 组"用 `setIdx + 1` */
  setIdx: number
  /** 当前动作的总组数（`phase` 为 idle/finished 时是 0） */
  sets: number
  /** 计划里的动作总数 */
  total: number
  /** 已写回的动作数 */
  done: number
  /** 间歇剩余秒数（`phase !== 'resting'` 时恒为 0） */
  restLeft: number
  /**
   * 间歇的**绝对**结束时刻（毫秒，与 `now()` 同基准）；`phase !== 'resting'` 时为 null。
   *
   * 暴露它是为了**持久化**：把这个数存下来，刷新/切后台回来后剩余时间是准的 ——
   * 存"还剩几秒"就会随恢复时刻漂（`data/workout-store.ts` 的 `restEndsAt`）。
   */
  restEndsAt: number | null
}

/** 从 `workout-store` 读回来的进度（`state === 'working' | 'resting'`）。 */
export interface RestoreSnapshot {
  exIdx: number
  setIdx: number
  state: string
  /** 间歇的绝对结束时刻；缺省时按"重新起算"处理（宁可多歇，不少歇） */
  restEndsAt?: number
}

export interface WorkoutSessionDeps {
  /**
   * 注入的时钟（毫秒）。生产传 `Date.now`，测试喂假时间。
   * ⚠ **必须注入**：`Date.now` 在 node 里也存在，直接读的实现会安静地通过测试，
   * 测的却是跑测试那一刻（同 `render/perf.ts` 的"线索注入"先例）。
   */
  now: () => number
  /**
   * 一个动作的组走完（或提前结束/跳过时已做了若干组）→ 调用方写回 + 落盘。
   * ⚠ **只在 `doneSets > 0` 时触发**：一组都没做就不该留下记录。
   */
  onExerciseDone?: (ex: SessionExercise, index: number, doneSets: number) => void
  onEvent?: (ev: SessionEvent) => void
}

export interface WorkoutSession {
  /** 开始一轮。空列表 → 直接 `finished`（调用方负责提前拦：无计划不该进来） */
  start: (exercises: SessionExercise[]) => void
  /** 从持久化进度接着做（`fits` 判定由调用方做，详见 `workout-store.ts`） */
  resume: (exercises: SessionExercise[], snap: RestoreSnapshot) => void
  /** 完成一组（`working` 时有效）。组还有剩 → 进间歇或下一组；组走完 → 写回 + 下一动作 */
  completeSet: () => void
  /** 跳过间歇，立刻进下一组 */
  skipRest: () => void
  /** 跳过当前动作。⚠ 已做过的组**照样写回**（那部分是真做了的） */
  skipExercise: () => void
  /** 提前结束整轮。⚠ 当前动作已做过的组照样写回，其余不记 */
  finishEarly: () => void
  /** 当前状态。**读它会顺便推进归零的间歇**（惰性迁移，见模块注释） */
  state: () => WorkoutSessionState
  /** 当前动作；`idle` / `finished` 时为 null */
  current: () => SessionExercise | null
  /** 间歇剩余秒数。同上，读它会推进归零 */
  restLeft: () => number
}

export function createWorkoutSession(deps: WorkoutSessionDeps): WorkoutSession {
  const now = deps.now

  let phase: SessionPhase = 'idle'
  let list: SessionExercise[] = []
  let exIdx = 0
  let setIdx = 0
  let done = 0
  /** 间歇的**绝对**结束时刻（毫秒）。只有 `phase === 'resting'` 时有意义。 */
  let restEndsAt = 0

  function emit(type: SessionEventType): void {
    deps.onEvent?.({ type, exIdx, setIdx, at: now() })
  }

  function finish(): void {
    phase = 'finished'
    emit('session.end')
  }

  /** 开始第 `exIdx` 个动作的第 `setIdx` 组。 */
  function startSet(): void {
    phase = 'working'
    emit('set.start')
  }

  /** 该动作收尾 → 下一个动作 / 结束。`doneSets` 为 0 时不写回。 */
  function closeExercise(doneSets: number): void {
    const ex = list[exIdx]
    if (ex && doneSets > 0) {
      deps.onExerciseDone?.(ex, exIdx, doneSets)
      done += 1
    }
    exIdx += 1
    setIdx = 0
    if (exIdx >= list.length) {
      finish()
      return
    }
    emit('exercise.start')
    startSet()
  }

  function completeSet(): void {
    // ⚠ **写操作也必须先收掉到点的间歇。** 归零是惰性的（见 syncRest），若这里不跑它，
    // "倒计时已经到点、但期间没人读过状态"时这一次 completeSet 会被下面那行当成
    // "不在 working" 静默吞掉 —— 用户点了"完成一组"却毫无反应。
    // 实测踩过：状态机单测里 advance 到点后直接 completeSet，写回记录少了一次。
    syncRest()
    if (phase !== 'working') return
    emit('set.end')
    setIdx += 1
    const ex = list[exIdx]
    if (!ex) {
      finish()
      return
    }
    if (setIdx >= ex.sets) {
      closeExercise(ex.sets)
      return
    }
    // `restSec` 为 0（"几乎不用歇"）或为 null（解析不出）→ 都不猜一个数，直接下一组。
    // ⚠ "0 就跳过间歇"的判定在这里，**不在解析器里**（`plan-parse.ts` 只做字符串→数字）。
    const rest = ex.restSec
    if (rest !== null && rest > 0) {
      restEndsAt = now() + rest * 1000
      phase = 'resting'
      emit('rest.start')
      return
    }
    startSet()
  }

  /** 退出间歇（归零或主动跳过）→ 进下一组。 */
  function leaveRest(): void {
    emit('rest.end')
    startSet()
  }

  /** 惰性推进：间歇已到点则离开 `resting`。任何读操作都先跑它。 */
  function syncRest(): void {
    if (phase === 'resting' && now() >= restEndsAt) leaveRest()
  }

  function computedRestLeft(): number {
    if (phase !== 'resting') return 0
    return Math.max(0, Math.ceil((restEndsAt - now()) / 1000))
  }

  return {
    start(exercises: SessionExercise[]): void {
      // 组数为 0 的动作在界面上是个死循环（永远做不完），直接剔掉
      list = exercises.filter((e) => e.sets > 0)
      exIdx = 0
      setIdx = 0
      done = 0
      restEndsAt = 0
      if (!list.length) {
        // 空列表**不算"练完了"**（不该 emit session.end —— 什么都没开始）。
        // 停在 idle，由调用方负责在进全屏之前就拦住"今天没有训练动作"。
        phase = 'idle'
        return
      }
      emit('session.start')
      emit('exercise.start')
      startSet()
    },

    resume(exercises: SessionExercise[], snap: RestoreSnapshot): void {
      list = exercises.filter((e) => e.sets > 0)
      if (!list.length) {
        phase = 'idle'
        return
      }
      exIdx = Math.min(Math.max(0, snap.exIdx), list.length - 1)
      const sets = list[exIdx].sets
      setIdx = Math.min(Math.max(0, snap.setIdx), sets)
      if (setIdx >= sets) {
        // 存的时候最后一组刚好做完但还没推进 → 补一次收尾（不重做已做完的组）
        closeExercise(sets)
        return
      }
      if (snap.state === 'resting') {
        const rest = list[exIdx].restSec
        // 缺 restEndsAt（版本升级/被人手改过）→ 按**重新起算**处理：
        // 宁可让用户多歇一会儿，也不要因为"猜它已经歇完了"而少歇。
        const ends = snap.restEndsAt ?? (rest && rest > 0 ? now() + rest * 1000 : 0)
        if (ends > now()) {
          restEndsAt = ends
          phase = 'resting'
          emit('exercise.start')
          emit('rest.start')
          return
        }
      }
      emit('exercise.start')
      startSet()
    },

    completeSet,

    skipRest(): void {
      syncRest()
      if (phase !== 'resting') return
      leaveRest()
    },

    skipExercise(): void {
      syncRest()
      if (phase !== 'working' && phase !== 'resting') return
      if (phase === 'resting') emit('rest.end')
      // `setIdx` 正是"已完成的组数"：做了 2/3 组就跳 → 那 2 组写回，第 3 组不记
      closeExercise(setIdx)
    },

    finishEarly(): void {
      syncRest()
      if (phase === 'idle' || phase === 'finished') return
      if (phase === 'resting') emit('rest.end')
      // 已做完的动作**已经**在 closeExercise 里写回了，这里只补当前这个半截的
      const ex = list[exIdx]
      if (ex && setIdx > 0) {
        deps.onExerciseDone?.(ex, exIdx, setIdx)
        done += 1
      }
      finish()
    },

    state(): WorkoutSessionState {
      syncRest()
      return {
        phase,
        exIdx,
        setIdx,
        sets: list[exIdx]?.sets ?? 0,
        total: list.length,
        done,
        restLeft: computedRestLeft(),
        restEndsAt: phase === 'resting' ? restEndsAt : null,
      }
    },

    current(): SessionExercise | null {
      return list[exIdx] ?? null
    },

    restLeft(): number {
      syncRest()
      return computedRestLeft()
    },
  }
}
