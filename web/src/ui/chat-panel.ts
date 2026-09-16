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
import { difficultyLabel, equipmentLabel, MEDIA_CREDIT, mediaUrl } from '../data/exercises'
import { muscleIdInItem, stageText, type ChatMessage, type ChatState } from './chat-flow'
import type { FoodCard } from '../data/vision'
import type { VoiceStatus } from './voice-flow'
import type { PhotoStatus } from './photo-flow'

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
  /**
   * 点相机按钮，拍餐识别。**不传就不渲染这个按钮** —— 识别服务不可用时
   * 宁可没有入口，也不要一个点了才报错的按钮（与 onMic 同一条约定）。
   */
  onPhoto?: () => void
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
  /** 拍餐状态 → 按钮外观与提示文字。逻辑在 photo-flow，这里只管画。 */
  setPhotoStatus: (status: PhotoStatus, error: string | null) => void
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

  // 带示例图的条目（只有动作检索会给）→ 画成卡片；其余走一行文本的列表。
  // 判据是**条目自带的能力**而不是另设一个类型名：后端哪天给别的条目也配了图，
  // 那边自动变卡片，前端不需要同步改。
  if (items.some((it) => typeof it.image === 'string' && it.image)) {
    renderExerciseCards(container, structured, items)
    return
  }

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
 * 动作条目 → 卡片：缩略图 + 名称 + 器械/难度 + 剂量。
 *
 * 为什么动作不能沿用一行文本的列表：库里的中文名是**逐词直译**的
 * （"摆臂 悬垂 直腿s"、"辅助 仰卧 小腿拉伸"），光看名字根本挑不出是不是自己
 * 要找的那个 —— **图是这一步唯一可靠的信息**。档案/记忆条目的 `name` 本身就是
 * 人话（"年龄"、"训练记录"），列表够用；动作不够。
 *
 * 交互照抄消歧选项那套：缩略图 44×44，点一下**原地**摊开成 180×180 动图。
 * ⚠ **180 是授权上限**（素材 © Gym visual），别往上调；整组末尾必须带署名 ——
 * 授权要求"每次使用都要带"，不是可选的礼貌（`data/exercises-dataset/NOTICE.md`）。
 */
