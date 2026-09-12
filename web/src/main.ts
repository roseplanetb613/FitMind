// `vite/client` 声明 `*.css` 等副作用导入模块。tsconfig 未设 `types`（那会顺带
// 关掉其它自动 @types 收录），故在此就地引一次——否则 `import './styles.css'`
// 报 TS2882 "Cannot find module ... for side-effect import"；没有它就得新增一个
// src/vite-env.d.ts 来承担同一件事。
/// <reference types="vite/client" />
import * as THREE from 'three'
import './styles.css'
import { ApiSource } from './data/api'
import { loadInto, type LoadTarget } from './data/load'
import type { MuscleMapSource } from './data/source'
import { fetchPicks } from './data/exercises'
import { type MuscleMapData } from './data/types'
import { createLabelLayer } from './render/labels'
import { createScene, framingFor, musclesUnderCursor, nextPickIndex, pickMuscleId, setHover, type PickState } from './render/scene'
import { createLabelNames, createLoadTarget } from './wiring'
import { atEdge, nextId } from './ui/focus'
import { createLegend } from './ui/legend'
import { createLoadErrorNotice } from './ui/notice'
import { createDetailFlow } from './ui/detail-flow'
import { hideDetail } from './ui/detail'

const params = new URLSearchParams(location.search)
const uid = params.get('user_id') ?? 'local'
const days = Number(params.get('days') ?? 7)

const canvas = document.querySelector<HTMLCanvasElement>('#stage')!
// 顶层 await：createScene 现在要异步加载 4MB 的肌肉模型
const handle = await createScene(canvas)
const detailEl = document.querySelector<HTMLElement>('#detail')!
const legendEl = document.querySelector<HTMLElement>('#legend')!
const labelsEl = document.querySelector<HTMLElement>('#labels')!
createLegend(legendEl)
const notice = createLoadErrorNotice(legendEl)

let latest: MuscleMapData | null = null
// 名称与数值分开：latest 是数值（失败必须作废），names 是名称（失败保留，
// 于是失败态的标签仍是中文名 + "无记录"，见 wiring.ts 的 createLabelNames）
const names = createLabelNames()
// "当前是哪一块肌肉"的两个瞬时维度，都只影响"看哪一块"，不参与材质里的恢复语义：
//   hovered —— 指针指着的那块（悬停高亮）
//   focused —— 键盘 Tab / 点选选中的那块（详情浮层 + roving tabindex 跟着它）
let hovered: string | null = null
let focused: string | null = null
const labels = createLabelLayer(labelsEl, handle.body, (id) => names.resolve(id))
/** 键盘遍历顺序 = 标签顺序（就是那 28 个 id） */
const ids = labels.ids()

// 标签显隐。两个开关独立：「全部」是总开关，「无记录」默认关 ——
// 实测 28 个肌群里 7 个无记录，全显示会盖住模型、把有数值的那些挤掉。
// 判据在 labels.ts 的 labelVisible（纯函数，有测试）。
const toggleLabels = document.querySelector<HTMLInputElement>('#toggle-labels')!
const toggleUnknown = document.querySelector<HTMLInputElement>('#toggle-unknown')!
function applyLabelVisibility(): void {
  labels.setVisibility({
    layerVisible: toggleLabels.checked,
    showUnknown: toggleUnknown.checked,
  })
}
toggleLabels.addEventListener('change', applyLabelVisibility)
toggleUnknown.addEventListener('change', applyLabelVisibility)
applyLabelVisibility()

// 指针坐标 → 命中的 muscleId。点选与悬停共用这一套换算，避免两份 NDC 公式漂移。
// 射线判定本身在 scene.ts 的 pickMuscleId（**必须非递归**，理由与实测数字见那里）。
//
// 另注：Raycaster 依赖 matrixWorld，而 build() 返回的 Group 在加入场景并渲染前
// 不会自动更新矩阵。这里没有问题（用户交互时渲染循环已跑过多帧），但若将来
// 改成"首帧渲染前就做射线判定"，必须先 updateMatrixWorld(true)，否则所有 mesh
// 都被当作落在原点，命中的是完全错误的目标。
const raycaster = new THREE.Raycaster()
const ndc = new THREE.Vector2()

