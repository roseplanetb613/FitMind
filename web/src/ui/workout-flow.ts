/**
 * 训练执行台：**全屏跟练界面**。
 *
 * ## 一次只显示当前动作
 *
 * 跟练时用户站在器械旁边看手机，屏幕上要回答的是"现在做哪个动作、第几组、歇多久"。
 * 所以它是一次一个动作的卡片，不是计划表的只读视图（那是 `ui/plan.ts`）。
 *
 * ## 它负责什么、不负责什么
 *
 * 负责：取计划 → 定位今天 → 组装 → 渲染 → 把用户操作转给状态机 → 写回 → 落盘 → 收尾。
 * **不负责**：
 *   · **计时** —— 倒计时由注入的时钟现算（状态机）。本文件不碰 `setInterval`；
 *     宿主（`main.ts`）按固定间隔调 `tick()`，而 `tick()` **只重绘**，不推进逻辑。
 *   · **3D 渲染的挂起/恢复** —— 通过 `onOpen` / `onClose` 交给宿主（那里才有
 *     `SceneHandle`）。
 *   · **结束后的那段总结** —— 通过 `onFinished` 交给宿主走 `agent.run`
 *     （规格 §4.8：**不在前端拼措辞**，数字与措辞一律来自后端）。
 *
 * ## 为什么整块抽出来而不是写在 `main.ts` 里
 *
 * `main.ts` 依赖 DOM + WebGL，在 node 里跑不了，于是写在那里的状态等于没有守卫
 * （`detail-flow.ts` 的模块注释记着两次实测教训）。这里依赖全部注入，`dom-stub`
 * 就能跑。
 */
import { fetchPlan } from '../data/plan'
import type { PlanDay } from '../data/plan'
import { buildSessionExercises, pickTodayDay, type SessionExercise } from '../data/plan-parse'
import { createWorkoutSession, type WorkoutSession } from '../data/workout-session'
import {
  clearProgress, exerciseKey, loadProgress, localDateISO, saveProgress,
  STORE_VERSION, type StorageLike, type WorkoutProgress,
} from '../data/workout-store'
import { summarizeSync, writeExercise, type SyncOutcome } from '../data/checkin'

/** 「今天该练哪一天」的取数结果。**读不到计划**与**今天没安排**是两件事。 */
export interface TodayLoad {
  /** 今天要练的那一天；`null` = 今天没得练 */
  day: PlanDay | null
  planId: string
  /** `true` = 图谱不可用读不到计划（可恢复的降级），不是"没有计划" */
  degraded: boolean
  /** 没得练时给用户看的一句话 */
  message: string
}

export interface FinishSummary {
  /** 计划日名（如"推日(胸·肩·三头)"）——宿主用它拼给 Agent 的那句话 */
  day: string
  /** 状态机认为已完成的动作数 */
  done: number
  /** 真正写回图谱成功的动作数 */
  synced: number
  failedNames: string[]
  /** 有失败时给用户看的一句话；全成功为 null */
  notice: string | null
  /**
   * 计划里的动作**全部**走完（`false` = 提前结束，或有动作被跳过）。
   * 宿主用它决定给 Agent 的那句话是"练完了"还是"练了一部分"。
   */
  allDone: boolean
}

export interface WorkoutFlowDeps {
  /** 全屏覆盖层的容器（`#workout`） */
  root: HTMLElement
  userId: string
  /** 取今天该练的那一天。注入以便测试；默认走 `GET /v1/plan` */
  loadToday?: () => Promise<TodayLoad>
  /** 写回一个动作。注入以便测试；默认走 `data/checkin` */
  write?: (ex: SessionExercise, doneSets: number) => Promise<SyncOutcome>
  /** 注入以便测试；生产传 `localStorage`。默认自己取，取不到就是 null（全降级） */
  storage?: StorageLike | null
  now?: () => number
  /** 进入全屏（宿主据此挂起 3D 渲染） */
  onOpen?: () => void
  /** 退出全屏（宿主据此恢复 3D 渲染） */
  onClose?: () => void
  /** 一轮结束（含提前结束）。宿主负责出总结 */
  onFinished?: (summary: FinishSummary) => void
}

