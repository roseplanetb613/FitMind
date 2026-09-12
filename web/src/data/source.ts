import type { MuscleMapData } from './types'

/** 前端只认这个接口，不认数据从哪来——便于测试注入假实现。 */
export interface MuscleMapSource {
  fetch(): Promise<MuscleMapData>
}
