import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { build } from '../body/build'
import { muscleState, type MuscleMapData } from '../data/types'
import { BASE_COLOR, HOVER_COLOR, palette } from './palette'

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
    // 统一走 muscleState（types.ts 的单源归一入口），使"缺键 / 显式 null / 有记录"
    // 三种情形只有一个判据。注意：本行的 `&&` 短路已经能兜住缺键的 undefined，
    // 所以这里不是非它不可——不依赖那个巧合才是理由。
    const state = muscleState(data, id)
    const entry = palette(state && state.has_record ? state.recovery : null)

    mat.color.set(entry.baseColor)
    mat.emissive.set(entry.emissive)
    mat.emissiveIntensity = entry.emissiveIntensity
    mat.wireframe = entry.style === 'wireframe'
    // spec §4.4：心脏块用半透明外壳与骨骼肌在视觉上区分。
    // **必须在这里乘系数**——palette 对任何有限 recovery 都给 opacity: 1。
    const nonMuscle = child.userData.style === 'non-muscle'
    mat.opacity = nonMuscle ? entry.opacity * 0.45 : entry.opacity
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
 * 残留风险（无头环境测不到）：高亮期间那一块的基色变亮，理论上可能被读成
 * "恢复度更高"；靠悬停态同时带来的标签加粗/上浮来消歧（见 labels.setHovered）。
 */
export function setHover(group: THREE.Group, id: string | null): void {
  for (const child of group.children) {
    const mid = child.userData.muscleId as string | undefined
    if (!mid) continue // 装饰 Group 无 muscleId：不参与高亮，也不会被当成悬停目标
    const mat = (child as THREE.Mesh).material as THREE.MeshStandardMaterial
    mat.color.set(mid === id ? HOVER_COLOR : BASE_COLOR)
  }
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
  resize(): void
  dispose(): void
}

export function createScene(canvas: HTMLCanvasElement): SceneHandle {
  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#0f1419')

  const initial = framingFor('front')
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(...initial.position)
  camera.lookAt(...initial.target)

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

  // 三点布光：主光偏右前，补光偏左后，顶光提轮廓
  const key = new THREE.DirectionalLight(0xffffff, 2.0)
  key.position.set(1.4, 2.4, 2.0)
  const fill = new THREE.DirectionalLight(0x9fb4d0, 0.8)
  fill.position.set(-1.8, 1.2, -1.2)
  const rim = new THREE.DirectionalLight(0xffffff, 0.6)
  rim.position.set(0, 2.6, -2.2)
  scene.add(key, fill, rim, new THREE.AmbientLight(0x404a56, 1.2))

  const body = buildBodyGroup()
  scene.add(body)

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
    resize,
    dispose(): void {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
    },
  }
}