export function renderExerciseCards(
  container: HTMLElement,
  structured: Structured,
  items: StructuredItem[],
): void {
  const box = el('div', 'chat-card')
  if (structured.title) box.appendChild(el('h4', 'chat-card-title', structured.title))

  const list = el('div', 'chat-exercises')
  for (const it of items) {
    const row = el('div', 'chat-exercise')

    const still = mediaUrl(it.image)
    if (still) {
      const img = el('img', 'chat-exercise-img')
      img.src = still
      img.alt = String(it.name_zh ?? '')
      img.loading = 'lazy' // 一次 5 条，不该一进对话就把图全拉了
      const gif = mediaUrl(it.gif_url)
      if (gif) {
        // 换 src 而不是再挂一个 <img>：GIF 均 100KB，只有用户点开那一刻才加载
        const btn = el('button', 'chat-exercise-media')
        btn.setAttribute('type', 'button')
        btn.setAttribute('aria-label', `看「${img.alt}」的动图`)
        btn.appendChild(img)
        // 用闭包变量而不是 `classList.contains` 记开合状态：node 侧的 DOM stub
        // 只实现了 toggle/add/remove（真实 DOM 才有 contains）。卡片每次都是
        // 重建的，闭包自己记最省事，也不多依赖一项 stub 能力。
        let open = false
        btn.addEventListener('click', () => {
          open = !open
          btn.classList.toggle('is-open', open)
          img.src = open ? gif : still
        })
        row.appendChild(btn)
      } else {
        // 没有动图就只画图，**不做成按钮** —— 点了没反应的按钮比没有按钮更糟
        const wrap = el('span', 'chat-exercise-media')
        wrap.appendChild(img)
        row.appendChild(wrap)
      }
    }

    const text = el('div', 'chat-exercise-text')
    text.appendChild(el('span', 'chat-exercise-name', String(it.name_zh ?? '')))
    const meta = [equipmentLabel(it.equipment), difficultyLabel(it.difficulty)]
      .filter(Boolean).join(' · ')
    if (meta) text.appendChild(el('span', 'chat-exercise-meta', meta))
    const dose = [
      it.sets ? `${it.sets} 组` : null,
      it.reps ? `${it.reps} 次` : null,
      it.rest_sec ? `休息 ${it.rest_sec} 秒` : null,
    ].filter(Boolean).join(' · ')
    if (dose) text.appendChild(el('span', 'chat-exercise-dose', dose))
    row.appendChild(text)
    list.appendChild(row)
  }
  box.appendChild(list)
  // 画了图就必须署名 —— 本函数只在条目带 image 时被调用，所以这里是无条件的
  box.appendChild(el('p', 'chat-exercise-credit', MEDIA_CREDIT))
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
 * 一个选项的图示。返回是否画出了图（整组署名要靠它）。
 *
 * 三种情况分开处理，因为**能点的东西必须真的有点的反应**：
 *   · 有图有动图 → 一个 button：点一下原地摊开成原始 180×180 的动图，再点收起
 *   · 只有图     → 只画图，**不做成按钮**（点了没反应的按钮比没有按钮更糟）
 *   · 没图       → 不画，也**不补占位**（占位框会被当成"图挂了"）
 */
function optionMedia(o: StructuredOption): HTMLElement | null {
  const still = mediaUrl(o.image)
  if (!still) return null
  const gif = mediaUrl(o.gif_url)

  const img = el('img', 'chat-option-img')
  img.src = still
  img.alt = o.name_zh
  img.loading = 'lazy' // 3-4 个候选，不该一进对话就把图全拉了

  if (!gif) {
    const box = el('span', 'chat-option-media')
    box.appendChild(img)
    return box
  }

  const btn = el('button', 'chat-option-media')
  btn.setAttribute('type', 'button')
  // 命中区是**看图**，不是**选它** —— 读屏念出来要和右边那个按钮分得开
  btn.setAttribute('aria-label', `看「${o.name_zh}」的动作动图`)
  btn.setAttribute('aria-expanded', 'false')
  btn.appendChild(img)
  // 开合状态存在闭包里而不是读 classList：stub 的 classList 没有 contains，
  // 读 className 又要处理空格 —— 闭包变量在两边语义都唯一。
  let open = false
  btn.addEventListener('click', () => {
    open = !open
    // 换 src 而不是叠两个 `<img>`：动图约 100KB，只有点开那一刻才下载
    img.src = open ? gif : still
    btn.classList.toggle('is-open', open)
    btn.setAttribute('aria-expanded', String(open))
  })
  return btn
}

/**
 * 器械 · 难度。两个都缺就**不画这一行** —— 写"未知"和"确实没有"读不出区别
 * （本仓的数据诚实约定：缺值返回 None + 原因，不编一个填充值顶上）。
 */
function optionMeta(o: StructuredOption): string | null {
  const parts = [equipmentLabel(o.equipment), difficultyLabel(o.difficulty)]
    .filter((x): x is string => !!x)
  return parts.length > 0 ? parts.join(' · ') : null
}

/** 「就记这个」按钮。点它才是真的写一条训练/偏好。 */
function optionButton(
  o: StructuredOption,
  onChoose?: (o: StructuredOption) => void,
  chosenId?: string | null,
): HTMLElement {
  const btn = el('button', 'chat-option')
  btn.setAttribute('type', 'button')

  const text = el('span', 'chat-option-text')
  text.appendChild(el('span', 'chat-option-name', o.name_zh))
  const meta = optionMeta(o)
  if (meta) text.appendChild(el('span', 'chat-option-meta', meta))
  btn.appendChild(text)

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
  return btn
}

/**
 * 消歧选项：让用户从**库内真实动作**里挑一个（human-in-the-loop）。
 *
 * 顺序由后端定（近 30 天练过的排前面）—— 前端**不再排**，否则同一个列表
 * 在两处有两条排序规则，迟早对不上。这里只负责画和把点击转出去。
 *
 * 每个选项是**两个独立命中区**：左边图（看动图）和右边按钮（就记这个）。
 * 之所以不把图塞进按钮里：库里的中文名是逐词翻译（"摆臂 悬垂 直腿s"），
 * 判断"我练的是不是这个"只能靠图 —— 而 `<button>` 套 `<button>` 是非法 HTML，
 * 用兄弟节点才能既分开命中区、又让键盘和读屏各拿到各的。
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

  let hasMedia = false
  for (const o of options) {
    const row = el('div', 'chat-option-row')
    const media = optionMedia(o)
    if (media) {
      hasMedia = true
      row.appendChild(media)
    }
    row.appendChild(optionButton(o, onChoose, chosenId))
    box.appendChild(row)
  }

  // 署名紧跟在图后面。授权要求每次使用都带（见 data/exercises-dataset/NOTICE.md），
  // 所以它挂在**画了图**的那一组下面，没图的一组不挂。
  if (hasMedia) box.appendChild(el('div', 'chat-options-credit', MEDIA_CREDIT))

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

/**
 * 营养卡片。
 *
 * **诚实降级要显眼**：`missing`（没算进去的食材）非空时总热量是**下界**，
 * 这一点必须写在卡片上 —— 一个偏低的数字如果不标注，用户会当成事实。
 * `—` 表示"缺值"而不是 0，两者不能混。
 */
export function renderFoodCard(container: HTMLElement, card: FoodCard): void {
  const box = el('div', 'chat-food')
  if (!card.ok) {
    box.appendChild(el('p', 'chat-error-line', card.error ?? '识别失败'))
    container.appendChild(box)
    return
  }
  box.appendChild(el('h4', 'chat-food-title', card.dish))
  const sub = [
    card.portion_g ? `按 ${Math.round(card.portion_g)}g 估算` : '',
    card.dish_id ? '成分库匹配' : '按食材估算',
  ].filter(Boolean).join(' · ')
  if (sub) box.appendChild(el('div', 'chat-food-sub', sub))

  const ps = card.nutrition?.per_serving ?? {}
  const ul = el('ul', 'chat-items')
  const rows: Array<[string, string | number | null | undefined]> = [
    ['热量', ps.calories_kcal],
    ['蛋白质', ps.protein_g],
    ['脂肪', ps.fat_g],
    ['碳水', ps.carbs_g],
  ]
  for (const [label, v] of rows) {
    const li = el('li', 'chat-item')
    li.appendChild(el('span', 'chat-item-name', label))
    // 缺值渲染成 —，**不是 0**：0 会读成"没有"，— 才是"不知道"
    li.appendChild(el('span', 'chat-item-value',
                      v == null ? '—' : `${Math.round(Number(v) * 10) / 10}${label === '热量' ? ' kcal' : ' g'}`))
    ul.appendChild(li)
  }
  box.appendChild(ul)

  for (const w of card.warnings ?? []) {
    box.appendChild(el('p', 'chat-food-warn', w))
  }
  if (card.allergens?.length) {
    box.appendChild(el('p', 'chat-food-allergen',
                       `含过敏原：${card.allergens.map((a) => a.replace('contains_', '')).join('、')}`))
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
    // 本地卡片（拍照识餐）与后端 structured 可以共存，先画卡片
    if (msg.foodCard) renderFoodCard(bubble, msg.foodCard)
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

  // 拍餐。**必须 type='button'**：form 里 type 缺省是 submit，
  // 点一下会顺带把半截文字发出去（同麦克风的理由）。
  let photo: HTMLButtonElement | null = null
  if (opts.onPhoto) {
    photo = el('button', 'chat-photo')
    photo.setAttribute('type', 'button')
    photo.setAttribute('aria-label', '拍照识餐')
    photo.textContent = '📷'
    photo.addEventListener('click', () => opts.onPhoto?.())
  }

  const row: HTMLElement[] = [input]
  if (mic) row.push(mic)   // 不传 onMic 就没有它，顺序不能错乱
  if (photo) row.push(photo)   // 不传 onPhoto 就没有它，顺序不能错乱
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
    // 相机同理：应答期间再发一张，会和当前这轮抢面板
    if (photo) photo.disabled = state.pending
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
    /**
     * 拍餐的状态反馈**必须可见**：识别是秒级（网络往返 + 模型推理），
     * 中间毫无提示的话用户会以为按钮坏了，然后再点一次 —— 那次要花钱。
     * 复用 `voiceLine` 那一行（role=status，读屏能读到）。
     */
    setPhotoStatus(status: PhotoStatus, error: string | null) {
      if (status === 'picking') {
        voiceLine.textContent = '选择照片…'
        voiceLine.classList.toggle('is-on', true)
        voiceLine.classList.remove('is-error')
      } else if (status === 'analyzing') {
        voiceLine.textContent = '正在识别…'
        voiceLine.classList.toggle('is-on', true)
        voiceLine.classList.remove('is-error')
      } else if (error) {
        voiceLine.textContent = error
        voiceLine.classList.toggle('is-on', true)
        voiceLine.classList.add('is-error')
      } else {
        voiceLine.textContent = ''
        voiceLine.classList.remove('is-on', 'is-error')
      }
      if (photo) photo.classList.toggle('is-busy', status === 'analyzing')
    },
  }
}
