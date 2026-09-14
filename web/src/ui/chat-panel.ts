/**
 * 对话面板的 DOM 渲染。**纯渲染**：不发请求、不存状态，状态由 `chat-flow` 给。
 *
 * 结构（自上而下）：头部 / 消息滚动区 / 阶段进度 / 错误条 / 输入区。
 *
 * 本模块只依赖 `dom-stub.ts` 覆盖得到的那几个 API（createElement / appendChild /
 * textContent / className / classList / setAttribute / scrollTop），
 * 于是它能在 node 里被断言，而不是只能靠肉眼。
 */
import type { Structured, StructuredItem, StructuredOption } from '../data/types'
import { muscleIdInItem, stageText, type ChatMessage, type ChatState } from './chat-flow'
import type { VoiceStatus } from './voice-flow'

/** 点结构化卡片时回调。返回 null 表示这块肌肉在 3D 里找不到，**不给链接**。 */
export type MuscleLinkHandler = (muscleId: string) => void

export interface ChatPanelOptions {
  /** 面板根元素（.chat-panel） */
  root: HTMLElement
  /** 28 个 canonical 肌肉 id 的单源（来自 load-model.ts 的 MUSCLE_IDS） */
  muscleIds: readonly string[]
  onMuscleClick?: MuscleLinkHandler
  /** 点了消歧选项（第二个参数是消息下标，供 flow 定位并防重复点） */
  onChooseOption?: (o: StructuredOption, at: number) => void
  /** 自定义输入的动作名（第二个参数是消息下标） */
  onChooseCustom?: (text: string, at: number) => void
  /** 打开建档窗口 */
  onOpenProfile?: () => void
  /**
   * 点麦克风按钮。**不传就不渲染这个按钮** —— 转写服务不可用时（未装模型、
   * 或走了非 https/localhost）宁可没有按钮，也不要一个点了就报错的按钮。
   */
  onMic?: () => void
  onSubmit: (text: string) => void
}

export interface ChatPanel {
  render: (state: ChatState) => void
  /** 折叠/展开。窄屏下由底部抽屉的把手触发 */
  setOpen: (open: boolean) => void
  isOpen: () => boolean
  /** 取输入框里的文字并清空 —— 供表单提交与快捷键共用 */
  takeInput: () => string
  /**
   * 把文本放进输入框（**不覆盖用户已经打的字**，追加在后面）。
   *
   * 语音转写的结果走这里回填：给用户一个改字的机会，而不是替他发出去。
   * 不复用 `takeInput`（那是取走不是写入，且不 trim）。
   */
  setInput: (text: string) => void
  /** 录音/转写状态 → 按钮外观与提示文字。逻辑在 voice-flow，这里只管画。 */
  setVoiceStatus: (status: VoiceStatus, error: string | null) => void
}

/** 元素的创建收口在这里，测试的 DOM stub 只需要支持这几步。 */
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

/**
 * 一条助手的结构化卡片。
 *
 * **逐级容忍**：`structured` 可能没有 `data`（guard / clarify 两种 mode），
 * `data` 可能没有 `items`（smalltalk），`items` 可能为空 —— 全都当"没有卡片"处理，
 * 不能假设任何一层存在（否则 guard 时整条消息渲染到一半就抛错）。
 */
export function renderStructured(
  container: HTMLElement,
  structured: Structured | undefined,
  ids: readonly string[],
  onMuscleClick?: MuscleLinkHandler,
): void {
  if (!structured) return
  const items = structured.data?.items
  if (structured.error) {
    // 技能失败：把原因显出来（后端透传的，不自己编措辞）
    container.appendChild(el('p', 'chat-error-line', structured.error))
  }
  if (!Array.isArray(items) || items.length === 0) return

  const box = el('div', 'chat-card')
  if (structured.title) box.appendChild(el('h4', 'chat-card-title', structured.title))

  const ul = el('ul', 'chat-items')
  for (const it of items) {
    const li = el('li', 'chat-item')
    const id = muscleIdInItem(it as StructuredItem, ids)
    const hasValue = typeof it.value === 'string' && it.value.length > 0

    if (id && onMuscleClick) {
      // 可点的才用 button —— 用 div 加 onClick 键盘和读屏都够不着
      const btn = el('button', 'chat-item-name is-link')
      btn.setAttribute('type', 'button')
      btn.textContent = it.name ?? id
      btn.addEventListener('click', () => onMuscleClick(id))
      li.appendChild(btn)
    } else {
      li.appendChild(el('span', 'chat-item-name', it.name ?? ''))
    }
    if (hasValue) li.appendChild(el('span', 'chat-item-value', String(it.value)))
    ul.appendChild(li)
  }
  box.appendChild(ul)
  container.appendChild(box)
}

/**
 * 风险拦截卡片。**必须与普通回答长得明显不同** ——
 * 后端把 `level_label` / `advice` 算好了却没序列化，导致"不建议练"以前只能
 * 退化成一段和普通回答一样的文字；现在有结构化的负载就该显眼。
 */
