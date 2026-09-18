import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { build } from '../body/build'
import { loadBody } from '../body/load-model'
import { applyStars, buildStarField, setStarMotion, tickStars } from './star-field'
import { buildHeartGlow, prefersReducedMotion, pulseGlow, pulseHeartEmissive } from './glow'
import { emptyMap, muscleState, type MuscleMapData } from '../data/types'
import { BASE_COLOR, HOVER_COLOR, NON_MUSCLE_COLOR, NON_MUSCLE_EMISSIVE, palette } from './palette'
import { TIER_SETTINGS, canIdlePause, type Tier } from './perf'

/** 只建场景图，不建 renderer —— 使 node 里可测（无 WebGL 上下文）。 */
export function buildBodyGroup(): THREE.Group {
  return build()
}

/**
 * 数据 → 材质。**绝不重建几何**（spec §3.2）：
 * 几何在加载时建一次，切用户 / 切天数只走这里。
 */
export function applyStates(group: THREE.Group, data: MuscleMapData): void {
  for (const child of group.children) {
    const id = child.userData.muscleId as string | undefined
    if (!id) continue
    const mat = (child as THREE.Mesh).material as THREE.MeshStandardMaterial
    // 非骨骼肌块（心脏）走**自己的一套固定呈现**，不参与恢复度色轴。
    // 理由见 NON_MUSCLE_COLOR 的说明 —— 后端给它的是 null，走 palette(null)
    // 会得到"线框 + 0.047 不透明度"，实际上完全看不见。
    if (child.userData.style === 'non-muscle') {
      mat.color.set(NON_MUSCLE_COLOR)
      mat.emissive.set(NON_MUSCLE_EMISSIVE)
      mat.emissiveIntensity = NON_MUSCLE_EMISSIVE_INTENSITY
      mat.wireframe = false // **必须显式关掉**：recovery=null 会让它看起来"未知"
      mat.opacity = NON_MUSCLE_OPACITY
      mat.transparent = NON_MUSCLE_OPACITY < 1
      mat.needsUpdate = true
      continue
    }

    // 统一走 muscleState（types.ts 的单源归一入口），使"缺键 / 显式 null / 有记录"
    // 三种情形只有一个判据。注意：本行的 `&&` 短路已经能兜住缺键的 undefined，
    // 所以这里不是非它不可——不依赖那个巧合才是理由。
    const state = muscleState(data, id)
    const entry = palette(state && state.has_record ? state.recovery : null)

    mat.color.set(entry.baseColor)
    mat.emissive.set(entry.emissive)
    mat.emissiveIntensity = entry.emissiveIntensity
    mat.wireframe = entry.style === 'wireframe'
    const wire = entry.style === 'wireframe'
    const base = entry.opacity * MUSCLE_OPACITY
    // 线框态垫下限：见 WIREFRAME_OPACITY_FLOOR 的说明（§5.4 的硬要求，不是观感）
    let opacity = wire ? Math.max(base, WIREFRAME_OPACITY_FLOOR) : base
    // 按**不透明度**乘系数 —— 见 OPACITY_SCALE 的说明（方向别搞反）
    const oScale = OPACITY_SCALE[id]
    if (oScale !== undefined) opacity *= oScale
    mat.opacity = opacity
    mat.transparent = mat.opacity < 1
    mat.needsUpdate = true
  }
}

