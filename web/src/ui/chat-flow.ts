/**
 * 对话的状态机。**所有会出错的东西都在这里，且全部可单测。**
 *
 * 为什么抽出来：本仓的约定是 `main.ts` 无法测试（要 DOM + WebGL），所以
 * "逻辑留在 main.ts 等于没有守卫"（见 `detail-flow.ts` 的模块注释，那里记着
 * 两个真实回归的代价）。对话有会话 id、竞态、流式回调、错误恢复四件易错事，
 * 一件都不该留在 `main.ts`。
 *
 * 状态全在闭包里，依赖全部注入 —— 于是测试可以塞一个假 send、记下调用序列，
 * 不需要 DOM，也不需要网络。
 */

import type { ChatRequest, ChatResponse, ChatStage, CheckinResolveResponse } from '../data/chat'
import type { GuardPayload, Structured, StructuredItem, StructuredOption } from '../data/types'
import type { FoodCard } from '../data/vision'

/** 后端阶段值 → 中文文案。**未知阶段原样显示**，不编一个好听的说法。 */
export const STAGE_TEXT: Record<string, string> = {
  received: '已收到，正在分析',
  understand: '正在理解你的问题',
  work: '正在查询动作库与训练记录',
  render: '正在生成回答',
}

export function stageText(stage: string): string {
  return STAGE_TEXT[stage] ?? stage
}

/**
 * 阶段 → 给用户看的**一行字**。带载荷的阶段（技能/工具调用）在这里拼出名字。
 *
 * 未知阶段原样显示：后端将来加阶段时，前端不该显示 undefined，也不该编一个
 * 好听的说法糊过去。
 */
export function stageLabel(s: ChatStage): string {
  if (s.stage === 'skill' && s.skill) return `技能 · ${s.skill}`
  if (s.stage === 'tool' && s.skill) return `工具 · ${s.skill}`
  return stageText(s.stage)
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  /** 助手消息才有：结构化卡片 */
  structured?: Structured
  /** 助手消息才有：风险拦截负载 */
  guard?: GuardPayload | null
  mode?: string
  /**
   * 已经点过的消歧选项 id。点过之后整组选项不再可点 ——
   * 否则连点会重复补记同一次训练（后端每次都会建一条新事件）。
   */
  chosenOptionId?: string | null
  /**
   * 这一轮**实际调用过的技能/工具**，按调用顺序、去重。
   *
   * 用户的诉求是"看不见 agent 在调什么"：只显示"正在生成回答"的话，一次多步编排
   * 和一次直接回答长得一模一样。这里把 `skill` / `tool` 两类阶段记下来，
   * 挂在回复上，事后也能回头看它做了什么。
   */
  trace?: string[]
  /** 拍照识餐的结果卡片。**本地消息**：不经后端、不进对话上下文，
   *  只是一张挂在流里的展示卡。 */
  foodCard?: FoodCard
}

export interface ChatState {
  messages: ChatMessage[]
  /** 正在等回复。composer 据此禁用，避免连点发出一堆并发请求 */
  pending: boolean
  /** 当前阶段文案；不在等回复时为 null */
  stage: string | null
  /** 最近一次错误；成功后清空 */
  error: string | null
}

export interface ChatFlowDeps {
  send: (
    req: ChatRequest,
    onStage: (stage: ChatStage) => void,
  ) => Promise<ChatResponse>
  /** 状态变化时回调（渲染）。**不传就只更新内部状态**，便于纯逻辑测试。 */
  onChange?: (state: ChatState) => void
  /** 会话 id 的读写。注入是为了让"跨刷新保留会话"这件事可测且不依赖 localStorage。 */
  loadSession?: () => string | null
  saveSession?: (id: string) => void
  /** 消息里的用户 id（后端记忆图谱归属） */
  userId?: string
  /** agent 说"还没建档"时回调 → 自动把建档窗口弹出来。
   *  读的是后端给的结构化标记 `data.need_profile`，**不是匹配那句中文** ——
   *  文案一改靠匹配就会静默失效，而且看起来只是"自动弹窗不好使了"。 */
  onNeedProfile?: () => void
  /** 补记通道。不传则消歧选项点了没反应（测试里可以省略） */
  resolve?: (req: { exercise_id?: string; text?: string; name_zh?: string
                     raw?: string; user_id?: string; kind?: string
                     value?: string }) => Promise<CheckinResolveResponse>
}

