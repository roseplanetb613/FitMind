import type { MuscleMapSource } from './source'
import type { MuscleMapData } from './types'

export class ApiSource implements MuscleMapSource {
  constructor(
    private userId: string,
    private days = 7,
  ) {}

  async fetch(): Promise<MuscleMapData> {
    const q = new URLSearchParams({ user_id: this.userId, days: String(this.days) })
    const res = await globalThis.fetch(`/v1/muscle-map?${q}`)
    if (!res.ok) {
      throw new Error(`muscle-map 请求失败：HTTP ${res.status}`)
    }
    return (await res.json()) as MuscleMapData
  }
}