/**
 * 悬停高亮：把 `id` 那一块的**基色**换成 HOVER_COLOR，其余写回 BASE_COLOR；
 * `id === null` 即全部写回 BASE_COLOR（移开指针 / 离开画布）。
 *
 * 为什么是基色，而不是"把自发光乘个系数"（spec §5 把语义放在自发光上）：
 *  1. 未知态的 `emissiveIntensity` 恒为 0（palette(null)），乘任何系数仍是 0——
 *     线框块会"高亮"得毫无变化。基色则对所有状态都是同一个常量，
 *     所以**未知态与已知态用同一条路径高亮**，没有特例。
 *     （线框块画的边线取自同一份材质：受光基色 + 自发光×强度，基色变亮 → 边线变亮。）
 *  2. 基色恒定（palette 三个分支都返回 BASE_COLOR）⇒ 还原不需要快照/基线字段，
 *     `setHover(group, null)` 直接写回常量即可。于是"悬停 → 移开 → applyStates"
 *     与"直接 applyStates"逐字段相同：本函数只碰 color，绝不碰
 *     emissive / emissiveIntensity / opacity / wireframe / transparent
 *     ——那些是 applyStates 的语义通道。
 *  3. spec §5.2 明确基色"不参与语义"，把交互反馈放在这里不会与恢复度混淆。
 *
 * 与 applyStates 一样**只改材质、不重建**。整组重算（幂等），不保留"上一个悬停"，
 * 因此与调用顺序无关：applyStates 之后再调本函数、或反过来，结果都一样。
 * 残留风险（无头环境测不到，属设计取舍）：
 *  (a) 高亮期间那一块的基色变亮，理论上可能被读成"恢复度更高"；靠悬停态同时带来的
 *      标签加粗/上浮来消歧（见 labels.setHovered）。
 *  (b) **自发光强的块上悬停反馈可能偏弱**：|t| 大时渲染亮度主要来自自发光
 *      （emissive × emissiveIntensity），基色那点提亮在总亮度里占比变小，
 *      于是"未知块（自发光恒 0、全靠基色）"上的高亮反而最明显。基色是唯一能同时
 *      照亮已知/未知两种状态的通道，这是走这条路的已知代价，不是可以顺手修掉的 bug。
 */
export function setHover(group: THREE.Group, id: string | null): void {
  for (const child of group.children) {
    const mid = child.userData.muscleId as string | undefined
    if (!mid) continue // 装饰 Group 无 muscleId：不参与高亮，也不会被当成悬停目标
    const mat = (child as THREE.Mesh).material as THREE.MeshStandardMaterial
    // 还原必须写回**该块自己的**基色，不能写死 BASE_COLOR ——
    // 否则悬停任意一块肌肉都会把心脏的红抹成灰（心脏的基色不是 BASE_COLOR）。
    mat.color.set(mid === id ? HOVER_COLOR : blockColor(child.userData.style))
  }
}

/**
 * NDC + 相机 → 命中的 muscleId（点选与悬停共用同一套判定）。
 *
 * **第三个参数必须是 `false`（非递归）。** body.children 里除了 50 块肌肉还有一个
 * scenery 装饰 Group，它内部的 mesh **没有** muscleId。递归遍历时首个命中往往就是
 * 这类装饰块 → `userData.muscleId` 是 undefined → 这里返回 null → 悬停/点选在
 * 被装饰块挡住的方向上**静默失效**（改一个字符就能复现，见 tests/pick.test.ts 的
 * 逐格实测：俯视机位改判 24%，正面只有 0.7%——正/背面人工验收看不出来）。
 * 这与 labels.update 里那条遮挡剔除的非递归要求同源，理由都写在 scenery.ts 的模块注释。
 *
 * `raycaster` / `ndc` / `camera` 由调用方传入并复用：交互期本函数调用频繁，
 * 没必要每次新建三个对象。
 */
/** 渲染分辨率上限（见 createScene 里的说明）。调高更清晰、更吃帧。 */
export const MAX_PIXEL_RATIO = 1.5

/**
 * 肌肉整体的不透明度（用户要的"30% 透明"）。
 *
 * 乘在 `palette` 给出的不透明度上，于是**各类状态之间的相对关系不变**——
 * 只是整体更透，能透出内部的星点与解剖背景。
 *
 * ⚠ 它不改语义：`emissive` 仍是恢复度的编码通道，透明度只是观感层。
 */
export const MUSCLE_OPACITY = 0.3

/**
 * 线框态（无记录）的不透明度**下限**。
 *
 * 未知态本身是 `0.35`，直接乘 `MUSCLE_OPACITY` 会变成 `0.105` —— 几乎看不见，
 * 于是它就不再"可辨识"了，而"未知必须与任何数值可区分"是 spec §5.4 的硬要求
 * （不是审美偏好）。所以给线框态垫一个下限，让它始终读得出来。
 */
export const WIREFRAME_OPACITY_FLOOR = 0.18

export const NON_MUSCLE_EMISSIVE_INTENSITY = 0.85
/** 心脏的不透明度。**不能是 1** —— 用户要"带不透明度"，要能透出它在胸腔里。
 *
 * 调过三次，记下来免得来回摇摆：0.85 →（要求减半）0.425 →（要求加回来一些）0.6。
 * 0.6 是"看得清是红的、又不至于挡住胸肌"的折中。 */
