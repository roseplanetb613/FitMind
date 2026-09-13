/**
 * 与 agent 的对话通道。
 *
 * 两个函数对应后端两个端点：
 *   · `postChat`   → `POST /v1/chat`        一次给全（保底路径）
 *   · `streamChat` → `POST /v1/chat/stream` 先流**阶段**、最后给整包
 *
 * **为什么流的是阶段不是字**：后端 `_apply_post_render_guards` 会在 LLM 渲染
 * 之后整条丢弃并替换 reply（编造的身体数字 → 换成确定性文本）。逐字流等于先把
 * 那些会被丢掉的内容给用户看一遍。细节见 `app/core/progress.py`。
 *
 * **为什么不用 `EventSource`**：SSE 规范里 `EventSource` 只支持 GET，而我们要 POST
 * 一个 JSON body。所以这里用 `fetch` + `res.body.getReader()` 手动解帧。
 *
 * URL 是**根绝对路径** `/v1/...`，不带 `/app/` 前缀 —— 与 `api.ts` / `exercises.ts`
 * 同一约定（`base: '/app/'` 只作用于静态资源；API 挂在服务器根上，dev 由
 * `vite.config.ts` 的 proxy 转发）。
 */
import type { GuardPayload, Structured } from './types'

export interface ChatResponse {
  session_id: string
  reply: string
  mode_used?: string
  provenance?: string[]
  structured?: Structured
  /** 风险拦截负载。后端此前算好了却没序列化，现已补上；无拦截时为 null。 */
  guard?: GuardPayload | null
}

export interface ChatRequest {
  message: string
  session_id?: string | null
  user_id?: string
}

/**
 * 一次进度上报。
 *
 * `stage` 是粗粒度阶段；`skill` / `mode` 只在 `stage` 为 `skill` / `tool` 时出现，
 * 是**载荷**而不是拼进 stage 字符串里的（拼字符串再切成"改个技能名就静默错位"）。
 */
export interface ChatStage {
  stage: string
  /** 技能 / 工具名，如 `qa`、`split_cycle` */
  skill?: string
  /** 调用模式：direct / react / plan_exec / rewoo */
  mode?: string
}

/** 流里的一帧。`stage` 是进度，`done` 是最终整包，`error` 是后端捕获的异常。 */
export type ChatEvent =
  | ({ type: 'stage' } & ChatStage)
  | { type: 'error'; message: string }
  | ({ type: 'done' } & ChatResponse)

/**
 * SSE 字节流 → 一帧帧的 data 载荷。
 *
 * **必须自己缓冲**：一条事件可能被 TCP 切成两段（实测阶段事件很小，很容易跨
 * chunk 边界）。不做缓冲的话 `JSON.parse` 会拿到半截字符串直接抛错，
 * 表现是"偶尔丢一条阶段"——比整条流挂掉更难查。
 *
 * 返回一个"喂进 chunk、吐出完整帧"的闭包，于是这段逻辑可以脱离网络单测。
 */
export function createSseParser(): (chunk: string) => string[] {
  let buf = ''
  return (chunk: string) => {
    // 统一换行：SSE 允许 \r\n，不归一化的话 `\n\n` 的分帧判断会漏
    buf += chunk.replace(/\r\n/g, '\n')
    const out: string[] = []
    let i = buf.indexOf('\n\n')
    while (i >= 0) {
      const frame = buf.slice(0, i)
      buf = buf.slice(i + 2)
      for (const line of frame.split('\n')) {
        // 只认 data 行；`:` 开头的是注释（心跳/填充），其余字段（event/id）本项目不用
        if (line.startsWith('data: ')) out.push(line.slice(6))
        else if (line.startsWith('data:')) out.push(line.slice(5))
      }
      i = buf.indexOf('\n\n')
    }
    return out
  }
}

/** 把网络层与 HTTP 层的失败包成同一种错，风格对齐 `api.ts`（URL + 状态写进 message）。 */
async function request(
  url: string,
  req: ChatRequest,
  fetchImpl: typeof fetch,
): Promise<Response> {
  let res: Response
  try {
    res = await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(req),
    })
  } catch (cause) {
    // `{ cause }` 保留原始栈：String() 插值会把栈帧丢掉
    throw new Error(`对话请求失败：${url}（网络层）：${String(cause)}`, { cause })
  }
  if (!res.ok) throw new Error(`对话请求失败：${url} HTTP ${res.status}`)
  return res
}

export async function postChat(
  req: ChatRequest,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<ChatResponse> {
  const res = await request('/v1/chat', req, fetchImpl)
  return (await res.json()) as ChatResponse
}

/**
 * 流式对话：每收到一个阶段回调一次 `onStage`，返回最终整包。
 *
 * 失败一律**抛错**，不返回半个响应 —— 调用方（chat-flow）据此把错误显示出来，
 * 而不是把一个空壳当成正常回答渲染出去。
 */
export async function streamChat(
  req: ChatRequest,
  onStage: (stage: ChatStage) => void,
  fetchImpl: typeof fetch = globalThis.fetch,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const url = '/v1/chat/stream'
  let res: Response
  try {
    res = await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(req),
      ...(signal ? { signal } : {}),
    })
  } catch (cause) {
    throw new Error(`对话请求失败：${url}（网络层）：${String(cause)}`, { cause })
  }
  if (!res.ok) throw new Error(`对话请求失败：${url} HTTP ${res.status}`)
  if (!res.body) {
    // 老浏览器 / 非流式环境。明确报出来，别退化成"永远不返回"
    throw new Error('对话请求失败：响应没有可读流（该环境不支持流式读取）')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const feed = createSseParser()
  let done: ChatResponse | null = null
  let errMsg: string | null = null

  try {
    for (;;) {
      const { value, done: finished } = await reader.read()
      if (finished) break
      for (const payload of feed(decoder.decode(value, { stream: true }))) {
        let ev: ChatEvent
        try {
          ev = JSON.parse(payload) as ChatEvent
        } catch {
          continue // 坏帧跳过：丢一条阶段远好过整条流炸掉
        }
        // 传整个事件（含 skill/mode 载荷），不是只传 stage 字符串
        if (ev.type === 'stage') onStage(ev)
        else if (ev.type === 'done') done = ev
        else if (ev.type === 'error') errMsg = ev.message
      }
    }
  } finally {
    reader.releaseLock()
  }

  if (errMsg) throw new Error(`对话失败：${errMsg}`)
  if (!done) throw new Error('对话失败：流已结束但没有收到结果')
  return done
}

// ────────────────────────────────────────────── 打卡消歧补记 ──

export interface CheckinResolveRequest {
  user_id?: string
  exercise_id: string
  name_zh?: string
  raw?: string
  sets?: number
  reps?: number
  occurred_at?: string
}

export interface CheckinResolveResponse {
  ok: boolean
  event_id?: string
  exercise_id?: string
  name_zh?: string
  muscles?: string[]
  ack?: string
  error?: string
}

/**
 * 用户在消歧选择框里点定了一个动作 → 补记这次训练。
 *
 * **不走 `/v1/chat`**：这时已经确知点了哪个动作，再跑一遍 agent 既慢（2~5s）
 * 又可能再次落回消歧。后端直接建事件 + 肌群边。
 */
export async function postCheckinResolve(
  req: CheckinResolveRequest,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<CheckinResolveResponse> {
  const url = '/v1/checkin/resolve'
  const res = await request(url, req as unknown as ChatRequest, fetchImpl)
  return (await res.json()) as CheckinResolveResponse
}
