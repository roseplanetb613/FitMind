import type { MuscleMapSource } from './source'
import type { MuscleMapData } from './types'

export class ApiSource implements MuscleMapSource {
  constructor(
    private userId: string,
    /** 默认 7；后端 GET /v1/muscle-map 同为 7 —— 改一处须改两处 */
    private days = 7,
  ) {}

  async fetch(): Promise<MuscleMapData> {
    const q = new URLSearchParams({ user_id: this.userId, days: String(this.days) })
    const url = `/v1/muscle-map?${q}`
    let res: Response
    try {
      res = await globalThis.fetch(url)
    } catch (cause) {
      // 网络层失败（后端没起 / DNS / CORS）——带上 URL 与用户，否则日志里定位不到是谁的请求
      throw new Error(`muscle-map 请求失败：${url}（网络层）：${cause}`)
    }
    if (!res.ok) {
      throw new Error(`muscle-map 请求失败：${url} HTTP ${res.status}`)
    }
    try {
      return (await res.json()) as MuscleMapData
    } catch (cause) {
      // 200 但 body 非 JSON（SPA fallback 返回 index.html 是典型场景）
      throw new Error(
        `muscle-map 响应不是 JSON：${url} HTTP ${res.status} content-type=${res.headers.get('content-type')}`,
      )
    }
  }
}