export const NON_MUSCLE_OPACITY = 0.6

/**
 * 一块肌肉的**基色**。
 *
 * `applyStates` 与 `setHover` 都必须走这里。`setHover` 会把所有**未被悬停**的块
 * 写回"基色"——那里若写死 `BASE_COLOR`，悬停任意一块肌肉都会把心脏的红抹成灰。
 */
export function blockColor(style: string | undefined): string {
  return style === 'non-muscle' ? NON_MUSCLE_COLOR : BASE_COLOR
}

/**
 * 特定肌群的**不透明度**系数。
 *
 * `1` = 不变；`0.7` = 不透明度变成原来的 70%，即**更透**。
 * 例：base 0.30 × 0.7 = 0.21。
 *
 * ⚠ 命名：初版叫 `TRANSPARENCY_SCALE` 并写成 `1-(1-base)*scale`（按"透明度"
 * 算），方向是反的 —— 0.3 会变成 0.51（更实）而不是 0.21（更透）。
 * 用户要的是**不透明度**乘系数。名字留"transparency"会让人继续踩这个反向坑，
 * 故改名为 OPACITY_SCALE 并直接乘。
 */
export const OPACITY_SCALE: Record<string, number> = {
  core: 0.7,
  obliques: 0.7,
}

/**
 * 可交互的 mesh —— **只有 28 个肌群块**。
 *
 * `other`（478 个筋膜/滑囊/肋间肌合并的中性网格）与外壳**必须排除**：
 *   · 点选拿到的若是个没有 `muscleId` 的东西，会被当作"点了空白"而清掉选中
 *     —— 表现就是"肌肉选不中了"
 *   · 标签的遮挡判定 `hits.some(h => h.object.userData.muscleId !== it.id)`
 *     会把它们当成遮挡物，于是**所有标签一起被判成被遮挡、整屏变暗**
 * 这两处正是引入真实模型后新出现的失效面。
 */
export function interactiveMeshes(body: THREE.Group): THREE.Mesh[] {
  return body.children.filter((c) => c.userData.muscleId) as THREE.Mesh[]
}

export function pickMuscleId(
  body: THREE.Group,
  raycaster: THREE.Raycaster,
  ndc: THREE.Vector2,
  camera: THREE.Camera,
): string | null {
  return musclesUnderCursor(body, raycaster, ndc, camera)[0] ?? null
}

/**
 * 射线沿线上**所有**肌群，按由近及远去重。
 *
 * 为什么要它：真实解剖是**分层**的，深层肌肉被浅层盖住，射线永远只打得到最前面那块。
 * 实测腹直肌就是这种情况 —— 腹外斜肌的**腱膜**（腹直肌鞘前层）在解剖上就覆盖在它前面，
 * 所以从正面点肚子拿到的全是 `obliques`（x 扫描 9 个采样点里 8 个如此，中线那个是 `core`）。
 * 这不是 bug，是"深层结构点不到"。配合 `nextPickIndex` 让同一点重复点击依次深入。
 */
export function musclesUnderCursor(
  body: THREE.Group,
  raycaster: THREE.Raycaster,
  ndc: THREE.Vector2,
  camera: THREE.Camera,
): string[] {
  raycaster.setFromCamera(ndc, camera)
  // 非递归（第三个参数 false）**且只打可交互 mesh** —— 两个条件都不可省，
  // 理由分别见本函数上方与 scenery.ts / load-model.ts 的模块注释。
  const hits = raycaster.intersectObjects(interactiveMeshes(body), false)
  const out: string[] = []
  for (const h of hits) {
    const id = h.object.userData.muscleId as string | undefined
    if (id && !out.includes(id)) out.push(id) // 同一 id 的多个面只算一次
  }
  return out
}

/** 上一次点选的位置与列表。**原地再点一下 = 往深一层。** */
export interface PickState {
  x: number
  y: number
  ids: string[]
  index: number
}

/**
 * 「再点一下往深一层」的决策（纯函数，可单测）。
 *
 * 同一个屏幕位置、同一串候选 → 序号 +1 并回绕；否则重新从最前面那块开始。
 * 位置容差是因为人手不可能点在同**一个**像素上。
 */
