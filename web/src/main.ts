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
import { attachFluidTrail } from './render/fluid-trail'
import { createLabelNames, createLoadTarget } from './wiring'
import {
  FpsWindow, TierGovernor, TIER_SETTINGS, detectInitialTier, readDeviceHints, type Tier,
} from './render/perf'
import { atEdge, nextId } from './ui/focus'
import { createLegend } from './ui/legend'
import { createLoadErrorNotice } from './ui/notice'
import { createSessionBar } from './ui/session-ui'
import { createDetailFlow } from './ui/detail-flow'
import { MUSCLE_IDS } from './body/load-model'
import { postCheckinResolve, streamChat } from './data/chat'
import { asrStatus, postAsr } from './data/asr'
import { postFoodPhoto, visionStatus } from './data/vision'
import { createChatFlow } from './ui/chat-flow'
import { createChatPanel } from './ui/chat-panel'
import { createVoiceFlow, type RecorderLike } from './ui/voice-flow'
import { createPhotoFlow } from './ui/photo-flow'
import { createProfileForm } from './ui/profile-form'
import { createPlanPanel } from './ui/plan'
import { createWorkoutFlow } from './ui/workout-flow'
import { deletePlan } from './data/plan'
import { saveProfile } from './data/profile'
import { hideDetail } from './ui/detail'

const params = new URLSearchParams(location.search)
const uid = params.get('user_id') ?? 'local'
const days = Number(params.get('days') ?? 7)

const canvas = document.querySelector<HTMLCanvasElement>('#stage')!

// ── 性能档位（2026-09-17 手机端治理）────────────────────────────────
// 开局由**能力探测**定，运行中由**帧率采样**调整。档位表与判定逻辑在
// render/perf.ts（纯函数 + 注入，有 25 条单测）。
const initialTier: Tier = detectInitialTier(readDeviceHints())
const initialSettings = TIER_SETTINGS[initialTier]
let tier: Tier = initialTier

const fpsWindow = new FpsWindow(60)
const governor = new TierGovernor({ initial: initialTier })

/**
 * 每帧喂给采样器；窗口满了就让 governor 判一次升降档。
 *
 * ⚠ 停帧期间本函数不会被调用，恢复时窗口必然是空的 —— 不会把那段静止时间
 * 算成卡顿（`FpsWindow.reset()` 存在的全部理由）。
 */
function sampleFrame(frameMs: number): void {
  fpsWindow.push(frameMs)
  if (!fpsWindow.ready) return
  const next = governor.onWindow(fpsWindow.fps)
  fpsWindow.reset()
  if (next !== tier) applyTier(next)
}

/**
 * 档位切换：两个渲染层都要联动。
 *
 * ⚠ 3D 层与尾流层是**两个独立对象**（各自一块 canvas、一套 GL 状态机），
 * 没有共同祖先 —— 必须显式分发，漏掉一支就会出现"3D 降了、尾流还在满速跑"。
 */
function applyTier(next: Tier): void {
  tier = next
  const s = TIER_SETTINGS[next]
  sceneHandle?.setTier(next)
  fluidHandle?.setTier(s.dyeResolution)
}

/**
 * 标签层跟随投影。**不再是自己的 rAF** —— 挂到 3D 循环的每帧回调上
 * （见下面 createScene 的 onFrame）。本仓此前有三个常驻 rAF（3D / 尾流 / DOM 标签），
 * 停帧只有把三个都收进来才有意义。
 *
 * 惰性赋值：`createScene` 要先于 `labels` 建好，而回调可能在赋值前就被调用（首帧）
 * —— null 检查让那一帧安静跳过。
 */
let updateLabels: (() => void) | null = null

// 拖拽尾流（RosePlanet 作品页那颗 SplashCursor 的移植，参数按用户口径缩小一半）。
// 挂在自己的 `#fluid` 画布上，DOM 顺序在 #stage 之前 → 就在 3D 模型**背后**。
//
// 拿不到 WebGL、或用户在系统里开了"减少动效" → `attachFluidTrail` 返回 null，
// 页面照常跑（本仓"全降级"传统）。
// eco 档**不创建**：那一档本来就要求关掉尾流，没必要先建一套 FBO 再停掉。
const fluidCanvas = document.querySelector<HTMLCanvasElement>('#fluid')
let fluidHandle = fluidCanvas && initialSettings.dyeResolution !== null
  ? attachFluidTrail(fluidCanvas, {
      dyeResolution: initialSettings.dyeResolution,
      pixelRatioCap: initialSettings.pixelRatio,
    })
  : null