export function renderGuard(container: HTMLElement, guard: ChatMessage['guard']): void {
  if (!guard) return
  const box = el('div', 'chat-guard')
  box.setAttribute('role', 'alert') // 安全提示要能被读屏立刻读到
  box.appendChild(el('div', 'chat-guard-label', guard.level_label ?? '安全提示'))
  if (guard.advice) box.appendChild(el('p', 'chat-guard-advice', guard.advice))
  container.appendChild(box)
}

/**
 * 消歧选项：让用户从**库内真实动作**里挑一个（human-in-the-loop）。
 *
 * 顺序由后端定（近 30 天练过的排前面）—— 前端**不再排**，否则同一个列表
 * 在两处有两条排序规则，迟早对不上。这里只负责画和把点击转出去。
 */
export function renderOptions(
  container: HTMLElement,
  structured: Structured | undefined,
  onChoose?: (o: StructuredOption) => void,
  chosenId?: string | null,
  onCustom?: (text: string) => void,
): void {
  const options = structured?.data?.options
  if (!Array.isArray(options) || options.length === 0) return

  const raw = structured?.data?.raw
  const box = el('div', 'chat-options')
  if (typeof raw === 'string' && raw) {
    box.appendChild(el('div', 'chat-options-hint', `「${raw}」对不上库里的动作，选一个：`))
  }

  for (const o of options) {
    const btn = el('button', 'chat-option')
    btn.setAttribute('type', 'button')
    btn.appendChild(el('span', 'chat-option-name', o.name_zh))
    // 练过的加角标：这正是"优先偏好动作"对用户可见的那一半
    if (o.recent_count && o.recent_count > 0) {
      btn.appendChild(el('span', 'chat-option-badge', `练过 ${o.recent_count} 次`))
    }
    // 已经点过 → 整组禁用：连点会重复补记同一次训练
    if (chosenId) {
      btn.disabled = true
      btn.classList.add('is-chosen')
      if (chosenId === o.id) btn.classList.add('is-picked')
    } else if (onChoose) {
      btn.addEventListener('click', () => onChoose(o))
    }
    box.appendChild(btn)
  }

  // 候选都不是 → 自己打一个。**只在没选过时出现**：选完还留着输入框，
  // 看着像还能再记一次，而补记守卫已经把这组锁住了。
  if (!chosenId && onCustom) {
    const form = el('form', 'chat-option-custom')
    const input = el('input', 'chat-option-input')
    input.setAttribute('type', 'text')
    input.setAttribute('placeholder', '都不是？自己写一个动作名')
    input.setAttribute('autocomplete', 'off')
    const ok = el('button', 'chat-option-submit')
    ok.setAttribute('type', 'submit')
    ok.textContent = '记账'
    form.append(input, ok)
    form.addEventListener('submit', (ev) => {
      ev.preventDefault()
      const t = input.value.trim()
      if (!t) return
      input.value = ''
      onCustom(t)
    })
    box.appendChild(form)
  }
  container.appendChild(box)
}

function renderMessage(
  list: HTMLElement,
  msg: ChatMessage,
  ids: readonly string[],
  onMuscleClick?: MuscleLinkHandler,
  onChooseOption?: (o: StructuredOption, at: number) => void,
  at = -1,
  onChooseCustom?: (text: string, at: number) => void,
): void {
  const row = el('div', `chat-msg is-${msg.role}`)
  const bubble = el('div', 'chat-bubble')
  // reply 是纯文本（后端给的是自然语言），用 textContent 而不是 innerHTML ——
  // innerHTML 会把模型输出里的尖括号当标签解析
  bubble.appendChild(el('p', 'chat-text', msg.text))

  // 调用轨迹：这一轮实际用了哪些技能 / 工具。放在正文之下、卡片之上 ——
  // 它是"过程"，卡片是"结论"，过程不该抢结论的位置。
  if (msg.role === 'assistant' && msg.trace && msg.trace.length) {
    const t = el('div', 'chat-trace')
    t.appendChild(el('span', 'chat-trace-label', '调用'))
    t.appendChild(el('span', 'chat-trace-list', msg.trace.join(' · ')))
    bubble.appendChild(t)
  }

  renderGuard(bubble, msg.guard)
  if (msg.role === 'assistant') {
    renderStructured(bubble, msg.structured, ids, onMuscleClick)
    renderOptions(bubble, msg.structured,
                  onChooseOption ? (o) => onChooseOption(o, at) : undefined,
                  msg.chosenOptionId,
                  onChooseCustom ? (t) => onChooseCustom(t, at) : undefined)
  }
  row.appendChild(bubble)
  list.appendChild(row)
}