export function nextPickIndex(
  prev: PickState | null,
  x: number,
  y: number,
  ids: string[],
  tol = 6,
): PickState {
  if (!ids.length) return { x, y, ids, index: -1 }
  const same =
    prev !== null &&
    Math.abs(prev.x - x) <= tol &&
    Math.abs(prev.y - y) <= tol &&
    prev.ids.length === ids.length &&
    prev.ids.every((v, i) => v === ids[i])
  const index = same ? (prev!.index + 1) % ids.length : 0
  return { x, y, ids, index }
}

/** 画布尺寸 → 渲染器尺寸与相机 aspect。抽出来是为了能在 node 里测（无需 GPU）。
 *  零尺寸兜底为 1：否则 aspect = 0/0 = NaN，相机矩阵全废、画面全黑且无报错。 */
export function viewportFor(width: number, height: number): {
  width: number
  height: number
  aspect: number
} {
  const w = width || 1
  const h = height || 1
  return { width: w, height: h, aspect: w / h }
}

/** 焦点（人体中心）。相机朝向由 OrbitControls 从 target 重推，
 *  所以 lookAt 与 controls.target 必须用同一个值——这就是它被提取的原因。 */
export const BODY_FOCUS: [number, number, number] = [0, 0.95, 0]

/**
 * 渲染循环**该不该继续排下一帧**。
 *
 * 三个"停"的条件各自独立：
 *   · `hidden`    —— **系统**说这个页面看不见（切后台 / 锁屏）
 *   · `suspended` —— **应用**说现在不需要（训练执行台全屏盖住了 3D）
 *   · 空闲        —— 本档没有常驻动效、也没人置脏（`canIdlePause`，只有 `eco` 为真）
 *
 * ⚠ **`hidden` 与 `suspended` 不合并。** 合并之后"回前台"会把挂起一起清掉 ——
 * 执行台还盖着，3D 却开始满速渲染（`onVisibility` 回前台时会 `requestRender()`）。
 * 抽成纯函数是为了能在 node 里断言：`loop` 本体要 WebGL 才能跑。
 */
export function shouldContinueLoop(opts: {
  hidden: boolean
  suspended: boolean
  /** 本档能不能空闲停帧（`perf.ts::canIdlePause`） */
  idlePause: boolean
  /** 有没有人置过脏标记 */
  needsRender: boolean
}): boolean {
  if (opts.hidden || opts.suspended) return false
  return !(opts.idlePause && !opts.needsRender)
}

/**
 * 挂起期间收到渲染请求时，**要不要唤醒循环**。答案恒为"不"。
 *
 * ⚠ 这一行是 `setSuspended` 存在的**全部理由**：脏标记照记（恢复后要重绘），
 * 但唤醒被抑制 —— 否则执行台盖上去之后 GPU 照样满速跑，挂起等于没做。
 * 之所以抽出来：它是产品决策，值得有一条会红的断言，而不是埋在闭包里
 * （同 `viewportFor` 被抽出来的理由）。
 */
export function shouldWakeOnRender(suspended: boolean): boolean {
  return !suspended
}

/** 正/背面取景。纯函数，可在 node 里断言；Task 9 的"正面/背面"按钮走它，
 *  保证点"正面"回到的正是场景加载时的姿态。 */
export function framingFor(view: 'front' | 'back'): {
  position: [number, number, number]
  target: [number, number, number]
} {
  return {
    position: [0, 1.5, view === 'back' ? -2.6 : 2.6],
    target: [...BODY_FOCUS],
  }
}

export interface SceneHandle {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  body: THREE.Group
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  /** 半透明外壳（只渲染背面）。模型加载失败回落到体块时为 null */
  shell: THREE.Object3D | null
  /** 星场（每块肌肉表面的星点）。数据驱动它的 drawRange/尺寸/不透明度 */
  stars: THREE.Group
  /** 几何是否来自真实肌肉模型（false = 回落到了代码生成的体块） */
  fromModel: boolean
  resize(): void
  /**
   * 请求渲染一帧。**停帧之后唯一的唤醒方式**。
   *
   * ⚠ 所有会改变画面输出的路径都必须调它：材质变了、相机动了、数据到了。
   * 漏一处就是"数据到了但屏幕不动" —— 静默失效，正是本仓最防的那类 bug。
   */
  requestRender(): void
  /** 档位变化：像素比 + 动效开关（尾流层由调用方另行联动，见 perf.ts）。 */
  setTier(tier: Tier): void
  /**
   * 挂起 / 恢复渲染。**训练执行台全屏盖住 3D 时用**。
   *
   * 与 `document.hidden` 那条自停路径是**两个独立条件**（见 `shouldContinueLoop`）：
   * `hidden` 是"系统说看不见"，这里是"应用说现在不需要"。
   *
   * ⚠ 为什么不能只靠 `canIdlePause`：那是**派生**量，只有 `eco` 档为真 ——
   * 桌面上的 `full` 档有心跳与星点闪烁，循环会一直跑。执行台盖上去时 GPU 照样
   * 在后台满速渲染，这正是本方法存在的理由。
   */
  setSuspended(suspended: boolean): void
  dispose(): void
}