/** 3D 层句柄（`createScene` 完成后才有）。档位联动用，只取需要的那个方法。 */
let sceneHandle: { setTier: (t: Tier) => void } | null = null

// **先发起、不在这里 await**（2026-09-13）。createScene 要异步加载 ~543 KiB 的
// 肌肉模型，而原写法把它 await 在模块顶部，于是**下面每一行都被它卡住** ——
// 包括与 3D 毫无关系的图例、错误提示、名称层。实测用户在手机上刷新时，
// 首屏只看到 index.html 里静态存在的工具栏（"正面/背面/训练计划/标签/无记录"），
// 等模型下完才"唰"地出现全部内容，像页面坏了。
//
// 现在只有**真正用到 handle 的地方**才等（见下方 `await sceneReady`），
// 图例等不受影响的装配立刻完成。配合 index.html 的 #boot 载入提示，
// 等待窗口读起来是"正在载入模型"而不是"页面残缺"。
const sceneReady = createScene(canvas, {
  tier: initialTier,
  onFrame: (frameMs) => {
    updateLabels?.() // 标签跟随相机（原先是独立 rAF，见 updateLabels 的说明）
    sampleFrame(frameMs)
  },
})
const detailEl = document.querySelector<HTMLElement>('#detail')!
const legendEl = document.querySelector<HTMLElement>('#legend')!
const labelsEl = document.querySelector<HTMLElement>('#labels')!
createLegend(legendEl)
const notice = createLoadErrorNotice(legendEl)
// 会话条（左下角）：当前身份 + 退出登录。内部 fetch /v1/auth/me，
// 未启用登录（账号表空）时整条自动隐藏 —— 单机用户看不到它。
createSessionBar(document.querySelector<HTMLElement>('#session')!)

let latest: MuscleMapData | null = null
// 名称与数值分开：latest 是数值（失败必须作废），names 是名称（失败保留，
// 于是失败态的标签仍是中文名 + "无记录"，见 wiring.ts 的 createLabelNames）
const names = createLabelNames()
// "当前是哪一块肌肉"的两个瞬时维度，都只影响"看哪一块"，不参与材质里的恢复语义：
//   hovered —— 指针指着的那块（悬停高亮）
//   focused —— 键盘 Tab / 点选选中的那块（详情浮层 + roving tabindex 跟着它）
let hovered: string | null = null
let focused: string | null = null
// 到这里才需要 3D —— 前面那些装配（图例/提示/名称层）在模型下载期间就已完成。
// finally 保证**成功或失败都撤掉 #boot**：模型加载失败时若留着它，"正在载入"
// 会永久盖在页面上，比空白更难排查（错误提示在建 #boot 的那一步已经被挡住了）。
const handle = await sceneReady.finally(() => {
  document.querySelector('#boot')?.remove()
})
sceneHandle = handle
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
  // 悬停改的是材质基色 → 必须请求渲染（eco 档可能正停着帧）
  handle.requestRender()
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
    // 数据到达 = 材质要重画。eco 档此时可能正停着帧，不请求就"数据到了屏幕不动"。
    handle.requestRender()
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

// 标签层跟随投影：接到 3D 循环的每帧回调上（见 createScene 的 onFrame），
// 这里只把「怎么更新」装进去。首屏那一帧先手动跑一次，免得等下一帧才出现标签。
updateLabels = () => {
  labels.update(handle.camera, { w: canvas.clientWidth, h: canvas.clientHeight })
}
updateLabels()

// ── 与教练对话（agent）──────────────────────────────────────────────
// 逻辑全在 ui/chat-flow.ts（可单测），这里只做三件装配的事：
// 把 DOM 交给面板、把网络交给 data/chat、把"点卡片"接到已有的 select()。

