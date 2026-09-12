import type { MuscleMapSource } from './source'
import { emptyMap, type MuscleMapData, type MuscleState } from './types'

/** 失败提示文案。它是一句**承诺**——"显示为全部未知态"由 loadInto 的空数据兑现，
 *  所以文案与兑现它的那行代码放在同一个文件里（改一处必须能看见另一处）。 */
export const LOAD_ERROR_TEXT = '数据加载失败，显示为全部未知态'

/**
 * 一次加载的下游。抽成接口只有一个理由：**让失败路径在 node 里可断言**。
 * main.ts 自己依赖 DOM 与 WebGL，它的 refresh 测不到；这里注入替身后，
 * "失败时到底把什么喂给了材质"就成了可以直接断言的事实。
 *
 * 形状与真实实现一一对应：`scene.applyStates` / `labelLayer.setStates` /
 * `createLoadErrorNotice.show|clear`。
 */
export interface LoadTarget {
  /** 数据 → 材质（绝不重建几何） */
  applyStates(data: MuscleMapData): void
  /** 数据 → 标签状态 */
  setLabels(states: Record<string, MuscleState | null>): void
  /** 展示加载失败提示。实现必须幂等——refresh 每 60s 一次，追加式渲染会堆叠 */
  showError(message: string): void
  /** 撤下加载失败提示。没有提示时也必须是安全的空操作 */
  clearError(): void
  /** 缓存本轮数据（点选详情读它）；失败时必须以 null 调用 */
  setLatest(data: MuscleMapData | null): void
}

/**
 * 拉一次数据并送到下游。成功与失败**都必须走材质通道**。
 *
 * 失败分支的四件事，理由是同一件事的两面（spec §5.4 "未知不得与任何数值混淆"）：
 *  - `applyStates(emptyMap(days))`：不调用的话 50 块停在 build() 的初始材质上，
 *    而那与 palette(0.5) 视觉等价（同基色 BASE_COLOR / emissiveIntensity 同为 0 /
 *    同为实心 / opacity 同为 1）——整屏看起来像"所有肌群恰好恢复 50%"。
 *  - `setLatest(null)`：不清的话点选详情还读得到上一轮的旧数据，与刚置为未知的
 *    材质/标签在同一屏上打架。
 *  - `setLabels({})`：标签改口说"无记录"（与材质同一时刻、同一事实）。
 *  - `showError(...)`：图例里说明原因，且不再是"每 60s 叠一条"。
 *
 * `emptyMap` 给的是**缺键**而非 null 值，muscleState 把两者一并归一（见 types.ts）。
 *
 * 顺序是有意的：先 `setLatest(null)`，下游（main.ts）会在那一刻撤下已打开的详情浮层
 * 并清掉"当前选中"。这样随后 applyStates/setLabels 再跑时，选中已经是空的——
 * 否则 setLabels 会拿"还没清掉的选中 + 还没作废的 latest"把浮层又填回旧数值，
 * 再被 setLatest(null) 关掉（一帧的闪烁）。
 */
export async function loadInto(
  source: MuscleMapSource,
  days: number,
  target: LoadTarget,
): Promise<void> {
  try {
    const data = await source.fetch()
    target.setLatest(data)
    target.applyStates(data)
    target.setLabels(data.muscles)
    target.clearError()
  } catch (err) {
    // 降级可见，不静默（与全仓 diag 留痕一致）
    console.error('肌群数据加载失败', err)
    target.setLatest(null)
    target.applyStates(emptyMap(days))
    target.setLabels({})
    target.showError(LOAD_ERROR_TEXT)
  }
}