export function createChatPanel(opts: ChatPanelOptions): ChatPanel {
  const { root, muscleIds, onMuscleClick } = opts
  let open = true

  root.innerHTML = ''

  const head = el('div', 'chat-head')
  head.appendChild(el('span', 'chat-title', 'FitMind 教练'))
  if (opts.onOpenProfile) {
    const pf = el('button', 'chat-profile-btn', '我的信息')
    pf.setAttribute('type', 'button')
    pf.addEventListener('click', () => opts.onOpenProfile?.())
    head.appendChild(pf)
  }
  const close = el('button', 'chat-close')
  close.setAttribute('type', 'button')
  close.setAttribute('aria-label', '收起对话')
  close.textContent = '×'
  close.addEventListener('click', () => setOpen(false))
  head.appendChild(close)

  const list = el('div', 'chat-list')
  list.setAttribute('role', 'log')
  list.setAttribute('aria-live', 'polite')

  const stageLine = el('div', 'chat-stage')
  stageLine.setAttribute('role', 'status')
  stageLine.setAttribute('aria-live', 'polite')

  const errorLine = el('div', 'chat-error')
  errorLine.setAttribute('role', 'alert')

  const form = el('form', 'chat-form')
  const input = el('input', 'chat-input')
  input.setAttribute('type', 'text')
  input.setAttribute('placeholder', '问点什么…（今天练什么 / 恢复得怎么样）')
  input.setAttribute('autocomplete', 'off')
  // 语音状态条：录音/转写/错误都走它。**必须有可见反馈** —— 从松手到出字
  // 是秒级（medium 每次约 0.7s + 首次 4.4s 加载），中间毫无提示的话用户
  // 会以为按钮坏了，然后再点一次。
  const voiceLine = el('div', 'chat-voice')
  voiceLine.setAttribute('role', 'status')
  voiceLine.setAttribute('aria-live', 'polite')

  const send = el('button', 'chat-send')
  send.setAttribute('type', 'submit')
  send.textContent = '发送'

  // 麦克风。**必须 type='button'**：form 里 type 缺省是 submit，
  // 点一下会顺带把表单提交了（把半截文字发出去）。
  let mic: HTMLButtonElement | null = null
  if (opts.onMic) {
    mic = el('button', 'chat-mic')
    mic.setAttribute('type', 'button')
    mic.setAttribute('aria-label', '语音输入')
    mic.textContent = '🎤'
    mic.addEventListener('click', () => opts.onMic?.())
  }

  const row: HTMLElement[] = [input]
  if (mic) row.push(mic)   // 不传 onMic 就没有它，顺序不能错乱
  row.push(send)
  form.append(...row)
  form.addEventListener('submit', (ev) => {
    ev.preventDefault()
    const text = input.value.trim()
    if (!text) return
    input.value = ''
    opts.onSubmit(text)
  })

  root.append(head, list, stageLine, errorLine, voiceLine, form)

  function setOpen(next: boolean): void {
    open = next
    root.classList.toggle('is-open', next)
  }

  function render(state: ChatState): void {
    // 消息区整段重建（对话量小，重建比 diff 简单且不会残留）
    list.innerHTML = ''
    state.messages.forEach((m, i) => {
      renderMessage(list, m, muscleIds, onMuscleClick, opts.onChooseOption, i,
                    opts.onChooseCustom)
    })
    if (state.messages.length === 0) {
      list.appendChild(el('p', 'chat-empty', '你好，我是你的教练。可以问我今天练什么、某块肌肉恢复得怎么样，或者让我排一份计划。'))
    }
    // 新消息进视野：不滚的话用户以为没反应
    list.scrollTop = list.scrollHeight

    stageLine.textContent = state.stage ? stageText(state.stage) : ''
    stageLine.classList.toggle('is-on', Boolean(state.stage))

    errorLine.textContent = state.error ?? ''
    errorLine.classList.toggle('is-on', Boolean(state.error))

    // 等待期间禁用发送：不禁的话连点会发出并发请求，后端会话状态会乱
    input.disabled = state.pending
    send.disabled = state.pending
    // 麦克风跟着一起禁 —— agent 应答期间再录一段，回来会和当前这轮抢输入框
    if (mic) mic.disabled = state.pending
    root.classList.toggle('is-busy', state.pending)

    // 有回复就先滚动到最新
    list.scrollTop = list.scrollHeight
  }

  setOpen(true)
  return {
    render,
    setOpen,
    isOpen: () => open,
    takeInput: () => {
      const v = input.value
      input.value = ''
      return v
    },
    setInput(text: string) {
      // **追加而不是覆盖**：用户可能已经打了半句再想起来用语音补一句，
      // 直接覆盖会把他打的字静默丢掉。
      input.value = input.value ? `${input.value} ${text}` : text
    },
    setVoiceStatus(status: VoiceStatus, error: string | null) {
      if (mic) {
        mic.classList.toggle('is-recording', status === 'recording')
        mic.disabled = status === 'transcribing'
        mic.textContent = status === 'recording' ? '⏹' : '🎤'
        mic.setAttribute('aria-label', status === 'recording' ? '结束录音' : '语音输入')
      }
      const text =
        error ? error
        : status === 'recording' ? '正在录音…说完点一下按钮结束'
        : status === 'transcribing' ? '正在转写…'
        : ''
      voiceLine.textContent = text
      // 错误要显眼（红），录音/转写是正常态（安静一点）
      voiceLine.classList.toggle('is-on', Boolean(text))
      voiceLine.classList.toggle('is-error', Boolean(error))
    },
  }
}