// 会话 id **按 user_id 分键**（2026-09-17，登录功能配套）：同一个浏览器
// 换账号登录时，若键不分账号，后一个人会接上前一个人"最近几轮对话"的语境
// —— 数据不会串（写都进各自账号），但"刚才说的"会串。键带上 uid 后，
// 各账号的对话上下文互不可见；同一账号换设备仍各自开新会话。
const SESSION_KEY = 'fitmind.chat.session.' + uid
const chatEl = document.querySelector<HTMLElement>('#chat')!
const chatToggleEl = document.querySelector<HTMLButtonElement>('#chat-toggle')!
const profileEl = document.querySelector<HTMLElement>('#profile')!
const planEl = document.querySelector<HTMLElement>('#plan')!
const planToggleEl = document.querySelector<HTMLButtonElement>('#plan-toggle')!

/**
 * 保证会话 id 存在 —— **建档和对话共用同一个**。
 *
 * 建档接口按 session_id 定位（`POST /v1/profile`），而没聊过天时前端手里还没有 id。
 * 与其为建档单开一条"按 user_id 建档"的接口（等于把同一件事做成两套），
 * 不如在前端生成一个：后端 `sessions.create(given_id)` 对未知 id 就是建一个新的，
 * 格式与它自己的 `uuid4().hex[:12]` 同形。副作用是**好事**：建档之后再聊天，
 * 对话会复用这个 id，档案和上下文落在同一个会话里。
 */
function ensureSessionId(): string {
  try {
    const cur = localStorage.getItem(SESSION_KEY)
    if (cur) return cur
    const buf = new Uint8Array(6)
    crypto.getRandomValues(buf)
    const id = Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('')
    localStorage.setItem(SESSION_KEY, id)
    return id
  } catch {
    return ''      // 隐私模式：拿不到存储 → 退回"服务端会新建一个"，建档仍可用
  }
}
let sessionId = ensureSessionId()

const profileForm = createProfileForm({
  root: profileEl,
  // getter：会话 id 可能在首次对话后才由后端确立，不能在建档窗口创建时快照
  get sessionId() { return sessionId || ensureSessionId() },
  userId: uid,
  save: (sid, profile, user) => saveProfile(sid, profile, user),
  onSaved: (p) => {
    // 存完告诉用户一声，并且**带上他刚填的关键项** —— 只说"已保存"用户
    // 不知道自己填的对不对
    const bits = [p.sex === 'male' ? '男' : p.sex === 'female' ? '女' : null,
                  p.age ? `${p.age}岁` : null,
                  p.height_cm ? `${p.height_cm}cm` : null,
                  p.weight_kg ? `${p.weight_kg}kg` : null].filter(Boolean)
    void chatFlow.send(`档案已保存：${bits.join(' ')}` || '档案已保存')
  },
})

// ── 语音输入 ────────────────────────────────────────────────────────
// 状态机在 ui/voice-flow.ts（可单测），这里只装配浏览器 API 并接线。

/**
 * 转写服务能不能用 —— **决定要不要渲染麦克风按钮**。
 *
 * ⚠ **必须是顶层 await，且必须在建面板之前完成**：`onMic` 传不传是在
 * `createChatPanel` 那一刻定下来的，晚了按钮就建不出来。
 * 探活端点**不会**触发模型加载（见 app/runtime/asr.py 的 status），所以这次
 * 多等一个 RTT 不会把 4.6GB 权重拉进显存。
 * 探活本身失败按"不可用"处理，不会让页面挂掉（见 data/asr.ts 的 asrStatus）。
 */
const asrInfo = await asrStatus()

/**
 * 食物识别能不能用 —— 同上，**决定要不要渲染相机按钮**。
 *
 * 与语音同一套约定：**探活必须在建面板之前**（`onPhoto` 传不传是在构造时定的），
 * 探活失败按"不可用"处理、不抛错，于是结果是"没有按钮"而不是"坏按钮"。
 * 见 app/runtime/vision.py 的 status —— 探活不调模型，所以这次请求不花钱。
 */
const visionInfo = await visionStatus()