function ndcFor(clientX: number, clientY: number): void {
  const rect = canvas.getBoundingClientRect()
  ndc.set(
    ((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1,
  )
}

function pickAt(clientX: number, clientY: number): string | null {
  ndcFor(clientX, clientY)
  return pickMuscleId(handle.body, raycaster, ndc, handle.camera)
}

/**
 * 详情浮层 + 推荐动作的流程。逻辑在 `ui/detail-flow.ts`，**那里有测试** ——
 * `main.ts` 依赖 DOM + WebGL，在 node 里跑不了，所以任何留在这里的状态机都等于没有守卫。
 * 这个教训是实测付出来的：本模块里那段内联逻辑先后踩过两次
 * （导入却没调用、selectedId 从未被赋值），两次都是 tsc 过 + 全量测试过。
 */
const detailFlow = createDetailFlow({
  container: detailEl,
  // 每次现取：60 秒一轮刷新后必须读到新值，快照会读到上一轮
  getLatest: () => latest,
  // names.resolve 已经返回最终显示名（含 labels 降级回落），不要再套 resolveLabel
  resolveName: (id) => names.resolve(id),
  fetchPicks,
})

/** 选中/取消选中一块肌肉：标签提亮 + roving tabindex + 详情浮层，三处同步。
 *  点选与键盘都走这里，所以"当前是哪一块"只有一个来源。
 *
 *  `deeper` 是"这块肌肉后面还压着几块"（只有画布点选知道）。放在这里而不是
 *  调用点，是因为**提示必须跟选中同生共死** —— 从标签、Tab、Esc 进来的路径
 *  不经过画布点击，提示留在屏幕上就成了一句和当前选中无关的假话。 */
function select(id: string | null, deeper = 0): void {
  focused = id
  labels.setFocused(id)
  void detailFlow.showFor(id)
  showDepthHint(id === null ? 0 : deeper)
}

// 点选：只命中带 muscleId 的 mesh（点空处 = 取消选中）。
//
// **同一点重复点击沿射线依次深入** —— 真实解剖是分层的，深层肌肉被浅层的腱膜盖住
// （实测腹直肌就被腹外斜肌的腱膜挡着），不这么做它们永远选不到。
let pickState: PickState | null = null
canvas.addEventListener('click', (ev) => {
  ndcFor(ev.clientX, ev.clientY)
  const ids = musclesUnderCursor(handle.body, raycaster, ndc, handle.camera)
  pickState = nextPickIndex(pickState, ev.clientX, ev.clientY, ids)
  const id = pickState.index >= 0 ? ids[pickState.index] : null
  if (!id) {
    pickState = null
    select(null)
    return
  }
  select(id, ids.length - 1 - pickState.index)
})

/**
 * "还压着 N 块更深的肌群，再点一下"。
 *
 * **这条提示是必需的**，不是锦上添花：腹直肌被腹外斜肌腱膜盖住（解剖如此），
 * 不做提示的话用户只会觉得"腹直肌点不到"，而不知道"原地再点一下"这个操作存在。
 */
function showDepthHint(deeper: number): void {
  const el = document.querySelector<HTMLElement>('#depth-hint')
  if (!el) return
  el.textContent = deeper > 0 ? `还压着 ${deeper} 块更深的肌群 —— 再点一下` : ''
  el.style.display = deeper > 0 ? '' : 'none'
}

// 悬停高亮（spec §6.2）+ 标签提升。pointermove 触发频率很高，先比对再动材质/DOM。
function setHovered(id: string | null): void {
  if (id === hovered) return
  hovered = id
  setHover(handle.body, id)
  labels.setHovered(id)
}
canvas.addEventListener('pointermove', (ev) => setHovered(pickAt(ev.clientX, ev.clientY)))
canvas.addEventListener('pointerleave', () => setHovered(null))

// 键盘 Tab / Shift+Tab 在 28 个肌群间前后移动焦点（spec §6.2）。
//
// 焦点边界（有意选择，两条）：
//  1. 监听器挂在 #labels 上。keydown 只从**焦点所在的元素**冒泡到祖先，所以只有焦点
//     已经在标签层内部时才会进到这里——焦点在别处（工具栏、页面外）时 Tab 完全是
//     浏览器默认行为，本组件不插手。
//  2. 走到**两端就不接管**（atEdge 为真时 return，不 preventDefault）：浏览器按默认顺序
//     把焦点移出标签层。于是本层不是键盘陷阱——用户不需要知道 Esc 也能用 Tab /
//     Shift+Tab 走出去（WCAG 2.1.2 的判据正是 Tab/Shift+Tab 能否离开）。代价是从最后
//     一条按 Tab 会离场而不是绕回第一条（绕回是 nextId 的语义，见 focus.ts）。
//     离开方向：DOM 上 #labels 在 #toolbar 之前，正方向离场落在"正面"按钮上。
// Esc 另有用处（spec §6.2）：清掉选中态、撤下详情浮层、把 roving tabindex 复位到
// 进入点——它不等于"离场"，是"取消选中"。
labelsEl.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Tab') return
  const dir: 1 | -1 = ev.shiftKey ? -1 : 1
  // 以真实焦点为准：首次从页面上 Tab 进来时 focused 还是 null（焦点由浏览器按
  // roving tabindex 落到入口那条），若只看 focused 会算出"原地不动"。
  const active = document.activeElement as HTMLElement | null
  const current = (active && labels.idOf(active)) ?? focused
  if (atEdge(ids, current, dir)) return
  ev.preventDefault()
  const id = nextId(ids, current, dir)
  select(id)
  labels.focusElement(id)
})

