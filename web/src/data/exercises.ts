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

/**
 * 媒体素材的署名。**授权要求每次使用都要带**（见
 * `data/exercises-dataset/NOTICE.md`）—— 凡是在界面上画了动作图示的地方，
 * 就得把这一行挂上去，不是"可选的礼貌"。
 *
 * 措辞与 NOTICE 里的版权声明逐字一致，别改写（改了就换了法律含义）。
 */
export const MEDIA_CREDIT = '© Gym visual — https://gymvisual.com/'

/**
 * 器械 slug → 中文。
 *
 * 取值域是 `exercise_metadata.json` 里 `normalized_equipment` 的**全部 9 个值**，
 * 不是猜的（`lib/exercise_repo.py` 只做同义归一，不做翻译）。
 * 表里没有的原样返回 —— 未来数据加了新器械时显示英文 slug，
 * 总好过显示一个编出来的中文名。
 */
const EQUIPMENT_ZH: Record<string, string> = {
  band: '弹力带',
  barbell: '杠铃',
  'body weight': '自重',
  cable: '绳索',
  dumbbell: '哑铃',
  kettlebell: '壶铃',
  machine: '器械',
  other: '其它',
  weighted: '负重',
}

export function equipmentLabel(slug: string | null | undefined): string | null {
  if (!slug) return null
  const k = slug.trim().toLowerCase()
  return EQUIPMENT_ZH[k] ?? k
}

/**
 * 难度 → 中文。库里 `difficulty` 是 1/2/3，`difficulty_label` 是英文
 * （beginner / intermediate / advanced，实测 1036 / 241 / 47 条）。
 *
 * 认不出的值返回 None —— **不猜**：写个"中级"上去，和"确实是中级"读不出区别。
 */
const DIFFICULTY_ZH: Record<string, string> = { '1': '新手', '2': '进阶', '3': '高级' }

export function difficultyLabel(d: number | string | null | undefined): string | null {
  if (d === null || d === undefined) return null
  const k = String(d).trim()
  if (!k) return null
  return DIFFICULTY_ZH[k] ?? null
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
