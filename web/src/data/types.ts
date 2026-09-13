/** 与后端 GET /v1/muscle-map 的契约一一对应（spec §3.3）。 */
export interface MuscleState {
  recovery: number
  confidence: 'high' | 'low'
  last_trained: string
  sessions: number
  has_record: boolean
  /** 措辞单源：后端用 recovery.describe() 算好，前端只显示（spec §5.3） */
  describe: string
  /** 整数百分比，**由后端算好**（与 describe 同口径）。
   *  前端**不得**自己 Math.round —— Python 的 round() 是 half-even、JS 是 half-up，
   *  0.245 会得出 24 vs 25，导致同一屏上标签与详情矛盾。 */
  pct: number
}

export interface MuscleMapData {
  generated_at: string
  days: number
  labels: Record<string, string>
  /** 28 项定长；无记录为 null（不是缺键） */
  muscles: Record<string, MuscleState | null>
  /** 图不可用时为 true（后端降级，不是错误） */
  degraded?: boolean
}

/**
 * 标签降级：labels_from_repo() 在 Neo4j 不可达时返回 {}，
 * 此时回落显示 muscle_id 而不是空白——**不得因为缺标签就丢块**（spec §3.3）。
 */
export function resolveLabel(labels: Record<string, string>, id: string): string {
  return labels[id] ?? id
}

/**
 * 缺键与显式 null 都归一成 null——§5.4 的未知态不区分二者。
 * 必要性：`Record<string, MuscleState | null>` 允许缺键，而 tsconfig 没开
 * `noUncheckedIndexedAccess`，所以 `muscles[id]` 的类型是 `MuscleState | null`
 * 但缺键时运行时是 `undefined` —— 不归一的话 `if (st === null)` 会把
 * `undefined` 当成"有数值"走进 recovery 分支并抛 TypeError，而不是画成线框。
 */
export function muscleState(d: MuscleMapData, id: string): MuscleState | null {
  return d.muscles[id] ?? null
}

/**
 * 全未知地图：`muscles` 里**每个 id 都缺键**（不是显式的 null 值）→ muscleState
 * 一律归一成 null → palette(null) → 线框 + 半透明（spec §5.4）。
 *
 * 它是加载失败路径的输入（见 data/load.ts）。少了它，applyStates 就没有数据可喂，
 * 50 块会停在 build() 的初始材质上——而那与 palette(0.5) 视觉等价
 * （同基色 BASE_COLOR、emissiveIntensity 同为 0、同为实心、opacity 同为 1），
 * 整屏看起来像"所有肌群恰好恢复 50%"，正是 §5.4 要排除的混淆。
 *
 * `generated_at` 是空串：没有生成过任何数据，不编造时间戳。`days` 原样带上，
 * 保持 MuscleMapData 的字段完整（消费方只读 muscles/labels）。
 */
export function emptyMap(days: number): MuscleMapData {
  return { generated_at: '', days, labels: {}, muscles: {} }
}

// ———————————————————————————————————————————————— 对话（/v1/chat）——

/**
 * `structured.data` —— **形状随意图而变，不能假设有 items**。
 *
 * 后端 `build_structured` 只是个信封，不按分支派发；实测这些情况都没有 items：
 *   · `guard` / `clarify` 两种 mode 的 structured **连 data 键都没有**
 *   · `smalltalk` 的 data 是 `{message, topic}`
 *   · 失败时是 `error` 而不是 `data`
 * 所以消费方一律逐级容忍，缺什么就当没有。
 */
export interface StructuredData {
  items?: StructuredItem[]
  /** 打卡消歧：让用户从库内真实动作里挑一个（见后端 clarify_exercise 节点）。
   *  **已经按"最近 30 天练过的"排好序** —— 前端不再排，避免两处口径。 */
  options?: StructuredOption[]
  /** 消歧的原始片段（如"完腿"），仅用于展示它到底没对上什么 */
  raw?: string
  [k: string]: unknown
}

/** 消歧选项。`recent_count` 是用户近 30 天练过这个动作的次数。 */
export interface StructuredOption {
  id: string
  name_zh: string
  equipment?: string | null
  difficulty?: number | string | null
  recent_count?: number
}

export interface StructuredItem {
  name?: string
  value?: string
  /** `teach` 分支独有：**canonical 肌肉 id**（如 `quadriceps`），用来联动 3D 视图。
   *  其它分支没有这个字段 —— 见 `muscleIdInItem` 的兜底策略。 */
  target?: string
  [k: string]: unknown
}

export interface Structured {
  title?: string
  sources?: string[]
  data?: StructuredData
  /** 技能失败时的原因（此时没有 data） */
  error?: string
  memory_ack?: string[]
}

/** 风险拦截负载。后端 `ChatResponse.guard` 一直算着，此前只是没序列化。 */
export interface GuardPayload {
  blocked?: boolean
  level_label?: string
  advice?: string
  blocks?: unknown[]
}