export interface ChatFlow {
  state: () => ChatState
  /** 发一条消息。返回一个 promise，测试可以 await 它。 */
  send: (text: string) => Promise<void>
  /** 清空消息与错误（**不清会话 id**：那是和 agent 的上下文，清掉就断片了） */
  clear: () => void
  /** 消歧选择框里点一个候选 → 补记训练。`at` 是那条消息在列表里的下标。 */
  chooseOption: (option: StructuredOption, at: number) => Promise<void>
  /** 候选都不是 → 自己打一个动作名。后端会再解析一次；解析不中就如实说没找到。 */
  submitCustom: (text: string, at: number) => Promise<void>
  /** 把一张营养卡片作为助手消息追加进流里。拍照识别用——
   *  它不经 /v1/chat（图片没有文本意图），所以不走 send()。 */
  addFoodCard: (card: FoodCard) => void
}

export function createChatFlow(deps: ChatFlowDeps): ChatFlow {
  const messages: ChatMessage[] = []
  let sessionId: string | null = deps.loadSession?.() ?? null
  let pending = false
  let stage: string | null = null
  let error: string | null = null
  // 竞态守卫：每次发送领一个号，回来时号对不上就丢弃。
  // 触发场景：用户发了 A，等不及又发了 B；A 的响应后到，不能覆盖 B。
  let seq = 0

  const snapshot = (): ChatState => ({ messages: [...messages], pending, stage, error })
  const emit = (): void => deps.onChange?.(snapshot())

  /**
   * 补记一条消歧结果（候选与自定义输入**共用**）。
   *
   * 抽出来是因为"防重复补记"这件事只该有一份实现：两条路径各写一遍的话，
   * 将来只给其中一条加守卫，另一条就会重复记账 —— 而这种回归没有症状，
   * 只是训练记录悄悄多了一条。
   *
   * `mark` 是防重标记（候选用 exercise id，自定义用 `custom:文本`）。
   */
  async function record(
    payload: { exercise_id?: string; text?: string; name_zh?: string },
    mark: string,
    at: number,
  ): Promise<void> {
    const msg = messages[at]
    // 三道门都要：消息得在、没点过、有补记通道。少一道就可能重复补记。
    if (!msg || msg.chosenOptionId || !deps.resolve) return
    msg.chosenOptionId = mark          // 先标记再 await：并发连点也只会记一次
    emit()
    try {
      const req = { ...payload } as { exercise_id?: string; text?: string
                                      name_zh?: string; raw?: string; user_id?: string
                                      kind?: string; value?: string }
      const raw = msg.structured?.data?.raw
      if (typeof raw === 'string') req.raw = raw
      // kind/value 原样带回：这次点选可能是"记偏好"而不是"补记训练"
      // （偏好陈述里没对上的动作，见 nodes.clarify_exercise）。后端据此分流。
      const kind = msg.structured?.data?.kind
      if (typeof kind === 'string') req.kind = kind
      const value = msg.structured?.data?.value
      if (typeof value === 'string') req.value = value
      if (deps.userId) req.user_id = deps.userId
      const r = await deps.resolve(req)
      if (r.need_pick) {
        // 自定义输入没有完全同名的动作 → **不要硬记**（库里没有叫"深蹲"的动作，
        // 模糊匹配会记成"弹力带…分腿深蹲"这种器械都不一样的），把这批近似结果
        // 当成新的一轮候选让用户确认。新消息 = 新的补记守卫，可以重新点。
        messages.push({ role: 'assistant', text: r.reply || '是下面哪个？',
                        structured: { title: '确认动作',
                                      data: { options: r.options ?? [],
                                              raw: r.raw ?? payload.text ?? '' } } })
      } else {
        messages.push({ role: 'assistant',
                        text: r.ack || `已记下：${payload.name_zh ?? payload.text ?? ''}` })
      }
    } catch (e) {
      // 记失败要说出来，并且**放开重试**（把标记撤掉）—— 不然用户以为记上了
      msg.chosenOptionId = null
      error = e instanceof Error ? e.message : String(e)
    }
    emit()
  }

  function addFoodCard(card: FoodCard): void {
    // 本仓 flow 用的是模块内的 `messages` 数组 + `emit()` 通知（不是
    // `state.messages` + `notify()`）—— 照实际名字走，语义不变：
    // 追加一条本地助手消息并重绘。
    messages.push({ role: 'assistant', text: '', foodCard: card })
    emit()
  }

  return {
    state: snapshot,
    addFoodCard,

    async send(text: string): Promise<void> {
      const trimmed = text.trim()
      if (!trimmed || pending) return // 空消息与连点都直接吞掉

      const mine = ++seq
      const trace: string[] = []
      messages.push({ role: 'user', text: trimmed })
      pending = true
      stage = null
      error = null
      emit()

      try {
        const req: ChatRequest = { message: trimmed, session_id: sessionId }
        if (deps.userId) req.user_id = deps.userId

        const resp = await deps.send(req, (s) => {
          // 迟到的阶段不能污染新一轮
          if (mine !== seq) return
          stage = stageLabel(s)
          // 只记"调了什么"，不记粗粒度阶段（那句太笼统，事后回看没有信息量）
          if (s.stage === 'skill' || s.stage === 'tool') {
            const label = stageLabel(s)
            if (!trace.includes(label)) trace.push(label)
          }
          emit()
        })

        if (mine !== seq) return // 已被更新的请求取代，这个响应作废
        // **会话 id 必须存下来**：后端是无状态的，不带上它下一轮就没有上下文
        // （历史、档案全丢）。而且后端在缺省时会新生成一个返回给我们。
        if (resp.session_id) {
          sessionId = resp.session_id
          deps.saveSession?.(resp.session_id)
        }
        // 未建档 → 主动弹窗口。放在 push 之后：先让回复进列表，
        // 用户看到"尚未建档"那句话的同时窗口才弹出来，因果才连得上。
        const needProfile = (resp.structured?.data as { need_profile?: boolean } | undefined)
          ?.need_profile
        messages.push({
          role: 'assistant',
          text: resp.reply,
          ...(resp.structured ? { structured: resp.structured } : {}),
          guard: resp.guard ?? null,
          ...(resp.mode_used ? { mode: resp.mode_used } : {}),
          ...(trace.length ? { trace } : {}),
        })
        pending = false
        stage = null
        emit()
        if (needProfile) deps.onNeedProfile?.()
      } catch (e) {
        if (mine !== seq) return
        pending = false
        stage = null
        // 失败必须**可见**：不把错误吞掉、也不留一个永远转圈的 pending
        error = e instanceof Error ? e.message : String(e)
        emit()
      }
    },

    async chooseOption(option: StructuredOption, at: number): Promise<void> {
      await record({ exercise_id: option.id, name_zh: option.name_zh }, option.id, at)
    },

    async submitCustom(text: string, at: number): Promise<void> {
      const t = text.trim()
      if (!t) return
      await record({ text: t }, `custom:${t}`, at)
    },

    clear(): void {
      seq++ // 让在途响应作废，否则清空后会被一条迟到的回复重新填上
      messages.length = 0
      pending = false
      stage = null
      error = null
      emit()
    },
  }
}