export interface SceneOptions {
  /** 开局档位。缺省 `full` —— 探测结果由调用方传入（见 perf.ts::detectInitialTier）。 */
  tier?: Tier
  /**
   * 每帧回调（帧间隔毫秒）。⚠ 停帧期间**不会**被调用 ——
   * 采样方必须在自己停帧/恢复的边界上 `FpsWindow.reset()`，
   * 否则恢复后第一帧的巨大间隔会被算成卡顿，刚唤醒就降档。
   */
  onFrame?: (frameMs: number) => void
}

export async function createScene(
  canvas: HTMLCanvasElement,
  options: SceneOptions = {},
): Promise<SceneHandle> {
  /** 当前性能档位。开局由能力探测给定，运行中由 governor 调整（见 perf.ts）。 */
  let tier: Tier = options.tier ?? 'full'
  let settings = TIER_SETTINGS[tier]

  const scene = new THREE.Scene()
  // **刻意不设 scene.background。** 画布底下压着一层拖拽尾流（`#fluid`），
  // 设了不透明背景就会把它整层盖死。底色改由 html/body 的 `#0f1419` 提供，
  // 视觉结果不变（见 index.html 的叠层说明与 styles.css 的 #app/#fluid）。
  scene.background = null

  const initial = framingFor('front')
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(...initial.position)
  camera.lookAt(...initial.target)

  // **alpha: true 是拖拽尾流那层能透出来的前提**（配合上面的 scene.background=null）。
  // 两者缺一，背景层就被这块画布整个盖住 —— 页面看着"没生效"，且不报任何错。
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true })
  renderer.setClearColor(0x000000, 0)
  // **渲染分辨率上限。** 原先是 2，在 4K + 高 DPI 下 backing store 可达 3300 万像素；
  // 而本场景有 shell / 背景组织 / 27 块半透明肌肉 / 星场好几层叠加 ——
  // 半透明丢掉 early-z，每片元都要混合，片元开销随像素量线性上涨。
  // 1.5 在视网膜屏上肉眼几乎无差（canvas 是 3D 内容，不是文字），像素量却砍掉一半多。
  //
  // 2026-09-17：取值改由**档位表**给（perf.ts 的 TIER_SETTINGS.pixelRatio）；
  // `MAX_PIXEL_RATIO` 退化为 full 档的取值来源，保留导出供既有引用。
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, settings.pixelRatio))

  // 三点布光：主光偏右前，补光偏左后，顶光提轮廓
  const key = new THREE.DirectionalLight(0xffffff, 2.0)
  key.position.set(1.4, 2.4, 2.0)
  const fill = new THREE.DirectionalLight(0x9fb4d0, 0.8)
  fill.position.set(-1.8, 1.2, -1.2)
  const rim = new THREE.DirectionalLight(0xffffff, 0.6)
  rim.position.set(0, 2.6, -2.2)
  scene.add(key, fill, rim, new THREE.AmbientLight(0x404a56, 1.2))

  // 真实肌肉模型（Z-Anatomy, CC BY-SA 4.0）。失败会回落到代码生成的体块，
  // 所以这里不会因为 404 / 损坏而白屏。
  const { group: body, shell, fromModel } = await loadBody()
  scene.add(body)
  // 星场：恢复度的第二条编码通道（补 emissive 在零线归零造成的非单调）。
  // 只在加载时建一次；数据变化只改 drawRange/尺寸/不透明度，不重建几何。
  const stars = buildStarField(body)
  scene.add(stars)

  // 用户在系统里开了"减少动效"：星点**不闪**（但仍在，静态星云一样能读出密度），
  // 心脏**不呼吸**（但辉光仍在）。两处共用这一个判断，不各调一次。
  const reduced = prefersReducedMotion()

  // **首帧之前先落一次"还没有数据"的状态。**
  // 不落的话第一帧用的是加载期的初始材质：星点已由 buildStarField 归零，但肌肉
  // 仍是 assembleBody 给的 `color: OTHER_TISSUE_COLOR, opacity: 1` —— 一具**不透明**
  // 的深灰人体，等第一次 applyStates 才变成半透明彩色，中间那一下是明显的跳变。
  // `emptyMap` 正是"没有数据"的表示（加载失败路径也用它），所以这里不需要新的特例。
  // 它的 `days` 字段不参与渲染，真实数据到达后立刻被覆盖。
  applyStates(body, emptyMap(0))
  applyStars(stars, emptyMap(0))

  // 心脏辉光。**加在 scene 上而不是 body.children 里** —— 与星场同一个理由：
  // applyStates / setHover / interactiveMeshes / buildStarField 都会遍历
  // body.children，多一个 Sprite 进去就得逐处加过滤，且点选射线可能打到它。
  //
  // ⚠ 用 `filter` 取全部、而不是 `find` 取第一个。实测当前装配结果里心脏**只有
  // 1 块**（`build-muscles.mjs` 合并过），两者等价；这么写是为了不依赖"合并后
  // 恰好只剩一块"这个巧合 —— 合并策略一变，find 会静默只照着一小瓣摆光斑。
  const hearts = body.children.filter((c) => c.userData.muscleId === 'cardio_system')
  const glow = hearts.length ? buildHeartGlow(hearts) : null
  if (glow) scene.add(glow)
  // 减少动效时只留静态辉光（`reduced` 在上面与星点共用同一次判断）
  let breathe = glow !== null && !reduced
  /** 星点是否在闪。由 applyMotion 单点推导（理由见下）。 */
  let ticking = false

  /**
   * 动效开关的**唯一**收口：星点闪烁与心脏呼吸都从这里推导。
   *
   * ⚠ 不要在别处各写一份判据。本仓吃过"同一份数据两个产出点、差异不报错
   * 只静默降级"的亏（见 clarify_options 的收口记录）；这里两个动效必须同时
   * 跟着档位走，分开写迟早出现"闪烁关了、呼吸还开着"这种半降级。
   */
  function applyMotion(): void {
    const twinkle = !reduced && settings.starTwinkle
    setStarMotion(stars, twinkle)
    ticking = twinkle
    breathe = glow !== null && !reduced && settings.heartBreathe
  }
  applyMotion()

  // 地面网格线**已移除**（用户口径「把 3d 脚下的网格线去掉」）。
  // 原先那圈 GridHelper 是把模型锚在地上的"地板"，但它同时也在深底上画出一片
  // 可见的格网，和新加的流体背景层叠在一起显得脏。体积感改由三点布光 + 心脏辉光
  // 提供——模型悬在虚空里才是这套视觉想要的。别再把它加回来。

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.target.set(...initial.target)
  controls.enableDamping = true
  controls.minDistance = 1.2
  controls.maxDistance = 6
  controls.update()

  function resize(): void {
    const vp = viewportFor(canvas.clientWidth, canvas.clientHeight)
    renderer.setSize(vp.width, vp.height, false)
    camera.aspect = vp.aspect
    camera.updateProjectionMatrix()
  }
  resize()
  // ⚠ resize 后必须**请求渲染**：停帧期间尺寸变化不会自己重画，会留一片空白
  // 且不报错。这是"停帧"引入的新失效面，漏掉它就是白屏。
  const ro = new ResizeObserver(() => {
    resize()
    requestRender()
  })
  ro.observe(canvas)

  let raf = 0
  /**
   * 挂起标志（训练执行台全屏时置真）。见 `SceneHandle.setSuspended`。
   * ⚠ 它**不**参与"脏标记"的语义：`requestRender()` 照常置 `needsRender`，
   * 只是不唤醒循环 —— 恢复时才能把挂起期间攒下的变化一次画出来。
   */
  let suspended = false
  /**
   * 置脏标记。⚠ **所有**会改变画面输出的路径都要走 `requestRender()` ——
   * 停帧之后没人替你把画面刷新过来。
   */
  let needsRender = true
  let lastFrameAt = performance.now()

  function stopLoop(): void {
    if (raf !== 0) {
      cancelAnimationFrame(raf)
      raf = 0
    }
  }

  function startLoop(): void {
    if (raf !== 0) return
    // ⚠ 重置基准：停帧期间攒下的间隔会让恢复后的第一帧 dt 巨大
    lastFrameAt = performance.now()
    raf = requestAnimationFrame(loop)
  }

  function requestRender(): void {
    needsRender = true
    // 挂起期间只记脏标记，**不唤醒循环**（否则挂起等于没做）
    if (!shouldWakeOnRender(suspended)) return
    if (raf === 0) startLoop()
  }

  // 相机变化（含阻尼收敛的每一帧）→ 请求渲染。
  // 靠这个闭环，交互停下后阻尼会把剩余的帧"用光"，然后循环自然停 ——
  // 不需要额外为阻尼留一个"再跑 N 帧"的定时器。
  controls.addEventListener('change', requestRender)

  function loop(): void {
    // 先消费置脏标记：帧内若又有人 requestRender（例如上面的 change 回调），
    // 标记会被重新置 true，于是这一帧结束后继续排下一帧。
    needsRender = false
    const now = performance.now()
    const frameMs = now - lastFrameAt
    lastFrameAt = now
    options.onFrame?.(frameMs)
    // 呼吸：光斑改尺寸+不透明度、心脏本体改自发光。都很便宜（一次 set + 写字段），
    // 且都在基准值上重算，不累积。
    if (breathe) {
      pulseGlow(glow!, now)
      pulseHeartEmissive(hearts, now, NON_MUSCLE_EMISSIVE_INTENSITY)
    }
    // 闪烁：只写一个**共享** uniform 的 value —— 28 个星点材质引用同一批对象
    // （见 star-field.ts），所以这里是 O(1) 而不是 O(材质数)。
    if (ticking) tickStars(stars, now)
    controls.update()
    renderer.render(scene, camera)

    // **空闲停帧**：本档没有常驻动效、也没人置脏 → 不再排下一帧。
    // full / lite 档有心跳或闪烁在动，canIdlePause 为 false，行为与改动前一致。
    // 挂起（执行台盖住 3D）与页面隐藏各自独立成一条 —— 见 shouldContinueLoop。
    if (!shouldContinueLoop({
      hidden: document.hidden,
      suspended,
      idlePause: canIdlePause(tier),
      needsRender,
    })) {
      raf = 0
      return
    }
    raf = requestAnimationFrame(loop)
  }

  /**
   * 页面切后台就停。
   *
   * ⚠ 手机上是**大头**：切走 / 锁屏后浏览器会限流 rAF，但"限流"不等于"停"，
   * 仍然在耗电。显式停掉更干净，回前台再按需恢复。
   */
  function onVisibility(): void {
    if (document.hidden) {
      stopLoop()
      return
    }
    // 回前台渲染一帧确认画面正确；eco 档若无事可做会随即自然停
    requestRender()
  }
  document.addEventListener('visibilitychange', onVisibility)

  startLoop()

  return {
    scene,
    camera,
    body,
    renderer,
    controls,
    shell,
    stars,
    fromModel,
    resize,
    requestRender,
    setTier(next: Tier): void {
      tier = next
      settings = TIER_SETTINGS[tier]
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, settings.pixelRatio))
      resize()
      applyMotion()
      requestRender()
    },
    setSuspended(next: boolean): void {
      if (suspended === next) return          // 幂等：重复调用不产生额外动作
      suspended = next
      if (next) {
        // 立刻停掉已排的那一帧。loop 结尾也有 suspended 判断 —— **两道都要**：
        // 只靠 stopLoop 的话，若这一帧已经在跑（不在队列里，cancel 不掉），
        // 得等它自然结束；两道一起才能立刻停。
        stopLoop()
        return
      }
      // 恢复：清挂起 + 请求一帧。挂起期间的脏标记可能早被消费掉了，
      // 不主动请求就可能留一屏挂起前的旧画面。
      requestRender()
    },
    dispose(): void {
      stopLoop()
      document.removeEventListener('visibilitychange', onVisibility)
      controls.removeEventListener('change', requestRender)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
    },
  }
}
