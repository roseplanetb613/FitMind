/**
 * 语音转写通道：`POST /v1/asr`（multipart 上传音频）与 `GET /v1/asr/status`。
 *
 * 与其他 data 模块同一约定：URL 是**根绝对路径** `/v1/...`，不带 `/app/` 前缀
 * （`base: '/app/'` 只作用于静态资源；dev 由 `vite.config.ts` 的 proxy 转发）。
 *
 * `fetchImpl` 可注入 —— vitest 跑在 node 里（没有 jsdom，见 tests/dom-stub.ts），
 * 测试靠它塞假响应，不真发请求。
 */

export interface AsrResult {
  text: string
  language: string
  /** 音频时长（秒），由后端算出 */
  duration: number
}

export interface AsrStatus {
  /** 能转写吗 —— **前端只该看这一个字段**决定要不要渲染麦克风按钮 */
  available: boolean
  enabled: boolean
  model: string
  model_exists: boolean
  /** 模型是否已常驻显存（首次调用前为 false，仅供排障） */
  loaded: boolean
  device: string | null
  error: string | null
}

/** 探活失败时按"不可用"处理，绝不因此抛错 —— 麦克风只是可选输入方式，
 *  转写服务挂掉不该让整个对话面板显示错误。 */
export const ASR_UNAVAILABLE: AsrStatus = {
  available: false,
  enabled: false,
  model: '',
  model_exists: false,
  loaded: false,
  device: null,
  error: null,
}

export async function asrStatus(
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<AsrStatus> {
  const url = '/v1/asr/status'
  try {
    const res = await fetchImpl(url)
    if (!res.ok) return ASR_UNAVAILABLE
    return (await res.json()) as AsrStatus
  } catch {
    // 网络层失败（后端没起）→ 不可用。**不抛**：调用方在启动时探活，
    // 抛出去会变成一个没人处理的 rejection，而结果和"不可用"完全一样。
    return ASR_UNAVAILABLE
  }
}

/**
 * 上传一段音频，拿回文本。
 *
 * 失败一律**抛错**，不返回空文本 —— 调用方据错误给提示。
 * 「转写成功但没有语音」是 **200 + 空 text**，与失败是两回事：
 * 前者提示"没听清，再说一次"，后者要显示真实原因（未启用/模型没加载）。
 */
export async function postAsr(
  blob: Blob,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<AsrResult> {
  const url = '/v1/asr'
  const form = new FormData()
  // 文件名必须带后缀：后端拿它当临时文件的扩展名，ffmpeg 靠扩展名认容器格式
  // （见 app/server.py 里 suffix 那段）。MediaRecorder 的 blob.type 形如
  // `audio/webm;codecs=opus`，取分号前那段来推后缀。
  const ext = (blob.type.split(';')[0]?.split('/')[1] || 'webm').trim()
  form.append('file', blob, `voice.${ext}`)

  let res: Response
  try {
    res = await fetchImpl(url, { method: 'POST', body: form })
  } catch (cause) {
    throw new Error(`语音转写失败（网络层）：${String(cause)}`, { cause })
  }
  if (!res.ok) {
    // 后端的 4xx/5xx 都带一句给人看的中文（"没说话就结束了？"/"未启用"），
    // 它比 `HTTP 422` 有用得多。取不到才回落到状态码。
    let detail = ''
    try {
      const body = (await res.json()) as { error?: string }
      detail = body?.error ?? ''
    } catch {
      detail = ''
    }
    throw new Error(detail || `语音转写失败：HTTP ${res.status}`)
  }
  return (await res.json()) as AsrResult
}
