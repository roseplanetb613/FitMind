/**
 * 食物识别通道：`POST /v1/vision/food`（multipart 上传图片）与
 * `GET /v1/vision/food/status`。
 *
 * URL 是**根绝对路径** `/v1/...`，不带 `/app/` 前缀 —— 与 `asr.ts` / `api.ts`
 * 同一约定（`base: '/app/'` 只作用于静态资源；dev 由 `vite.config.ts` 的 proxy 转发）。
 *
 * `fetchImpl` 可注入 —— vitest 跑在 node 里（没有 jsdom），测试靠它塞假响应。
 */

export interface FoodNutrition {
  per_serving: Record<string, number | null>
  per_100g: Record<string, number | null>
  breakdown: Array<Record<string, unknown>>
  /** 没在成分库里找到、**未计入**的食材。非空时总热量是下界。 */
  missing: Array<{ name: string; grams: number }>
  /** 命中但缺值的食材 */
  incomplete: Array<{ name_zh: string; missing_fields: string[] }>
  total_grams: number
}

export interface FoodCard {
  ok: boolean
  /** 菜品库命中时是菜品 id；走兜底路径（VLM 拆食材）时为 null */
  dish_id: string | null
  dish: string
  portion_g: number | null
  confidence?: number | null
  method?: string
  nutrition?: FoodNutrition
  allergens?: string[]
  provenance?: string[]
  /** 后端算好的提示（"按 250g 折算""以下食材未计入"），**原样显示不要改写** */
  warnings?: string[]
  error?: string
}

export interface VisionStatus {
  /** 能识别吗 —— **前端只该看这一个字段**决定要不要渲染相机按钮 */
  available: boolean
  enabled: boolean
  model: string
  key_configured: boolean
  error: string | null
}

/** 探活失败时按"不可用"处理，绝不因此抛错 —— 拍照只是可选输入方式，
 *  识别服务挂掉不该让整个对话面板显示错误。 */
export const VISION_UNAVAILABLE: VisionStatus = {
  available: false, enabled: false, model: '', key_configured: false, error: null,
}

export async function visionStatus(
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<VisionStatus> {
  try {
    const res = await fetchImpl('/v1/vision/food/status')
    if (!res.ok) return VISION_UNAVAILABLE
    return (await res.json()) as VisionStatus
  } catch {
    // 网络层失败（后端没起）→ 不可用。**不抛**：调用方在启动时探活，
    // 抛出去会变成一个没人处理的 rejection，而结果和"不可用"完全一样。
    return VISION_UNAVAILABLE
  }
}

/**
 * 上传一张餐食照片，拿回营养卡片。
 *
 * 失败一律**抛错**，不返回空卡片 —— 调用方据错误给提示。
 * 「照片里没看出食物」也是失败（后端 200 + ok:false），因为对用户来说
 * 那和"识别失败"是同一件事：都需要换一张。
 */
export async function postFoodPhoto(
  file: File,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<FoodCard> {
  const form = new FormData()
  form.append('file', file, file.name || 'photo.png')

  let res: Response
  try {
    res = await fetchImpl('/v1/vision/food', { method: 'POST', body: form })
  } catch (cause) {
    throw new Error(`图片上传失败（网络层）：${String(cause)}`, { cause })
  }

  // 后端的 4xx/5xx 都带一句给人看的中文，它比 `HTTP 422` 有用得多。
  let body: FoodCard | null = null
  try {
    body = (await res.json()) as FoodCard
  } catch {
    body = null
  }
  if (!res.ok) throw new Error(body?.error || `食物识别失败：HTTP ${res.status}`)
  if (!body?.ok) throw new Error(body?.error || '食物识别失败')
  return body
}
