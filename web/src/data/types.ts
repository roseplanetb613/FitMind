/** 与后端 GET /v1/muscle-map 的契约一一对应（spec §3.3）。 */
export interface MuscleState {
  recovery: number
  confidence: 'high' | 'low'
  last_trained: string
  sessions: number
  has_record: boolean
  /** 措辞单源：后端用 recovery.describe() 算好，前端只显示（spec §5.3） */
  describe: string
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