const chatPanel = createChatPanel({
  root: chatEl,
  // 28 个 id 的**单源**就是 load-model 的 MUSCLE_IDS —— 卡片里扫肌肉名时用它比对，
  // 不另写一份列表（另写一份必然与后端漂移）
  muscleIds: MUSCLE_IDS,
  // 点卡片 → 走已有的 select()，于是高亮 / 标签提亮 / 详情浮层 / 推荐动作
  // 整套链路自动生效，这里不需要知道其中任何一件
  onMuscleClick: (id) => {
    select(id)
    chatPanel.setOpen(false) // 让出屏幕，否则面板正好盖住要看的模型
  },
  onChooseOption: (o, at) => void chatFlow.chooseOption(o, at),
  onChooseCustom: (text, at) => void chatFlow.submitCustom(text, at),
  onOpenProfile: () => void profileForm.open(),
  // 不可用就**不传**，按钮整个不渲染 —— 不给一个点了才报错的按钮
  ...(asrInfo.available ? { onMic: () => void voiceFlow.toggle() } : {}),
  ...(visionInfo.available ? { onPhoto: () => void photoFlow.pick() } : {}),
  onSubmit: (text) => void chatFlow.send(text),
})

const voiceFlow = createVoiceFlow({
  // ⚠ **必须传方法本体**，不能包一层箭头函数：
  //   `(c) => navigator.mediaDevices?.getUserMedia(c)` 这个箭头**恒为真值**，
  //   voice-flow 里 `if (!gum)` 的"环境不支持"分支永远不触发；而非安全上下文
  //   （http:// + 非 localhost，比如局域网 IP）下 `mediaDevices` 是 undefined，
  //   可选链让调用**静默返回 undefined** → `new MediaRecorder(undefined)` →
  //   用户看到原生 TypeError（实测）。传方法本体，undefined 会如实透传给
  //   voice-flow 的人话分支。
  //   `.bind` 是必须的：getUserMedia 从 navigator.mediaDevices 上摘下来裸调
  //   会 Illegal invocation。
  getUserMedia: navigator.mediaDevices?.getUserMedia.bind(navigator.mediaDevices),
  createRecorder: (stream) =>
    new MediaRecorder(stream) as unknown as RecorderLike,
  transcribe: (blob) => postAsr(blob),
  // 转写结果**只回填、不自动发**：语音必然有听错的，给用户改字的机会
  onText: (text) => chatPanel.setInput(text),
  onChange: (s) => chatPanel.setVoiceStatus(s.status, s.error),
})

const photoFlow = createPhotoFlow({
  // 文件选择器。用 <input type=file accept=image/*>，移动端会直接给拍照选项。
  pickFile: () => new Promise<File | null>((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    // ⚠ **取消必须 resolve(null)**：photo-flow 在 pick() 期间是 'picking'，
    // 进行中的点击会被忽略。而用户取消选择时 `change` **不触发**（浏览器差异）
    // —— promise 一悬挂，状态就永远回不到 idle，表现为"取消一次后按钮卡死，
    // 再也选不了"（实测）。原先的注释说"悬挂没关系，下次点会开新 input"，
    // 那是没算上状态机：**悬挂的 promise + 忽略并发点击 = 永久锁死**。
    //
    // 两道保险：input 的 `cancel` 事件（Chrome 113+ / Safari 16.4+）+
    // 窗口焦点回归（选择器关掉必然焦点回来；延 500ms 是给可能迟到的 change 让路）。
    let settled = false
    const done = (v: File | null): void => {
      if (settled) return
      settled = true
      window.removeEventListener('focus', onFocus)
      resolve(v)
    }
    const onFocus = (): void => {
      window.setTimeout(() => { if (!settled) done(null) }, 500)
    }
    input.addEventListener('change', () => done(input.files?.[0] ?? null))
    input.addEventListener('cancel', () => done(null))
    window.addEventListener('focus', onFocus)
    input.click()
  }),
  upload: (file) => postFoodPhoto(file),
  // 卡片作为**本地消息**进流 —— 图片没有文本意图，不走 /v1/chat
  onCard: (card) => chatFlow.addFoodCard(card),
  onChange: (s) => chatPanel.setPhotoStatus(s.status, s.error),
})

