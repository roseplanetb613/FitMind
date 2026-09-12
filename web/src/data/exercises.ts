/**
 * 动作推荐：点一块肌肉 → 看该练什么。
 *
 * 数据来自 `GET /v1/muscle-exercises`，而那背后是 **`ExerciseRepo.recommend`**
 * （变体族去重 + 主目标优先 + 逐级放宽），本文件**不做任何推荐逻辑**，只做搬运。
 *
 * ⚠ **媒体版权**：`gif_url` / `image` 指向的素材 **© Gym visual，商用需另行取得授权**
 * （见 `data/exercises-dataset/docs/HANDOVER.md`）。当前仅用于非商业演示。
 */

export interface ExercisePick {
  id: string
  name: string
  name_zh: string
  /** 相对 `data/exercises-dataset/` 的路径，如 `videos/0168-xxx.gif` */
  gif_url: string | null
  image: string | null
  exercise_type?: string
  difficulty?: number
  /** 该动作对这块肌肉是**主练**还是仅协同 */
  role: 'target' | 'synergist'
}

export interface MuscleExercises {
  muscle: string
  count: number
  /**
   * `true` = 推荐时**放宽过条件**（含最后一步放开拉伸类）。
   * 实测 `tibialis_anterior` 全库只有 1 个动作、`levator_scapulae` 2 个 ——
   * **不足 3 条是常态**，界面要能显示"只有这么多"，不要补占位。
   */
  fallback: boolean
  exercises: ExercisePick[]
}

/** 媒体相对路径 → 可取的 URL。后端把它挂在与 `/v1` 同源的 `/media` 下。 */
export function mediaUrl(rel: string | null | undefined): string | null {
  if (!rel) return null
  // 已经是绝对 URL 就别再拼（数据可能来自别处）
  if (/^https?:\/\//i.test(rel)) return rel
  return `/media/${rel.replace(/^\/+/, '')}`
}

/** 取这块肌肉的推荐动作。后端不可用时**抛错由调用方决定怎么显示**，不静默吞。 */
export async function fetchPicks(muscleId: string, limit = 3): Promise<MuscleExercises> {
  const q = new URLSearchParams({ muscle: muscleId, limit: String(limit) })
  const res = await globalThis.fetch(`/v1/muscle-exercises?${q}`)
  if (!res.ok) {
    throw new Error(`muscle-exercises 请求失败：HTTP ${res.status}`)
  }
  return (await res.json()) as MuscleExercises
}
