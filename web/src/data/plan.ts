/**
 * 训练计划：从图谱读回**已经生成过**的那一份，不在这里重算。
 *
 * 数据来自 `GET /v1/plan`，背后是 `MemoryStore.latest_plan` —— 即
 * `plan_skill._register_plan` 在产出计划时登记的 `PlanVersion`。
 * 同一份计划要在聊天、网页、后续编辑里保持一致，所以**只有图谱一个来源**。
 *
 * ⚠ `content` 是**原样透传**的后端结构，本文件只做类型声明与搬运，
 * 不重塑、不补默认值。重塑就会多一个会跟后端漂的平行结构。
 */

/** 计划里的一天。`type` 为 `rest` 时 `exercises` 为空。 */
export interface PlanDay {
  /** 如"推日(胸·肩·三头)" */
  day: string
  /** 日期锚点，如"今天（9月13日 周日）"——**后端算好的中文，前端不重算** */
  date: string
  /** `train` | `rest` */
  type: string
  pattern?: string
  /** 该天因减量/封堵被调整过的标记（存在即为真） */
  deload?: boolean
  /** 休息日的恢复提示 */
  note?: string
  /** 因**身体筛查**原定训练被封堵而降级为休息日时的原定日名 */
  blocked_from?: string
  /** 因**用户自己要求**（"今天改成休息日"）改为休息日时的原定日名 */
  rest_from?: string
  exercises: PlanExercise[]
}

export interface PlanExercise {
  name: string
  difficulty?: number
  /** 后端给的是区间字符串（"3-4"），不是数字——原样显示 */
  sets?: string
  reps?: string
  rest_sec?: string
  equipment?: string
  progression?: { status?: string; weight_kg?: number; reason?: string }
}

export interface PlanMacros {
  bmr?: number
  tdee?: number
  target_kcal?: number
  protein_g?: number
  fat_g?: number
  carbs_g?: number
}

export interface PlanScreening {
  /** `green` | `yellow` | `red` */
  level?: string
  level_label?: string
  warnings?: string[]
}

/** 计划的全部内容（后端 `content` 字段，原样）。 */
export interface PlanContent {
  profile?: Record<string, unknown>
  screening?: PlanScreening
  /** 频率/强度等通用处方要点 */
  fitt?: { resistance?: string[] }
  macros?: PlanMacros
  training?: { scheme?: string; items?: PlanDay[] }
}

export interface Plan {
  plan_id: string
  created_at: string | null
  content: PlanContent
}

export interface PlanResponse {
  user_id: string
  /**
   * `null` = **没有计划**（从没生成过），不是错误。界面据此显示"还没有计划"。
   * 与"请求失败"（抛错）是两件事，不要合并处理。
   */
  plan: Plan | null
  /** `true` = 图谱不可用，读不到计划；同样是可渲染状态，不是错误 */
  degraded: boolean
}

/** 取最新计划。网络/服务不可用时**抛错**由调用方决定怎么显示，不静默吞。 */
export async function fetchPlan(
  userId: string,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<PlanResponse> {
  const q = new URLSearchParams({ user_id: userId })
  const res = await fetchImpl(`/v1/plan?${q}`)
  if (!res.ok) throw new Error(`plan 请求失败：HTTP ${res.status}`)
  return (await res.json()) as PlanResponse
}

/** 训练日（`rest` 之外）——渲染表格时只需这些天。 */
export function trainingDays(content: PlanContent | null | undefined): PlanDay[] {
  const items = content?.training?.items ?? []
  return items.filter((d) => d.type !== 'rest')
}

/** 计划的天数（训练日 + 休息日），用于"共 N 天"这类概述。 */
export function planDayCount(content: PlanContent | null | undefined): number {
  return (content?.training?.items ?? []).length
}