export interface WorkoutFlow {
  /** 打开执行台。三种"到不了跟练"的情况都在这里如实显示，不弹空白 */
  open: () => Promise<void>
  /** 关掉（不写总结，比如用户看完"今天没有安排"那条提示） */
  close: () => void
  isOpen: () => boolean
  /** 宿主按固定间隔调它刷新倒计时。**只重绘，不推进逻辑** */
  tick: () => void
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string,
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag)
  if (className) n.className = className
  if (text !== undefined) n.textContent = text
  return n
}

/**
 * 清空子节点。`innerHTML = ''` 对真实 DOM 足够，但 `tests/dom-stub.ts` 不解析 HTML、
 * 它的 `children` 是普通数组不会因 innerHTML 清空 —— 所以只对数组动手
 * （真实 HTMLCollection 的 `Array.isArray` 为 false，不会被误改）。同 `ui/plan.ts`。
 */
function clear(node: HTMLElement): void {
  node.innerHTML = ''
  const kids = (node as unknown as { children?: unknown }).children
  if (Array.isArray(kids)) kids.length = 0
}

/** `localStorage` 取不到（隐私模式 / 老浏览器）→ null，全降级。 */
function safeLocalStorage(): StorageLike | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function createWorkoutFlow(deps: WorkoutFlowDeps): WorkoutFlow {
  const now = deps.now ?? (() => Date.now())
  const storage = deps.storage === undefined ? safeLocalStorage() : deps.storage
  const write = deps.write
    ?? ((ex: SessionExercise, doneSets: number) =>
      writeExercise(ex, doneSets, { userId: deps.userId }))
  const loadToday = deps.loadToday ?? defaultLoadToday

  let open = false
  let today = localDateISO()
  let planId = ''
  let dayLabel = ''
  /** 组装好的动作序列（恢复提示里要用它显示"练到哪个动作"） */
  let built: SessionExercise[] = []
  /** 进全屏前就确定要如实说明的话（跳过/无编号），逐条显示 */
  let notes: string[] = []
  /** 已**成功写回**的动作键。⚠ 只在写回成功后追加（规格 §4.6） */
  let doneKeys: string[] = []
  /** 本轮所有写回结果（含失败），结束时汇总 */
  let outcomes: SyncOutcome[] = []
  /** 飞行中的写回。收尾前必须 await 完，否则汇报会漏掉最后几个 */
  const pending: Promise<void>[] = []
  /** 上一帧渲染的 phase —— `tick` 靠它判断"要不要整屏重绘" */
  let renderedPhase = ''
  /** 倒计时那个数字节点（`tick` 只改它，不重建整屏） */
  let countdownEl: HTMLElement | null = null
  let finishing = false

  const session: WorkoutSession = createWorkoutSession({
    now,
    onExerciseDone: (ex, _index, doneSets) => {
      // ⚠ 状态机是**同步**回调进来、然后立刻推进游标的，所以这里得自己异步收尾：
      // `doneKeys` 只在写回**成功**之后追加，然后与**当前**（已推进的）游标同一次
      // 落盘。顺序反了就会出现"动作标为已完成、但其实没写回"（规格 §4.6）。
      //
      // 反过来的窗口（请求还在飞、用户刷新了）会重做一遍这个动作 —— 那是**有意**
      // 选的方向：宁可多记一次，也不要"游标前进但没写回"（后者是静默丢记录）。
      const task = (async (): Promise<void> => {
        const outcome = await write(ex, doneSets)
        outcomes.push(outcome)
        if (outcome.ok) doneKeys.push(exerciseKey(ex))
        persist()
      })()
      pending.push(task)
    },
  })

  async function defaultLoadToday(): Promise<TodayLoad> {
    const res = await fetchPlan(deps.userId)
    if (res.degraded) {
      return { day: null, planId: '', degraded: true,
               message: '暂时读不到计划（记忆图谱不可用）。恢复后再点一次。' }
    }
    const pick = pickTodayDay(res.plan?.content, today)
    return { day: pick.day, planId: res.plan?.plan_id ?? '',
             degraded: false, message: pick.message }
  }

  // ── 落盘 ──────────────────────────────────────────────────────────
  function persist(): void {
    const st = session.state()
    if (st.phase !== 'working' && st.phase !== 'resting') {
      // `idle` / `finished` **不持久化**（只有"进行中"才值得续）
      clearProgress(storage)
      return
    }
    saveProgress(storage, {
      v: STORE_VERSION, date: today, planId,
      exIdx: st.exIdx, setIdx: st.setIdx, state: st.phase,
      doneIds: [...doneKeys],
      ...(st.restEndsAt !== null ? { restEndsAt: st.restEndsAt } : {}),
    })
  }

  function setOpen(next: boolean): void {
    if (open === next) return
    open = next
    deps.root.classList.toggle('is-open', next)
    if (next) deps.onOpen?.()
    else deps.onClose?.()
  }

  // ── 渲染 ──────────────────────────────────────────────────────────
  function makeCard(label: string): HTMLElement {
    clear(deps.root)
    renderedPhase = ''
    countdownEl = null
    const card = el('div', 'workout-card')
    card.setAttribute('role', 'dialog')
    card.setAttribute('aria-modal', 'true')
    card.setAttribute('aria-label', label)
    return card
  }

  function mount(card: HTMLElement): void {
    deps.root.appendChild(card)
  }

  function button(
    label: string, cls: string, onClick: () => void,
  ): HTMLButtonElement {
    const b = el('button', cls, label)
    b.setAttribute('type', 'button')
    b.addEventListener('click', onClick)
    return b
  }

  function renderMessage(text: string, tone?: 'error'): void {
    const card = makeCard('训练执行台')
    const p = el('p', 'workout-note', text)
    if (tone) p.classList.add(`is-${tone}`)
    card.appendChild(p)
    card.appendChild(button('知道了', 'workout-close', closeFlow))
    mount(card)
  }

  function renderActive(): void {
    const st = session.state()
    const ex = session.current()
    const card = makeCard('训练执行台')
    renderedPhase = st.phase

    const head = el('div', 'workout-head')
    head.appendChild(el('span', 'workout-day', dayLabel))
    head.appendChild(el('span', 'workout-progress',
      `${Math.min(st.exIdx + 1, st.total)}/${st.total} 个动作`))
    card.appendChild(head)

    if (st.phase === 'working' && ex) {
      card.appendChild(el('h3', 'workout-exercise', ex.name))
      const meta = [`第 ${st.setIdx + 1}/${st.sets} 组`]
      if (ex.reps) meta.push(`目标 ${ex.reps} 次`)   // ⚠ 原样，不解析（有时间型"20-60s 保持"）
      if (ex.equipment) meta.push(ex.equipment)
      card.appendChild(el('p', 'workout-meta', meta.join(' · ')))
      const row = el('div', 'workout-actions')
      row.appendChild(button('完成一组', 'workout-primary', () => {
        session.completeSet()
        afterAction()
      }))
      row.appendChild(button('跳过这个动作', 'workout-ghost', () => {
        session.skipExercise()
        afterAction()
      }))
      card.appendChild(row)
    } else if (st.phase === 'resting') {
      card.appendChild(el('h3', 'workout-exercise', '组间歇'))
      countdownEl = el('div', 'workout-countdown', String(st.restLeft))
      card.appendChild(countdownEl)
      if (ex) card.appendChild(el('p', 'workout-meta', `下一组：${ex.name}`))
      const row = el('div', 'workout-actions')
      row.appendChild(button('跳过休息', 'workout-primary', () => {
        session.skipRest()
        afterAction()
      }))
      card.appendChild(row)
    }

    const foot = el('div', 'workout-foot')
    for (const n of notes) foot.appendChild(el('p', 'workout-note', n))
    foot.appendChild(button('结束训练', 'workout-end', () => {
      session.finishEarly()
      afterAction()
    }))
    card.appendChild(foot)
    mount(card)
  }

  function renderResumePrompt(p: WorkoutProgress): void {
    const card = makeCard('继续上次训练')
    card.appendChild(el('h3', 'workout-title', '继续上次训练？'))
    const name = built[p.exIdx]?.name
    card.appendChild(el('p', 'workout-note',
      `上次练到第 ${p.exIdx + 1} 个动作${name ? `（${name}）` : ''}的第 ${p.setIdx + 1} 组，` +
      `已经记下 ${p.doneIds.length} 个动作。`))
    const row = el('div', 'workout-actions')
    row.appendChild(button('继续', 'workout-primary', () => beginResume(p)))
    row.appendChild(button('重新开始', 'workout-ghost', () => {
      // 「重新开始」= 明确丢弃旧游标。用户看得见这句话，所以不是静默丢数据
      clearProgress(storage)
      beginFresh()
    }))
    card.appendChild(row)
    mount(card)
  }

  function afterAction(): void {
    persist()
    if (session.state().phase === 'finished') {
      void finish()
      return
    }
    renderActive()
  }

  // ── 一轮的生命周期 ────────────────────────────────────────────────
  /** 把组装结果里那些"要如实说明"的点收集成几句话 */
  function buildNotes(b: ReturnType<typeof buildSessionExercises>): string[] {
    const out: string[] = []
    if (b.skipped.length) {
      out.push(`这 ${b.skipped.length} 个动作记不上，已跳过：` +
               b.skipped.map((s) => s.name).join('、'))
    }
    if (b.withoutId) {
      // 老计划：写回按名字走 `norm_zh` 精确匹配，正常必然命中 ——
      // 所以这不是"记不上"，只是如实说一句，**不跳过**
      out.push(`其中 ${b.withoutId} 个动作没有动作库编号，将按名字记录。`)
    }
    return out
  }

  function resetRound(): void {
    outcomes = []
    pending.length = 0
    finishing = false
  }

  function beginFresh(): void {
    const b = buildSessionExercises(sessionDay)
    built = b.exercises
    notes = buildNotes(b)
    doneKeys = []
    resetRound()
    session.start(b.exercises)
    persist()          // 开始即落盘：此时刷新页面也能续上
    renderActive()
  }

  function beginResume(p: WorkoutProgress): void {
    const b = buildSessionExercises(sessionDay)
    built = b.exercises
    notes = buildNotes(b)
    doneKeys = [...p.doneIds]
    resetRound()
    session.resume(b.exercises, {
      exIdx: p.exIdx, setIdx: p.setIdx, state: p.state,
      ...(p.restEndsAt !== undefined ? { restEndsAt: p.restEndsAt } : {}),
    })
    persist()
    renderActive()
  }

  async function finish(): Promise<void> {
    if (finishing) return
    finishing = true
    // ⚠ 先等飞行中的写回落地：不等的话最后几个动作的成败还没进 `outcomes`，
    // 汇报就会漏 —— 用户会以为记上了，其实没有。
    await Promise.all(pending)
    const summary = summarizeSync(outcomes)
    const st = session.state()
    clearProgress(storage)
    setOpen(false)
    deps.onFinished?.({
      day: dayLabel,
      done: st.done,
      synced: summary.ok,
      failedNames: summary.failedNames,
      notice: summary.notice,
      allDone: st.done >= st.total,
    })
  }

  function closeFlow(): void {
    setOpen(false)
  }

  /** 当前这一轮的计划日（`beginFresh` / `beginResume` 都要它） */
  let sessionDay: PlanDay | null = null

  return {
    async open(): Promise<void> {
      if (open) return
      setOpen(true)
      renderMessage('正在读取今天的训练…')
      today = localDateISO()
      let load: TodayLoad
      try {
        load = await loadToday()
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        renderMessage(`读取计划失败：${msg}`, 'error')
        return
      }
      if (!load.day) {
        // 读不到 / 今天没安排 / 计划过期 —— 三种都用后端能给的话如实说，
        // 不弹空白、也不假装"今天没有安排"（那是另一回事）
        renderMessage(load.message || '今天没有训练安排。',
                      load.degraded ? 'error' : undefined)
        return
      }
      sessionDay = load.day
      planId = load.planId
      dayLabel = load.day.day || ''
      const b = buildSessionExercises(load.day)
      if (!b.exercises.length) {
        renderMessage('今天这一天的动作都记不上，没法跟练。', 'error')
        return
      }
      built = b.exercises
      const saved = loadProgress(storage, { today, planId })
      if (saved) {
        renderResumePrompt(saved)
        return
      }
      beginFresh()
    },

    close: closeFlow,
    isOpen: () => open,

    tick(): void {
      if (!open) return
      const st = session.state()
      // 归零是惰性的（见 workout-session.ts），状态可能在两次 tick 之间自己变。
      // 变了就整屏重绘，否则只改倒计时那个数字 —— 每 250ms 重建一次 DOM 太浪费。
      if (st.phase !== renderedPhase) {
        if (st.phase === 'finished') void finish()
        else if (st.phase === 'working' || st.phase === 'resting') renderActive()
        return
      }
      if (st.phase === 'resting' && countdownEl) {
        countdownEl.textContent = String(st.restLeft)
      }
    },
  }
}