const chatFlow = createChatFlow({
  send: (req, onStage) => streamChat(req, onStage),
  // 消歧选择框点定 → 补记（不走 chat：已经确知是哪个动作了，没必要再跑 agent）
  resolve: (req) => postCheckinResolve(req),
  onChange: (state) => chatPanel.render(state),
  loadSession: () => {
    try {
      return localStorage.getItem(SESSION_KEY)
    } catch {
      return null // 隐私模式下 localStorage 会抛，别让它带挂整个页面
    }
  },
  saveSession: (id) => {
    sessionId = id                 // 建档窗口共用同一个会话 id
    try {
      localStorage.setItem(SESSION_KEY, id)
    } catch {
      /* 存不下就算了：只是丢了跨刷新的上下文，不影响本次对话 */
    }
  },
  userId: uid,
  // 后端说"还没建档" → 直接把窗口弹出来，别让用户自己去找入口
  onNeedProfile: () => void profileForm.open(),
})

chatToggleEl.addEventListener('click', () => chatPanel.setOpen(true))
chatPanel.render(chatFlow.state()) // 首帧：显示欢迎语而不是空面板

// 训练计划抽屉。打开时才拉取 —— 计划是"想查才看"的东西，没必要每次进页面都请求。
// 它与对话面板无关：计划来自图谱里的 PlanVersion，不是这次对话的产物。
const planPanel = createPlanPanel({
  root: planEl,
  userId: uid,
  deletePlan: (u) => deletePlan(u),   // 卡片「删除计划」按钮 → /v1/plan/delete
})
planToggleEl.addEventListener('click', () => void planPanel.open())

// ── 训练执行台（全屏跟练）────────────────────────────────────────────
// 逻辑全在 ui/workout-flow.ts（依赖注入 + 有测试）。这里只装配三件事，每件都是
// 本文件才有的东西：3D 句柄、对话通道、定时器。
const workoutEl = document.querySelector<HTMLElement>('#workout')!
const workoutToggleEl = document.querySelector<HTMLButtonElement>('#workout-toggle')!

/**
 * 跟练期间的刷新节拍。
 *
 * ⚠ 它**只驱动重绘**，不参与计时 —— 剩余秒数每次都由状态机按**绝对时间戳**现算
 * （见 data/workout-session.ts）。所以这个间隔取多大都不会让倒计时走偏，
 * 250ms 只是"秒数变化后最多 250ms 内可见"。
 */
let workoutTimer = 0
function setWorkoutLoop(on: boolean): void {
  if (on && !workoutTimer) {
    workoutTimer = window.setInterval(() => workoutFlow.tick(), 250)
  } else if (!on && workoutTimer) {
    window.clearInterval(workoutTimer)
    workoutTimer = 0
  }
}

const workoutFlow = createWorkoutFlow({
  root: workoutEl,
  userId: uid,
  onOpen: () => {
    // 执行台盖住 3D → 停渲染。**不能靠 canIdlePause**：那是派生的，只有 eco 档
    // 为真，而桌面上是 full 档（有心跳与星点闪烁），循环会一直满速跑。
    handle.setSuspended(true)
    setWorkoutLoop(true)
  },
  onClose: () => {
    handle.setSuspended(false)
    setWorkoutLoop(false)
  },
  onFinished: (s) => {
    // ⚠ **不在前端拼总结**（规格 §4.8）：措辞与数字由 Agent 结合刚写回的图谱数据给。
    // 这里只负责把"发生了什么"如实告诉它 —— 包括"有几个动作没记上"，那是 Agent
    // 从图谱里**看不出来**的运行状态（图谱里没有 = 它只会以为没做，
    // 于是总结会漏掉那几个动作而不是说"记录失败"）。
    const head = s.allDone
      ? `我今天按计划练完了（${s.day}）`
      : `我今天按计划练了一部分就结束了（${s.day}）`
    void chatFlow.send(s.notice ? `${head}。${s.notice}` : head)
  },
})
workoutToggleEl.addEventListener('click', () => void workoutFlow.open())
