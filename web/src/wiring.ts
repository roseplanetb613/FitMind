import type * as THREE from 'three'
import type { LoadTarget } from './data/load'
import { resolveLabel, type MuscleMapData, type MuscleState } from './data/types'
import type { LoadErrorNotice } from './ui/notice'
import { applyStates, setHover } from './render/scene'

/**
 * `LoadTarget` 的真实装配：把一次加载的结果接到 场景材质 / 标签 / 失败提示 / 数据缓存。
 *
 * 抽成工厂（而不是写在 main.ts 里）是为了让下面这条**承重行为**能在 node 里断言：
 * `applyStates` 会把基色写回 palette 的常量，于是**抹掉悬停高亮**；而 main.ts 的
 * `setHovered` 有 `id === hovered` 的早退守卫（指针不动就不会再触发），所以刷新后不
 * 重放一次，高亮就**一直回不来**——要等指针挪到别的块上才恢复。失败路径走的是同一条
 * 材质通道，所以也要重放（失败时"50 块全未知"与"悬停的那块仍高亮"不矛盾）。
 *
 * `getHovered` 是 **getter 而不是值**：重放必须读"此刻"的悬停，用构造时的快照
 * 会把高亮钉死在装配那一刻的状态上。
 */
export interface LoadTargetDeps {
  /** 场景里那 50 块肌肉的 Group */
  body: THREE.Group
  labels: { setStates(states: Record<string, MuscleState | null>): void }
  notice: LoadErrorNotice
  /** 当前悬停的 muscleId（没有则 null）。每次重放时读取 */
  getHovered(): string | null
  /** 数据缓存（详情浮层读它）；失败时以 null 调用 */
  setLatest(data: MuscleMapData | null): void
  /** 标签状态落地之后（详情浮层用同一轮数据重画） */
  afterLabels(): void
}

/**
 * 肌群中文名的"最后已知值"。**名称与数值分开**：数值（latest）在整轮失败时必须作废
 * （否则详情还读得到旧恢复度），而名称不是数值、也不含恢复语义（spec §5.4 管的是
 * "数值不得被读成恢复度"），所以整轮失败时保留它 —— 失败态的标签因此是
 * "股四头肌 无记录"而不是回落到英文 id。
 *
 * 替换而不是合并：成功响应里的 labels 就是这一轮的唯一权威，它降级成 `{}` 时
 * 照样回落显示 id（§3.3 的降级行为原样保留）。
 */
export function createLabelNames(): {
  /** 每轮数据落地时调用：`null`（整轮失败）时保留上一批名称，成功时整体替换 */
  update(data: MuscleMapData | null): void
  /** 渲染时调用：名称缺失的 id 回落成 id 本身（spec §3.3） */
  resolve(id: string): string
} {
  let names: Record<string, string> = {}
  return {
    update(data): void {
      if (data !== null) names = data.labels
    },
    resolve(id): string {
      return resolveLabel(names, id)
    },
  }
}

export function createLoadTarget(deps: LoadTargetDeps): LoadTarget {
  return {
    applyStates: (data) => {
      applyStates(deps.body, data)
      // 必须紧跟在 applyStates 之后：它刚把基色写成 palette 的常量，这里再按当前
      // 悬停重放一次高亮（见模块注释里"高亮回不来"的那段）。
      setHover(deps.body, deps.getHovered())
    },
    setLabels: (states) => {
      deps.labels.setStates(states)
      deps.afterLabels()
    },
    showError: (message) => deps.notice.show(message),
    clearError: () => deps.notice.clear(),
    setLatest: (data) => deps.setLatest(data),
  }
}
