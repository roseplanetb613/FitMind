import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { build } from '../body/build'
import { loadBody } from '../body/load-model'
import { applyStars, buildStarField } from './star-field'
import { buildHeartGlow, prefersReducedMotion, pulseGlow, pulseHeartEmissive } from './glow'
import { emptyMap, muscleState, type MuscleMapData } from '../data/types'
import { BASE_COLOR, HOVER_COLOR, NON_MUSCLE_COLOR, NON_MUSCLE_EMISSIVE, palette } from './palette'

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
  dispose(): void
}

export async function createScene(canvas: HTMLCanvasElement): Promise<SceneHandle> {
  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#0f1419')

  const initial = framingFor('front')
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(...initial.position)
  camera.lookAt(...initial.target)

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
  // **渲染分辨率上限。** 原先是 2，在 4K + 高 DPI 下 backing store 可达 3300 万像素；
  // 而本场景有 shell / 背景组织 / 27 块半透明肌肉 / 星场好几层叠加 ——
  // 半透明丢掉 early-z，每片元都要混合，片元开销随像素量线性上涨。
  // 1.5 在视网膜屏上肉眼几乎无差（canvas 是 3D 内容，不是文字），像素量却砍掉一半多。
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO))

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
  // 用户在系统里开了"减少动效"就不呼吸，只留静态辉光
  const breathe = glow !== null && !prefersReducedMotion()

  // 地面参考——给体积感一个锚，否则模型飘在虚空里
  const grid = new THREE.GridHelper(6, 24, 0x2a323c, 0x1c232b)
  scene.add(grid)

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
  const ro = new ResizeObserver(resize)
  ro.observe(canvas)

  let raf = 0
  const loop = (): void => {
    raf = requestAnimationFrame(loop)
    // 呼吸：光斑改尺寸+不透明度、心脏本体改自发光。都很便宜（一次 set + 写字段），
    // 且都在基准值上重算，不累积。
    if (breathe) {
      pulseGlow(glow!, performance.now())
      pulseHeartEmissive(hearts, performance.now(), NON_MUSCLE_EMISSIVE_INTENSITY)
    }
    controls.update()
    renderer.render(scene, camera)
  }
  loop()

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
    dispose(): void {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
    },
  }
}