document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Escape') return
  if (focused !== null) {
    select(null)
    // blurIfOwned：焦点已经不在标签层里（Tab 到工具栏按钮后按 Esc 就会这样——
    // 那时 atEdge 已经放手、focused 却还留着）时不许动它，否则键盘焦点被丢回 body。
    labels.blurIfOwned(document.activeElement as HTMLElement | null)
  }
  hideDetail(detailEl)
})

// 走 scene.ts 的 framingFor，不在这里另写一套坐标——"正面"的姿态只能有一个来源。
// 审查发现过真实的不一致：曾是场景加载时相机在 (0.9,1.5,2.6)（四分之三视角）、
// 而按钮回到 (0,1.5,2.6)，同一个"正面"两个姿态。现在 createScene 的初始相机也走
// framingFor('front')，本行是那条单源的唯一兑现点——若在这里硬编码坐标，不一致会复发。
document.querySelectorAll<HTMLButtonElement>('#toolbar button').forEach((b) => {
  b.addEventListener('click', () => {
    const view = b.dataset.view === 'back' ? 'back' : 'front'
    const f = framingFor(view)
    handle.camera.position.set(...f.position)
    handle.controls.target.set(...f.target)
    handle.controls.update()
  })
})

// loader 以 **MuscleMapSource 接口**为形参（不是具体的 ApiSource）——
// 这是那个接口唯一的兑现点：测试/故事书可以注入假实现而无需 stub fetch。
// 加载流程在 data/load.ts、它的下游装配在 wiring.ts，两者都只认接口/依赖对象，
// 因此成功与失败两条路径都能在 node 里断言（main.ts 依赖 DOM + WebGL，自己测不到）。
const target: LoadTarget = createLoadTarget({
  body: handle.body,
  stars: handle.stars,
  labels,
  notice,
  // getter：重放高亮时要读"此刻"的悬停，不是装配时的快照
  getHovered: () => hovered,
  setLatest: (data) => {
    latest = data
    names.update(data) // 名称：失败保留、成功替换（见 createLabelNames）
    if (data === null) {
      // 数据作废 → 已打开的详情浮层同步撤下并清掉选中态。不清的话浮层还挂着
      // 上一轮的数值，与刚置为未知的材质/标签在同一屏上互相打架。
      select(null)
    }
  },
  afterLabels: () => void detailFlow.showFor(focused), // 详情跟着同一轮新数据走，不留在上一轮的数值上
})

const source: MuscleMapSource = new ApiSource(uid, days)
setInterval(() => void loadInto(source, days, target), 60_000)
void loadInto(source, days, target)

// 标签层跟随投影（几何与渲染由 scene.ts 自己的 RAF 驱动）
const tick = (): void => {
  requestAnimationFrame(tick)
  labels.update(handle.camera, { w: canvas.clientWidth, h: canvas.clientHeight })
}
tick()
