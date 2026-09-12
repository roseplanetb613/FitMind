import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { build } from '../body/build'
import { muscleState, type MuscleMapData } from '../data/types'
import { palette } from './palette'

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
    // 走 muscleState 而不是 data.muscles[id] —— 缺键与显式 null 都要归一成 null，
    // 否则缺键的 undefined 会被当成"有数值"走进 recovery 分支（见 types.ts 的说明）
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

  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(0.9, 1.5, 2.6)
  camera.lookAt(0, 0.95, 0)

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
  controls.target.set(0, 0.95, 0)
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
