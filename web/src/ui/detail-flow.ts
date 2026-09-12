import type { MuscleExercises } from '../data/exercises'
import { muscleState, type MuscleMapData } from '../data/types'
import { hideDetail, renderPicks, showDetail } from './detail'

/**
 * 详情浮层 + 推荐动作的流程。**抽出来就是为了可测**。
 *
 * 为什么要有这个模块：这段逻辑原先直接写在 `main.ts` 里，而 `main.ts` 依赖
 * DOM + WebGL，**在 node 里跑不了** —— 于是它没有任何测试。代价实测发生了两次：
 *
 *   1. 只加了 `import { fetchPicks }`，**没加调用** —— tsc 过、178 条测试全过，
 *      但点肌肉什么都不出（后来靠 `noUnusedLocals` 才能拦）
 *   2. `selectedId` 只在"取消选中"那一支里被置 null，**正常分支从没被设成 id** ——
 *      而 `loadPicks` 拿 `selectedId !== id` 丢弃过期响应，判据恒真 →
 *      每次都在第一行 return → 推荐永远不渲染
 *
 * 两次的共同点：**状态在多处读写、却没有一处能被断言**。本模块把状态（选中项、
 * 缓存）收拢进闭包，依赖全部注入，于是"点了之后该发生什么"可以在 node 里测。
 */
export interface DetailFlowDeps {
  container: HTMLElement
  /** 当前数据。**每次现取，不能快照** —— 60 秒一轮刷新后要读到新值 */
  getLatest: () => MuscleMapData | null
  resolveName: (id: string) => string
  fetchPicks: (id: string, limit: number) => Promise<MuscleExercises>
  /** 可注入以便测试观察；生产就是 detail.ts 的那两个 */
  showDetail?: typeof showDetail
  hideDetail?: typeof hideDetail
  renderPicks?: typeof renderPicks
}

export interface DetailFlow {
  /** 选中/取消选中一块肌肉。传 null = 撤下浮层。 */
  showFor(id: string | null): Promise<void>
  /** 当前选中项（测试与竞态断言用） */
  selected(): string | null
}

export function createDetailFlow(deps: DetailFlowDeps): DetailFlow {
  const _show = deps.showDetail ?? showDetail
  const _hide = deps.hideDetail ?? hideDetail
  const _picks = deps.renderPicks ?? renderPicks

  let selectedId: string | null = null
  /**
   * 推荐缓存。**不只是省请求**：`showDetail` 每次会 `innerHTML = ''`，
   * 60 秒一轮的数据刷新若不重挂，用户正看着的推荐会突然消失。
   */
  const cache = new Map<string, { exercises: MuscleExercises['exercises']; fallback: boolean }>()

  async function load(id: string): Promise<void> {
    try {
      const r = await deps.fetchPicks(id, 3)
      if (selectedId !== id) return // 用户已经点了别的，这个响应作废
      cache.set(id, { exercises: r.exercises, fallback: r.fallback })
      _picks(deps.container, r.exercises, r.fallback)
    } catch (err) {
      if (selectedId !== id) return
      console.warn('推荐动作加载失败', err)
      _picks(deps.container, null, false, err)
    }
  }

  return {
    async showFor(id: string | null): Promise<void> {
      const latest = deps.getLatest()
      if (!id || !latest) {
        selectedId = null
        _hide(deps.container)
        return
      }
      // **这一行是承重的。** 少了它，load 里的 `selectedId !== id` 恒真，
      // 推荐永远不渲染（实测踩过，见模块顶部说明）。
      selectedId = id

      _show(deps.container, deps.resolveName(id), muscleState(latest, id))

      const hit = cache.get(id)
      if (hit) _picks(deps.container, hit.exercises, hit.fallback)
      else await load(id)
    },
    selected: () => selectedId,
  }
}
