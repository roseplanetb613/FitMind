/**
 * 训练计划字段解析：**区间字符串 → 具体数字**。
 *
 * ## 为什么需要它
 *
 * `GET /v1/plan` 里 `exercises[]` 的 `sets` / `rest_sec` **不是数字，是区间字符串**
 * （2026-09-18 实测计数）：
 *
 * | 字段 | 实际取值 |
 * |---|---|
 * | `sets` | `"3"` / `"3-4"` / `"4-5"` / `"2-3"` / **`"组"`（脏值）** |
 * | `rest_sec` | `"90-180"` / `"45-90"` / `"150-240"` / `"30-60"` / `"0-30"` |
 *
 * 执行台要迭代"第几组"、要跑组间歇倒计时 —— 都得先是数字。解析集中在这一个文件，
 * **不许在 UI 里散落 `parseInt`**：散落之后每处都要各自决定"区间取上限还是下限"、
 * "脏值怎么办"，而它们迟早会给出不一致的答案。
 *
 * ## 两条口径
 *
 * 1. **区间取下限。** 区间表达的是"建议范围"，下限是"至少歇这么久 / 至少做这么多"。
 *    取了上限就会让倒计时比用户实际需要的长（要一直等）。
 * 2. **解析器只做"字符串 → 数字"。** 比如"`rest_sec` 为 0 时跳过间歇阶段"这条
 *    判定**在状态机里**，不在这里特判 —— 那属于流程语义，不属于格式转换。
 *
 * ⚠ 脏值 `"组"` 说明数据侧没有强校验，所以**任意输入都必须有确定兜底**：
 * 不抛、不出 `NaN`（本仓"诚实数据"原则：拿不准就给确定值，并说得出它从哪来）。
 */
import type { PlanContent, PlanDay } from './plan'

/** `sets` 解析不出数字时的兜底组数（脏值 `"组"` 走这条）。 */
export const DEFAULT_SETS = 3

/** 组数下限 / 上限 —— 钳制区间，脏值不会让界面出现 200 组。 */
export const SETS_MIN = 1
export const SETS_MAX = 10

/**
 * 取串里**第一个数字**。
 *
 * `"3-4"` → 3、`"90-180"` → 90、`"0-30"` → 0、`"组"` → null。
 * 用"首个数字"而不是完整正则匹配区间，是因为实测那些值的长相并不统一
 * （`"15-30 或 30-60s"`、`"20-60s 保持"`），宽松取首数对它们都给出确定答案，
 * 而严格匹配会在没预料到的形态上掉进"解析失败"分支。
 *
 * 逗号/全角数字不管：动作库是数据文件，不是用户输入，没有全角数字。
 */
function firstNumber(raw: unknown): number | null {
  const s = String(raw ?? '').trim()
  if (!s) return null
  const m = /\d+/.exec(s)
  return m ? Number(m[0]) : null
}

/**
 * `sets` → 组数。区间取**下限**；脏值/空 → `DEFAULT_SETS`；钳到 `[1, 10]`。
 *
 * 返回类型是 `number` 而不是 `number | null`：组数是执行台**必须有**的量
 * （没它连"第几组"都显示不出来），所以这里给确定值而不是让调用方到处判空。
 */
export function parseSets(raw: unknown): number {
  const n = firstNumber(raw)
  if (n === null) return DEFAULT_SETS
  return Math.min(SETS_MAX, Math.max(SETS_MIN, n))
}

/**
 * `rest_sec` → 秒。区间取**下限**；没有数字 → `null`。
 *
 * ⚠ **`0` 与 `null` 必须分得开**：
 *   · `0`（来自 `"0-30"`）是"几乎不用歇" → 状态机据此**跳过**间歇阶段；
 *   · `null` 是"这个字段没法解析" → 不知道，状态机按"不猜一个数"处理。
 * 把两者合成一个 0，就等于把"算不出来"说成"不用歇"—— 本仓"缺值不能当 0"。
 */
export function parseRestSec(raw: unknown): number | null {
  const n = firstNumber(raw)
  if (n === null) return null
  return Math.max(0, n)
}

/** 执行台里一个动作。`sets` / `restSec` 已解析成数字。 */
export interface SessionExercise {
  /** 动作库 id。**老计划（本次改动之前生成的）没有** —— 那时写回走名字分支。 */
  id?: string
  name: string
  /** 已解析的组数，`1..10` */
  sets: number
  /**
   * **原样显示，不解析**：存在 `"20-60s 保持"` 这种时间型静力保持，
   * 把它当次数解析会得到"保持 20 次"这种假话。
   */
  reps?: string
  /** 已解析的组间歇秒数；`null` = 解析不出（与 `0` = 不用歇 不同） */
  restSec: number | null
  difficulty?: number
  equipment?: string
}

