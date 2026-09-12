// `vite/client` 声明 `*.css` 等副作用导入模块。tsconfig 未设 `types`（那会顺带
// 关掉其它自动 @types 收录），故在此就地引一次——否则 `import './styles.css'`
// 报 TS2882 "Cannot find module ... for side-effect import"。没有它就得新增
// src/vite-env.d.ts，而本任务的交付面刻意只有 3 个 web 文件。
/// <reference types="vite/client" />
import * as THREE from 'three'
import './styles.css'
import { ApiSource } from './data/api'
import { loadInto, type LoadTarget } from './data/load'
import type { MuscleMapSource } from './data/source'
import { muscleState, resolveLabel, type MuscleMapData } from './data/types'
import { createLabelLayer } from './render/labels'
import { applyStates, createScene, framingFor, setHover } from './render/scene'
import { atEdge, nextId } from './ui/focus'
import { createLegend } from './ui/legend'
import { createLoadErrorNotice } from './ui/notice'
import { hideDetail, showDetail } from './ui/detail'

const params = new URLSearchParams(location.search)
const uid = params.get('user_id') ?? 'local'
const days = Number(params.get('days') ?? 7)

const canvas = document.querySelector<HTMLCanvasElement>('#stage')!
const handle = createScene(canvas)
const detailEl = document.querySelector<HTMLElement>('#detail')!
const legendEl = document.querySelector<HTMLElement>('#legend')!
const labelsEl = document.querySelector<HTMLElement>('#labels')!
createLegend(legendEl)
const notice = createLoadErrorNotice(legendEl)

// 标签名走 resolveLabel：labels 降级成 {} 时回落显示 id，不丢块（spec §3.3）
let latest: MuscleMapData | null = null
// "当前是哪一块肌肉"的两个瞬时维度，都只影响"看哪一块"，不参与材质里的恢复语义：
//   hovered —— 指针指着的那块（悬停高亮）
//   focused —— 键盘 Tab / 点选选中的那块（详情浮层 + roving tabindex 跟着它）
let hovered: string | null = null
let focused: string | null = null
const labels = createLabelLayer(
  labelsEl,
  handle.body,
  (id) => (latest ? resolveLabel(latest.labels, id) : id),
)
/** 键盘遍历顺序 = 标签顺序（就是那 28 个 id） */
const ids = labels.ids()

// 指针坐标 → 命中的 muscleId。点选与悬停共用这一套换算，避免两份 NDC 公式漂移。
//
// **非递归遍历（第三个参数 false）是硬性要求**，理由见 scenery.ts 模块注释。
// 另注：Raycaster 依赖 matrixWorld，而 build() 返回的 Group 在加入场景并渲染前
// 不会自动更新矩阵。这里没有问题（用户交互时渲染循环已跑过多帧），但若将来
// 改成"首帧渲染前就做射线判定"，必须先 updateMatrixWorld(true)，否则所有 mesh
// 都被当作落在原点，命中的是完全错误的目标。
const raycaster = new THREE.Raycaster()
const ndc = new THREE.Vector2()
function pickAt(clientX: number, clientY: number): string | null {
  const rect = canvas.getBoundingClientRect()
  ndc.set(
    ((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1,
  )
  raycaster.setFromCamera(ndc, handle.camera)
  const hit = raycaster.intersectObjects(handle.body.children, false)[0]
  return (hit?.object.userData.muscleId as string | undefined) ?? null
}

/** 详情浮层：跟着 focused 走。数据未就绪（失败后 latest 为 null）就不展示。 */
function showDetailFor(id: string | null): void {
  if (!id || !latest) {
    hideDetail(detailEl)
    return
  }
  // muscleState 而非 latest.muscles[id]：缺键与 null 都要归一（见 types.ts）
  showDetail(detailEl, resolveLabel(latest.labels, id), muscleState(latest, id))
}

/** 选中/取消选中一块肌肉：标签提亮 + roving tabindex + 详情浮层，三处同步。
 *  点选与键盘都走这里，所以"当前是哪一块"只有一个来源。 */
function select(id: string | null): void {
  focused = id
  labels.setFocused(id)
  showDetailFor(id)
}

// 点选：只命中带 muscleId 的 mesh（点空处 = 取消选中）
canvas.addEventListener('click', (ev) => select(pickAt(ev.clientX, ev.clientY)))

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
    ;(document.activeElement as HTMLElement | null)?.blur?.()
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
// 加载逻辑本身在 data/load.ts，它只认下面这个 LoadTarget，因此失败路径能在
// node 里断言（main.ts 依赖 DOM + WebGL，自己测不到）。
const target: LoadTarget = {
  applyStates: (data) => {
    applyStates(handle.body, data)
    // applyStates 把基色写回 palette 的常量，会抹掉悬停高亮；每 60s 一次的刷新
    // 之后重放当前悬停，否则鼠标不动的话高亮要等下一次 pointermove 才回来。
    setHover(handle.body, hovered)
  },
  setLabels: (states) => {
    labels.setStates(states)
    showDetailFor(focused) // 详情跟着新数据走，不留在上一轮的数值上
  },
  showError: (message) => notice.show(message),
  clearError: () => notice.clear(),
  setLatest: (data) => {
    latest = data
    if (data === null) {
      // 数据作废 → 已打开的详情浮层同步撤下并清掉选中态。不清的话浮层还挂着
      // 上一轮的数值，与刚置为未知的材质/标签在同一屏上互相打架。
      select(null)
    }
  },
}

const source: MuscleMapSource = new ApiSource(uid, days)
setInterval(() => void loadInto(source, days, target), 60_000)
void loadInto(source, days, target)

// 标签层跟随投影（几何与渲染由 scene.ts 自己的 RAF 驱动）
const tick = (): void => {
  requestAnimationFrame(tick)
  labels.update(handle.camera, { w: canvas.clientWidth, h: canvas.clientHeight })
}
tick()
