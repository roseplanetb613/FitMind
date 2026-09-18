/**
 * 训练执行台的**逐动作写回**：一个动作的所有组走完 → `POST /v1/checkin/resolve` 一次。
 *
 * ## 为什么粒度是"动作"而不是"组"
 *
 * `checkin` 的语义本来就是"一次打卡 = 一个动作"（`app/server.py` 的
 * `checkin_resolve`）。**每组写一次会凭空多出 N 条训练记录**，而肌群恢复度全靠
 * 打卡算 —— `server.py` 里那条注释警告的正是这件事（"写成打卡会凭空多出一次
 * 训练记录，污染恢复度"）。
 *
 * ## 为什么不复用 `data/chat.ts` 的 `postCheckinResolve`
 *
 * 那个函数在 HTTP 非 2xx 时**抛错且丢掉响应体**，只留一句 `HTTP 404`。
 * 而本端点的错误信息全在 body 里（`{"ok":false,"error":"动作库里没有「X」"}`）——
 * 规格 §4.9 要求"带后端给的 `error` 文案"如实汇报给用户。所以这里自己发请求、
 * 自己读 body；**端点、payload、口径都与它一致**（同一个 URL 同一套字段）。
 *
 * ## ⚠ 绝不弹消歧
 *
 * 后端在"库里没有完全同名的动作"时返回 `need_pick` + 候选列表。对话路径碰到它
 * 会弹选择框 —— **全屏跟练界面里不能这么干**：用户正在卧推架旁边，而且这是
 * 练到一半。规格 §4.9 定的口径是转成"记录未同步"，等结束一并如实汇报。
 */
import type { CheckinResolveResponse } from './chat'
import type { SessionExercise } from './plan-parse'

export type SyncFailReason =
  /** 后端要求消歧（库里没有完全同名）—— 不弹框，如实记成未同步 */
  | 'need_pick'
  /** 其它失败：5xx / 网络 / 后端给了 error 文案 */
  | 'error'

export type SyncOutcome =
  | { ok: true; name: string; exerciseId?: string }
  | { ok: false; name: string; reason: SyncFailReason; message: string }

export interface WriteDeps {
  userId: string
  /** 注入以便测试；生产用 `globalThis.fetch` */
  fetchImpl?: typeof fetch
}

/**
 * 写回一个动作。**永不抛** —— 网络层的异常也转成 `ok:false`（全降级：
 * 记录失败不该打断训练本身）。
 */
export async function writeExercise(
  ex: SessionExercise,
  doneSets: number,
  deps: WriteDeps,
): Promise<SyncOutcome> {
  const fetchImpl = deps.fetchImpl ?? globalThis.fetch
  const payload: Record<string, unknown> = {
    user_id: deps.userId,
    // 实际完成的组数（跳过/提前结束时可能小于计划组数）—— 记计划值就是在编数据
    sets: doneSets,
  }
  if (ex.id) {
    payload.exercise_id = ex.id
  } else {
    // 老计划没有 id → 按名字走。计划的 name 来自动作库检索命中的 name_zh，
    // 所以后端 `norm_zh` 精确匹配**必然命中**（见 plan-parse.ts 的说明）。
    payload.text = ex.name
  }
  // ⚠ `reps` **不传**：计划里它是区间字符串（`"5-12"`、`"20-60s 保持"`），
  // 而端点收的是 `int`。把 `"5-12"` 硬转成数字就是在编一个用户没做的次数。

  let res: Response
  try {
    res = await fetchImpl('/v1/checkin/resolve', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch (cause) {
    return { ok: false, name: ex.name, reason: 'error',
             message: `网络不可用：${String(cause)}` }
  }

  let body: CheckinResolveResponse | null = null
  try {
    body = (await res.json()) as CheckinResolveResponse
  } catch {
    body = null                        // 非 JSON 响应（网关错误页等）
  }

  // 先判 ok：后端返回 `ok:false` + `need_pick:true`，两者不是并列关系
  if (body?.ok) {
    return { ok: true, name: body.name_zh ?? ex.name,
             ...(body.exercise_id ?? ex.id
               ? { exerciseId: body.exercise_id ?? ex.id }
               : {}) }
  }
  if (body?.need_pick) {
    return { ok: false, name: ex.name, reason: 'need_pick',
             message: body.reply ?? `库里没有正好叫「${ex.name}」的动作` }
  }
  return { ok: false, name: ex.name, reason: 'error',
           message: body?.error ?? `写入失败（HTTP ${res.status}）` }
}

export interface SyncSummary {
  /** 成功写回的动作数 */
  ok: number
  /** 没写上的动作名（去重后，供 UI 列出） */
  failedNames: string[]
  /**
   * 给用户看的一句话。**没有任何失败时是 `null`** —— 不要用一句"全部成功 ✓"
   * 去占地方（用户会以为有什么值得一提）。
   */
  notice: string | null
}

/**
 * 汇总一轮的写回结果。**这是"状态汇报"，不是"内容措辞"** ——
 * 规格 §4.8 说的"不在前端拼措辞"指的是训练总结（那段由 Agent 结合图谱数据生成），
 * 而"有几个动作没记上"是 Agent 无从得知的运行状态，必须由前端如实说。
 */
export function summarizeSync(outcomes: SyncOutcome[]): SyncSummary {
  const failed = outcomes.filter((o): o is Extract<SyncOutcome, { ok: false }> => !o.ok)
  const ok = outcomes.length - failed.length
  const failedNames = [...new Set(failed.map((f) => f.name))]
  if (!failedNames.length) return { ok, failedNames, notice: null }
  const head = failedNames.length === 1
    ? `有 1 个动作（${failedNames[0]}）没能记录到图谱`
    : `有 ${failedNames.length} 个动作没能记录到图谱`
  // 原因给一条就够（多半是同一种：图谱离线 / 库里没有同名）
  return { ok, failedNames, notice: `${head}：${failed[0].message}。这次训练照常算完成。` }
}
