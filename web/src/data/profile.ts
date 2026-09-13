/**
 * 建档（身体档案）的数据层 + **字段单源**。
 *
 * 字段范围与后端 `app/core/agent.py:validate_profile` 一一对应（改一处须改两处）。
 * 前端这份只用于**即时提示**；判定权威始终在后端 —— 那边返回 422 才是最终结论。
 * 不复制一份到别处，是为了让"窗口里能填什么"只有一个来源。
 */

export type FieldKind = 'int' | 'number' | 'select'

export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  /** 单位（数字类） */
  unit?: string
  min?: number
  max?: number
  step?: number
  options?: Array<{ value: string; label: string }>
  /** 选填：留空不算错，后端有默认值兜着（见 REQUIRED_FIELDS 的说明） */
  optional?: boolean
  /** 打开时的预填值（用户没填过时用它），比留一个空下拉友好 */
  defaultValue?: string
  hint?: string
}

/**
 * 建档**必填**项 —— 与 `plan_skill.REQUIRED` 逐字一致。
 *
 * 只有这三项是计划算不出来的。其余都能默认：`goal` 缺省 maintain、
 * `activity` 缺省 1.55（见 `plan_skill` 的两行 setdefault），`sex` 只进 BMR 公式、
 * 缺省按中性算。
 *
 * ⚠ **不要把选填项标成必填**：前端比后端严，会让用户被自己的表单挡住 ——
 * 明明后端能收，界面却说"请填写"。本仓的权威校验只有 `validate_profile` 一处。
 */
export const REQUIRED_FIELDS = ['age', 'height_cm', 'weight_kg'] as const

/**
 * 建档常用六项。
 *
 * 后端的计划功能**硬性要求** `weight_kg / height_cm / age`（`plan_skill.REQUIRED`），
 * 另三项影响算出来的数：`sex` 进 BMR 公式，`goal` 决定热量方向，`activity` 是
 * TDEE 系数。六项定下来计划就算得准，其余调参项（热量缺口、蛋白/脂肪系数、
 * 编排方案、伤病）继续走聊天 —— 放进来会让表单变长，而它们多数人不需要动。
 */
export const PROFILE_FIELDS: FieldSpec[] = [
  { key: 'sex', label: '性别', kind: 'select', optional: true, options: [
    { value: 'male', label: '男' }, { value: 'female', label: '女' } ],
    hint: '影响基础代谢公式；不填按中性算' },
  { key: 'age', label: '年龄', kind: 'int', unit: '岁', min: 1, max: 120 },
  { key: 'height_cm', label: '身高', kind: 'number', unit: 'cm', min: 50, max: 250, step: 0.5 },
  { key: 'weight_kg', label: '体重', kind: 'number', unit: 'kg', min: 5, max: 400, step: 0.1 },
  { key: 'goal', label: '目标', kind: 'select', optional: true,
    defaultValue: 'maintain', options: [
    { value: 'lose_fat', label: '减脂' },
    { value: 'maintain', label: '维持' },
    { value: 'build_muscle', label: '增肌' } ] },
  { key: 'activity', label: '活动量', kind: 'select', optional: true, options: [
    { value: '1.2', label: '久坐（1.2）' },
    { value: '1.375', label: '轻度（1.375）' },
    { value: '1.55', label: '中度（1.55，默认）' },
    { value: '1.725', label: '高强度（1.725）' },
    { value: '1.9', label: '极高（1.9）' } ],
    hint: '不确定就留空，按中度算' },
]

export interface Profile {
  sex?: 'male' | 'female'
  age?: number
  height_cm?: number
  weight_kg?: number
  goal?: 'lose_fat' | 'maintain' | 'build_muscle'
  activity?: number
  [k: string]: unknown
}

/**
 * 表单值 → 提交用的档案对象。**只带上填了的字段**。
 *
 * 不填 `undefined` 占位：后端是 `sess.profile.update(profile)` 的**合并**语义，
 * 带一个 `undefined`（序列化后键还在但值为 null）会把已有值抹掉。
 */
export function toProfile(values: Record<string, string>): Profile {
  const out: Profile = {}
  for (const f of PROFILE_FIELDS) {
    const raw = (values[f.key] ?? '').trim()
    if (!raw) continue
    if (f.kind === 'select') {
      if (f.key === 'activity') out.activity = Number(raw)
      else out[f.key] = raw
    } else if (f.kind === 'int') {
      out[f.key] = Math.round(Number(raw))
    } else {
      out[f.key] = Number(raw)
    }
  }
  return out
}

/**
 * 前端即时校验：返回 `{字段: 错误}`。
 *
 * **只提示，不拦截提交的最终判定**（后端 validate_profile 才是权威）——但能挡住
 * 明显的输入错误，省一轮往返。
 */
export function validateValues(values: Record<string, string>): Record<string, string> {
  const errs: Record<string, string> = {}
  for (const f of PROFILE_FIELDS) {
    const raw = (values[f.key] ?? '').trim()
    if (!raw) {
      // 必填只认 REQUIRED_FIELDS（与后端 plan_skill.REQUIRED 同源）——
      // 多标一个必填就会挡住一个后端本来接受的提交
      if ((REQUIRED_FIELDS as readonly string[]).includes(f.key)) errs[f.key] = '请填写'
      continue
    }
    if (f.kind === 'select') continue      // 下拉的取值由 options 保证
    const n = Number(raw)
    if (!Number.isFinite(n)) { errs[f.key] = '请填数字'; continue }
    if (f.min !== undefined && n < f.min) { errs[f.key] = `不能小于 ${f.min}`; continue }
    if (f.max !== undefined && n > f.max) { errs[f.key] = `不能大于 ${f.max}` }
  }
  return errs
}

export async function fetchProfile(
  sessionId: string,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<Profile | null> {
  const res = await fetchImpl(`/v1/profile/${encodeURIComponent(sessionId)}`)
  // 404 = 会话还不存在（没聊过也没建过档）—— 空档案，不是错误
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`读取档案失败：HTTP ${res.status}`)
  const d = (await res.json()) as { profile?: Profile }
  return d.profile ?? null
}

export interface SaveProfileResult {
  ok: boolean
  profile?: Profile
  /** 后端 422 的字段级说明（validate_profile 的原话） */
  details?: string[]
  error?: string
}

export async function saveProfile(
  sessionId: string,
  profile: Profile,
  userId: string,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<SaveProfileResult> {
  const res = await fetchImpl('/v1/profile', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, profile, user_id: userId }),
  })
  const body = (await res.json().catch(() => ({}))) as SaveProfileResult
  if (res.status === 422) return { ok: false, details: body.details ?? [], error: body.error }
  if (!res.ok) return { ok: false, error: `保存失败：HTTP ${res.status}` }
  return { ok: true, profile: body.profile }
}
