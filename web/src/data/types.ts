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
