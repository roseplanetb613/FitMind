/**
 * 训练进行中的游标持久化（localStorage）。
 *
 * ## ⚠ 先说清它**不是**什么
 *
 * 它**不是**"哪些动作已经写回过"的权威来源 —— 图谱才是。但它确实是前端**唯一**
 * 可见的那份记录：没有任何接口能问"今天哪些动作已经写回过"
 * （`/v1/muscle-map` 只给**肌群**恢复度、粒度是 `muscle_id` 不是 `exercise_id`；
 * `/v1/plan` 只给计划）。所以 `doneIds` 一丢，已写回的动作会被**再写一遍**，
 * 而重复打卡会污染恢复度（肌群恢复全靠打卡算）—— 这是规格 §4.6 明确接受的
 * 取舍：复发条件很窄（用户主动清站点数据 / 换浏览器，且发生在一次训练中途），
 * 而补它要新增一个端点 + 一条读路径。
 *
 * ⚠ 规格 §4.6 更正过一处想当然：**不能说"真相在图谱，本地丢了也不会重复记录"**。
 * 前端没有任何接口能查这件事，所以它会重复。这条注释留着，免得日后又有人这么推。
 *
 * ## 两条硬不变式
 *
 * 1. **`doneIds` 与游标同一次写入落盘** —— 分两次写就会出现"动作标为已完成、
 *    但其实没写回"（或反之）。所以对外只有一个 `save()`，它只调一次 `setItem`。
 * 2. **对不上就当作无进度，不猜** —— `v` / 日期 / `planId` 任一不符，或 JSON 坏，
 *    一律返回 `null` 从头开始。按错误游标"续"比重新开始糟得多（会跳过动作）。
 *
 * ## 全降级
 *
 * 隐私模式下 `localStorage` 的读写都会抛。这里一律吞掉：读不到 = 没有进度，
 * 写不进 = 这次训练不跨刷新恢复。绝不因为存储不可用而拦住训练本身。
 */

/** 格式版本。**不匹配就当没进度** —— 宁可重来，也不要按错误游标续。 */
export const STORE_VERSION = 1

export const STORE_KEY = 'fitmind.workout.progress'

/** 只持久化"进行中"的两个状态（`idle` / `finished` 不落盘，见规格 §4.6）。 */
export const PERSISTED_STATES = ['working', 'resting'] as const

export interface WorkoutProgress {
  v: number
  /** 本地日期 `YYYY-MM-DD`。跨日作废（昨天的游标续到今天会跳过动作） */
  date: string
  planId: string
  exIdx: number
  setIdx: number
  /** `working` | `resting` */
  state: string
  /** 已**成功写回**的动作键（见 `exerciseKey`） */
  doneIds: string[]
  /**
   * 间歇的**绝对**结束时刻（毫秒）。带了它，切后台/刷新后剩余时间是准的
   * （状态机用绝对时间戳而不是自增计数器，见 `workout-session.ts`）。
   */
  restEndsAt?: number
}

/** `localStorage` 的最小接口（注入以便测试；生产直接传 `localStorage`）。 */
export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

/**
 * 动作在"已写回"集合里的键。
 *
 * 优先用 `id`（跨设备/跨名字改动都稳定）；老计划没有 `id` 时退回名字 ——
 * 这种计划写到**本机**的 localStorage 里，名字就是它当时的样子，够用。
 */
export function exerciseKey(ex: { id?: string; name: string }): string {
  return ex.id ? `id:${ex.id}` : `name:${ex.name}`
}

/**
 * **本地**日期 `YYYY-MM-DD`。
 *
 * ⚠ 不能用 `new Date().toISOString().slice(0,10)` —— 那是 **UTC** 日期，
 * 东八区在 08:00 之前会算成前一天，于是"今天练到一半"的进度在早上打开时
 * 被判成"昨天的，作废"。这类跨日 bug 只在特定时段复现，最难查。
 */
export function localDateISO(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 读回来是不是一个可用的游标。字段类型也查 —— 手改/版本升级都会造出畸形值。 */
function isUsable(p: unknown, today: string, planId: string): p is WorkoutProgress {
  if (!p || typeof p !== 'object') return false
  const o = p as Record<string, unknown>
  if (o.v !== STORE_VERSION) return false
  if (o.date !== today) return false
  if (planId && o.planId !== planId) return false
  if (!PERSISTED_STATES.includes(o.state as (typeof PERSISTED_STATES)[number])) return false
  if (!Number.isInteger(o.exIdx) || (o.exIdx as number) < 0) return false
  if (!Number.isInteger(o.setIdx) || (o.setIdx as number) < 0) return false
  if (!Array.isArray(o.doneIds)) return false
  if (o.restEndsAt !== undefined && !Number.isFinite(o.restEndsAt)) return false
  return true
}

/**
 * 读进度。**任何一处对不上就返回 `null`（= 从头开始），绝不猜。**
 *
 * 调用方拿到非 null 后要向用户确认一句"继续上次训练？"再 `session.resume()` ——
 * 这个函数只回答"有没有可续的进度"，不替用户做决定。
 */
export function loadProgress(
  storage: StorageLike | null | undefined,
  opts: { today: string; planId: string },
): WorkoutProgress | null {
  if (!storage) return null
  let raw: string | null = null
  try {
    raw = storage.getItem(STORE_KEY)
  } catch {
    return null                       // 隐私模式：读不到就是没有进度
  }
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null                       // 坏 JSON（被谁写脏了）→ 当无进度
  }
  if (!isUsable(parsed, opts.today, opts.planId)) return null
  const p = parsed as WorkoutProgress
  // 归一 `doneIds` 的元素类型：手改过 / 别的版本写过都可能混进非字符串
  return { ...p, doneIds: p.doneIds.filter((k): k is string => typeof k === 'string') }
}

/**
 * 写进度。**一次 `setItem`** —— `doneIds` 与游标必须同一次落盘，分两次写就会出现
 * "标为已完成但其实没写回"（那条不变式见模块注释）。
 *
 * 返回是否真的写成功：调用方**不需要**据此改变行为（全降级），但测试与诊断要看。
 */
export function saveProgress(
  storage: StorageLike | null | undefined,
  progress: WorkoutProgress,
): boolean {
  if (!storage) return false
  try {
    storage.setItem(STORE_KEY, JSON.stringify(progress))
    return true
  } catch {
    return false                      // 配额满 / 隐私模式：这次训练不跨刷新恢复
  }
}

/** 清掉进度（练完 / 提前结束 / 用户放弃）。存储不可用时静默失败。 */
export function clearProgress(storage: StorageLike | null | undefined): void {
  if (!storage) return
  try {
    storage.removeItem(STORE_KEY)
  } catch {
    /* 清不掉也无所谓：下次 `loadProgress` 会因为状态或日期对不上而作废 */
  }
}
