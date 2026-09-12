import type { MuscleMapData } from './types'

/**
 * 前端消费数据的唯一缝：组件只认这个接口，不认数据从哪来。
 *
 * 当前唯一实现是 `ApiSource`；Task 9 的 loader 会以它为形参，
 * 届时测试可注入假实现而无需 stub `fetch`。
 */
export interface MuscleMapSource {
  fetch(): Promise<MuscleMapData>
}