/**
 * 从一条结构化条目里找出它指的是哪块肌肉，找不到返回 null。
 *
 * **优先 `target`**（`teach` 分支直接给的 canonical id）；否则在展示串里扫 ——
 * `qa#muscle_panel` 那种分支把 id 嵌在名字里（`"腿（quadriceps）面板"`）。
 *
 * 扫的时候拿 `ids`（= 28 个 canonical id 的**单源**）去比对，**而不是写正则猜**：
 * 猜出来的东西会随着 id 增删而腐化，而且猜错就会点亮一块无关的肌肉。
 * **扫不到就不给链接**，不猜。
 */
export function muscleIdInItem(item: StructuredItem, ids: readonly string[]): string | null {
  if (typeof item.target === 'string' && ids.includes(item.target)) return item.target
  const hay = `${item.name ?? ''} ${item.value ?? ''}`
  // **取最长的那个命中，不是第一个。** 当前 28 个 id 恰好互不为子串，但
  // `core` / `core_stabilizers` 这类关系一出现，按数组顺序取的写法就会稳定地点亮
  // 错的那一块（而且看起来"能用"，很难发现）。多花一次比较换掉这个雷。
  let best: string | null = null
  for (const id of ids) {
    if (hay.includes(id) && (best === null || id.length > best.length)) best = id
  }
  return best
}
