/**
 * 建档窗口：把"逐句告诉 agent 我多高多重"换成**填一张表**。
 *
 * 以前只能靠聊天：用户得说"我 178、72 公斤、今年 28"，抽取出错了还得再说一遍，
 * 而且他根本不知道该说哪些。表单一次问清，字段范围也当场给出来。
 *
 * 校验分两层：本地先挡明显错误（即时反馈），**最终以后端 422 为准** ——
 * 范围判定只在 `app/core/agent.py:validate_profile` 一处。
 */
import {
  PROFILE_FIELDS, fetchProfile, saveProfile, toProfile, validateValues,
  type FieldSpec, type Profile, type SaveProfileResult,
} from '../data/profile'

export interface ProfileFormDeps {
  root: HTMLElement
  sessionId: string
  userId: string
  /** 注入以便测试；默认走 data/profile 的真实实现 */
  load?: (sessionId: string) => Promise<Profile | null>
  save?: (sessionId: string, profile: Profile, userId: string) => Promise<SaveProfileResult>
  onSaved?: (profile: Profile) => void
}

export interface ProfileForm {
  open: () => Promise<void>
  close: () => void
  isOpen: () => boolean
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string,
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag)
  if (className) n.className = className
  if (text !== undefined) n.textContent = text
  return n
}

/** 当前档案值 → 输入框的字符串。下拉用的是字符串，数字也要转成字符串。 */
function toValue(v: unknown): string {
  return v === undefined || v === null ? '' : String(v)
}

export function createProfileForm(deps: ProfileFormDeps): ProfileForm {
  // 默认实现把 `userId` 一起带上：读路径在会话缓存缺失时要靠它从图谱取档案
  // （见 data/profile.ts 的 fetchProfile）。注入的 `load` 保持单参签名，
  // 现有测试不必跟着改。
  const load = deps.load ?? ((sid: string) => fetchProfile(sid, deps.userId))
  const save = deps.save ?? ((sid, p, uid) => saveProfile(sid, p, uid))
  let open = false

  const root = deps.root
  root.innerHTML = ''

  const backdrop = el('div', 'profile-backdrop')
  const form = el('form', 'profile-card')
  form.setAttribute('role', 'dialog')
  form.setAttribute('aria-modal', 'true')
  form.setAttribute('aria-label', '身体档案')

  form.appendChild(el('h3', 'profile-title', '身体档案'))
  form.appendChild(el('p', 'profile-sub',
    '填了才能算热量、排计划。只填知道的部分也行，之后随时能改。'))

  const grid = el('div', 'profile-fields')
  const inputs = new Map<string, HTMLInputElement | HTMLSelectElement>()
  const errs = new Map<string, HTMLElement>()

  function addField(f: FieldSpec): void {
    const row = el('label', 'profile-field')
    const lab = el('span', 'profile-field-label', f.label)
    if (f.unit) lab.appendChild(el('span', 'profile-unit', ` (${f.unit})`))
    row.appendChild(lab)

    let input: HTMLInputElement | HTMLSelectElement
    if (f.kind === 'select') {
      const sel = el('select', 'profile-input')
      // 空选项：选填字段允许留空；必填字段由本地校验挡住
      const blank = el('option', undefined, f.optional ? '（不填，按默认）' : '请选择')
      blank.setAttribute('value', '')
      sel.appendChild(blank)
      for (const o of f.options ?? []) {
        const opt = el('option', undefined, o.label)
        opt.setAttribute('value', o.value)
        sel.appendChild(opt)
      }
      input = sel
    } else {
      const inp = el('input', 'profile-input')
      inp.setAttribute('type', 'number')
      if (f.min !== undefined) inp.setAttribute('min', String(f.min))
      if (f.max !== undefined) inp.setAttribute('max', String(f.max))
      if (f.step !== undefined) inp.setAttribute('step', String(f.step))
      input = inp
    }
    input.setAttribute('name', f.key)
    inputs.set(f.key, input)
    row.appendChild(input)

    const err = el('span', 'profile-field-err')
    errs.set(f.key, err)
    row.appendChild(err)
    if (f.hint) row.appendChild(el('span', 'profile-field-hint', f.hint))
    grid.appendChild(row)
  }
  for (const f of PROFILE_FIELDS) addField(f)
  form.appendChild(grid)

  const status = el('p', 'profile-status')
  form.appendChild(status)

  const actions = el('div', 'profile-actions')
  const cancel = el('button', 'profile-cancel', '取消')
  cancel.setAttribute('type', 'button')
  const submit = el('button', 'profile-save', '保存')
  submit.setAttribute('type', 'submit')
  actions.append(cancel, submit)
  form.appendChild(actions)

  root.append(backdrop, form)

  function setFieldErrors(map: Record<string, string>, generic?: string[]): void {
    for (const [k, node] of errs) node.textContent = map[k] ?? ''
    // 后端 422 的 details 是整句（"weight_kg 取值非法: 500"），展示在底部；
    // 不再往字段上映射 —— 那需要反过来解析它的中文措辞，正是要避免的耦合
    status.textContent = generic?.join('；') ?? ''
    status.classList.toggle('is-error', Boolean(generic?.length))
  }

  function collect(): Record<string, string> {
    const v: Record<string, string> = {}
    for (const [k, node] of inputs) v[k] = node.value
    return v
  }

  function fill(p: Profile | null): void {
    for (const [k, node] of inputs) {
      const cur = toValue(p?.[k])
      // 用户没填过 → 用字段自带的预填值（如目标默认"维持"），
      // 比留一个空下拉好：空下拉要么被误当成"没这一项"，要么逼用户瞎选
      const spec = PROFILE_FIELDS.find((f) => f.key === k)
      node.value = cur || spec?.defaultValue || ''
    }
    setFieldErrors({})
  }

  function setOpen(next: boolean): void {
    open = next
    root.classList.toggle('is-open', next)
    if (!next) status.textContent = ''
  }

  cancel.addEventListener('click', () => setOpen(false))
  backdrop.addEventListener('click', () => setOpen(false))

  form.addEventListener('submit', (ev) => {
    const e = ev as unknown as { preventDefault?: () => void }
    e.preventDefault?.()
    void submitAll()
  })

  async function submitAll(): Promise<void> {
    const values = collect()
    const local = validateValues(values)
    if (Object.keys(local).length) {
      setFieldErrors(local)
      return
    }
    setFieldErrors({})
    submit.disabled = true
    status.textContent = '保存中…'
    status.classList.remove('is-error')
    try {
      const r = await save(deps.sessionId, toProfile(values), deps.userId)
      if (!r.ok) {
        // 后端才是权威：本地放过、后端拒绝的情况要如实显示它的原话
        setFieldErrors({}, [r.error ?? '保存失败', ...(r.details ?? [])])
        return
      }
      setOpen(false)
      deps.onSaved?.(r.profile ?? toProfile(values))
    } catch (err) {
      setFieldErrors({}, [err instanceof Error ? err.message : String(err)])
    } finally {
      submit.disabled = false
    }
  }

  return {
    async open(): Promise<void> {
      setOpen(true)
      status.textContent = '读取中…'
      try {
        fill(await load(deps.sessionId))
      } catch {
        fill(null)            // 读不到就当空档重填，不挡住建档（写会覆盖）
      }
      status.textContent = ''
    },
    close: () => setOpen(false),
    isOpen: () => open,
  }
}