/** 预解析阶段就被挑出、**不进执行台**的动作。 */
export interface SkippedExercise {
  name: string
  /** 给用户看的原因（如实说明，不是"加载失败"这类含糊话） */
  reason: string
}

export interface SessionBuild {
  exercises: SessionExercise[]
  skipped: SkippedExercise[]
  /**
   * 其中几个动作**没有 id**（老计划）。不是错误、也不跳过 —— 写回会按名字走
   * `norm_zh` 精确匹配。UI 据此如实提一句，而不是假装一切照旧。
   */
  withoutId: number
}

/**
 * `PlanDay` → 执行台的输入（解析 + 挑脏值）。
 *
 * ⚠ **"没有 id" 不等于"记不上"**：计划里的 `name` 来自动作库检索命中的 `name_zh`，
 * 因此按名字走 `POST /v1/checkin/resolve` 时 `norm_zh` 精确匹配**必然命中**。
 * 真正记不上的是"连名字都没有"那一种（数据侧没有强校验，脏值确实存在），
 * 那种才进 `skipped`。
 */
export function buildSessionExercises(day: PlanDay | null | undefined): SessionBuild {
  const exercises: SessionExercise[] = []
  const skipped: SkippedExercise[] = []
  let withoutId = 0

  for (const raw of day?.exercises ?? []) {
    const name = String(raw?.name ?? '').trim()
    if (!name) {
      skipped.push({ name: '（无名动作）', reason: '计划里这一条没有动作名，记不上' })
      continue
    }
    if (!raw.id) withoutId += 1
    exercises.push({
      id: raw.id,
      name,
      sets: parseSets(raw.sets),
      reps: raw.reps,
      restSec: parseRestSec(raw.rest_sec),
      difficulty: raw.difficulty,
      equipment: raw.equipment,
    })
  }
  return { exercises, skipped, withoutId }
}

/**
 * 两个 `YYYY-MM-DD` 相差几天（**本地日历**，不是 UTC）。解析不出来 → null。
 *
 * ⚠ `new Date('2026-09-18')` 是 **UTC** 午夜（东八区 = 当天 08:00），直接相减会在
 * 跨时区/夏令时下差出一天。所以一律拼 `T00:00:00`（无 `Z` → 按**本地**时间解析）。
 * 最后 `Math.round` 兜掉夏令时导致的一天不是整 86400000 毫秒。
 */
export function dayDiff(fromISO: string, toISO: string): number | null {
  const a = new Date(`${fromISO}T00:00:00`)
  const b = new Date(`${toISO}T00:00:00`)
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null
  return Math.round((b.getTime() - a.getTime()) / 86_400_000)
}

export interface TodayPick {
  /** 今天要练的那一天；`null` = 今天没得练 */
  day: PlanDay | null
  kind: 'ok' | 'no_plan' | 'rest' | 'out_of_range'
  /** 没得练时给用户看的一句话（`kind === 'ok'` 时是空串） */
  message: string
}

/**
 * 从计划里挑出**今天**该练的那一天。
 *
 * ## 为什么用 `start_date` 算偏移，而不是认 `date` 里的"今天"
 *
 * `PlanDay.date` 是后端算好的中文串（`"今天（9月13日 周日）"`），而 `GET /v1/plan`
 * 返回的是**生成时的快照** —— 隔日打开，那个"今天"指的是**生成那天**，不是今天。
 * 唯一可靠的基准是 `content.start_date`（ISO）。这里只做**定位**，不重算那个中文串
 * （"后端算好的中文，前端不重算"这条约束仍然成立）。
 *
 * `start_date` 缺失（很老的计划）→ 按第 1 天算，保守但可用。
 */
export function pickTodayDay(
  content: PlanContent | null | undefined,
  todayISO: string,
): TodayPick {
  const items = content?.training?.items ?? []
  if (!items.length) {
    return {
      day: null,
      kind: 'no_plan',
      message: '还没有训练计划。在对话里说「帮我排一个一周四天的计划」就会生成。',
    }
  }
  const start = content?.start_date
  const diff = start ? dayDiff(start, todayISO) : null
  const idx = diff === null ? 0 : Math.max(0, diff)   // 计划还没开始 → 按第 1 天
  if (idx >= items.length) {
    // 不按周期取模续用：过期的计划继续照着做是**误导**（它当时算的疲劳/减量早就失效了）
    return {
      day: null,
      kind: 'out_of_range',
      message: `这份计划是从 ${start} 起的 ${items.length} 天，今天已经超出它的跨度了 —— 要我重排一版吗`,
    }
  }
  const day = items[idx]
  if (day.type === 'rest') {
    return {
      day: null,
      kind: 'rest',
      message: `${day.date || '今天'}是休息日${day.note ? `：${day.note}` : ''}。`,
    }
  }
  if (!(day.exercises ?? []).length) {
    return { day: null, kind: 'rest', message: `${day.date || '今天'}没有安排动作。` }
  }
  return { day, kind: 'ok', message: '' }
}
