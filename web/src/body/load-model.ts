import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { build } from './build'
import { styleOf } from './layout'
import MAP_DOC from './muscle-map.json'

/**
 * 加载 Z-Anatomy 真实肌肉模型，返回**与 `build()` 同契约**的 Group。
 *
 * "同契约"是这次能保持小范围的关键：每个肌群 mesh 带 `userData.muscleId`，
 * 于是 `applyStates` / 标签 / 悬停 / 点选**一行都不用改**——它们只认这个字段，
 * 不关心几何是代码生成的还是从 glb 来的。
 *
 * 模型来源：Z-Anatomy（**CC BY-SA 4.0**），见 muscle-map.json 的 attribution。
 * 构建管线：`scripts/build-muscles.mjs`。
 */

/** 28 个 canonical id —— 与后端 `/v1/muscle-map` 的键集一致。 */
export const MUSCLE_IDS: string[] = Object.keys(MAP_DOC.map)

/** 名字 → 是不是肌群 id。非肌群的（other / 外壳）不带 muscleId，于是点选命中不到。 */
export function muscleIdOf(name: string): string | null {
  return MUSCLE_IDS.includes(name) ? name : null
}

/**
 * 本仓的坐标系基准身高（与 `layout.ts` 的包围盒断言同一常量）。
 * 真实模型的单位与它无关，必须归一化过来。
 */
export const BODY_HEIGHT = 1.8

/**
 * 背景组织（`other`）的颜色。**刻意远离恢复度色轴**（`palette` 的基色是 `#8a8f96`，
 * 冷端偏青、暖端偏金）。用一块压暗的中性灰，读起来像"背景"，不像一个恢复度数值。
 */
export const OTHER_TISSUE_COLOR = 0x2f3439

/**
 * 把任意来源的模型缩放到 `BODY_HEIGHT` 并落到地面（min.y = 0）。
 *
 * **这一步不可省。** 实测 Z-Anatomy 的 FBX 是**厘米单位**（bbox y 跨度 170.65），
 * 不归一化的话模型比我们的坐标系大 ~95 倍 —— 相机会落在模型脚里面，画面近乎全黑，
 * 而且**不会有任何报错**（几何本身是好的）。
 *
 * 返回缩放系数，供测试断言。
 *
 * **幂等**：按"当前还需要缩放多少倍"来乘，而不是把 scale 设成那个倍数。
 * 用 `setScalar` 的话第二遍量到的 h 已是 1.8、s = 1，于是把上一遍的缩放**重置掉** ——
 * 模型悄悄变回 172 单位（厘米原尺寸），画面近乎全黑且不报错。
 * （本函数目前只在 `assembleBody` 末尾调一次，但静默失败代价太高，值得挡住。）
 */
export function normalizeToBodyHeight(root: THREE.Object3D, target = BODY_HEIGHT): number {
  root.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(root)
  const h = box.max.y - box.min.y
  if (!Number.isFinite(h) || h <= 0) return 1
  const s = target / h
  // 乘而非设：见上方"幂等"。装配出的 group 初始 scale 为 1，故生产路径上两者等价。
  root.scale.multiplyScalar(s)
  // 落到地面：缩放后重新量一次，把 min.y 平移到 0
  root.updateMatrixWorld(true)
  const box2 = new THREE.Box3().setFromObject(root)
  root.position.y -= box2.min.y
  return s
}

export interface LoadedBody {
  group: THREE.Group
  /** 外壳（供 Task 9 的可见性开关用）；模型里没有外壳时为 null */
  shell: THREE.Object3D | null
  /** 模型文件是否真的加载成功——失败时会回落到代码生成的体块 */
  fromModel: boolean
}

/** 从已解析的 glTF 场景图装配出 body group。**纯函数式，可在 node 里测**（不需要 WebGL）。 */
export function assembleBody(scene: THREE.Object3D): LoadedBody {
  const group = new THREE.Group()
  group.name = 'muscle-body'

  let shell: THREE.Object3D | null = null

  // **先收集、再处理。** 不能在 traverse 的回调里 group.add(mesh) ——
  // three 的 add 会把 mesh 从原父节点摘掉，于是 scene.children 在遍历途中被改动、
  // 数组位移，遍历到 undefined 就抛「Cannot read properties of undefined」。
  const meshes: THREE.Mesh[] = []
  scene.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh)
  })

  for (const mesh of meshes) {
    // 模型里**只有 position**（构建期刻意去掉法线，好让顶点能焊接、减面才生效）。
    // 这里补回来 —— 焊接后的共享顶点会得到平滑法线，正是肌肉该有的样子。
    if (!mesh.geometry.attributes.normal) mesh.geometry.computeVertexNormals()

    if (mesh.name === 'shell') {
      shell = mesh
      continue // 外壳单独处理（见下），不进肌肉循环
    }

    const id = muscleIdOf(mesh.name)
    // 每个 mesh **独立材质** —— applyStates 逐块改色，共享材质会让"改一块"变成"改全部"。
    // glb 里所有 primitive 共用一份材质，所以这里必须克隆。
    mesh.material = new THREE.MeshStandardMaterial({
      color: OTHER_TISSUE_COLOR,
      emissive: 0x000000,
      emissiveIntensity: 0,
      roughness: 0.55,
      metalness: 0.05,
      transparent: true,
      opacity: 1,
    })
    mesh.userData.muscleId = id ?? undefined
    // **style 走 layout 的声明，而不是一律 'muscle'。** 原先写 `id ? 'muscle' : 'other'`，
    // 于是 'non-muscle' 在这条路径上永不出现 —— 心脏（cardio_system）会与骨骼肌
    // 渲染得一模一样，违反 spec §4.4。见 layout.ts 的 styleOf。
    mesh.userData.style = id ? styleOf(id) : 'other'

    if (!id) {
      // `other` = 其余 478 个网格（筋膜/滑囊/**肋间肌/髂胫束**…）合并成的背景组织。
      //
      // 两处必须特殊处理：
      //  1. **不写深度、且先画**（renderOrder -1）。因为其中有些结构在解剖上就**在我们
      //     着色的肌肉外面**——髂胫束包着 quadriceps、肋间肌盖着 pectorals。
      //     正常写深度的话它们会**盖住**那些肌肉，表现就是"穿模 / 肌肉看不见也选不中"。
      //     关掉深度写入后，肌肉永远画在它们之上；没有肌肉的地方才露出它们。
      //  2. **颜色必须离恢复度色轴远远的**（见 OTHER_TISSUE_COLOR）。原先用 0x8a8f96，
      //     那恰好是 palette(0.5) 的基色 —— 一块灰组织挨着一块彩肌，**看起来就像
      //     那块肌肉恢复到 50%**，直接违反 spec §5.4「未知不得与任何数值混淆」。
      mesh.material.depthWrite = false
      mesh.renderOrder = -1
      // **必须留在不透明批次**（transparent: false）。transparent 会把它排到
      // 透明批次 —— 而透明批次跑在已知肌肉的**不透明批次之后**，于是它靠着深度测试
      // 仍会盖住位于它后面的肌肉，白改一场。不透明 + 不写深度 + 排最前，
      // 才是"肌肉永远画在它之上"的完整条件。
      mesh.material.transparent = false
    }
    group.add(mesh)
  }

  if (shell) {
    const s = shell as THREE.Mesh
    // 外壳：**只渲染背面**。正面不画 → 不挡视线、且射线打不到正面
    //（标签的遮挡判定靠射线，多一层正面会把它整片判成"被遮挡"）。
    s.material = new THREE.MeshStandardMaterial({
      color: 0x5a6b7d,
      roughness: 0.9,
      metalness: 0,
      transparent: true,
      opacity: 0.18,
      side: THREE.BackSide,
      depthWrite: false,
    })
    s.userData.style = 'shell'
    s.userData.muscleId = undefined
    s.renderOrder = -2 // 最先画；不写深度所以不会挡住肌肉（见 other 的同类处理）
    s.name = 'shell'
    group.add(s)
  }

  // **归一化必须在装配之后**（不能对源 scene 设 scale 再往里搬）：
  // three 的 add 是"重新挂父节点"，**不保留世界变换**（要保记得用 attach），
  // 所以设在源 scene 上的缩放会被丢掉 —— 实测这样写模型会保持厘米尺寸，
  // 比我们的坐标系大 95 倍，画面近乎全黑且不报错。
  normalizeToBodyHeight(group)

  return { group, shell, fromModel: true }
}

/** 加载 glb。URL 走 Vite 的 `BASE_URL`，这样 `base: '/app/'` 下也解析得到。 */
export async function loadBodyFromModel(): Promise<LoadedBody> {
  const url = `${import.meta.env.BASE_URL}models/muscles.glb`
  const gltf = await new GLTFLoader().loadAsync(url)
  return assembleBody(gltf.scene)
}

/**
 * 加载真实模型；**失败时回落到代码生成的体块**。
 *
 * 回落不是可选的礼貌——模型是 4MB 的外部资源，可能 404（未构建）、可能损坏。
 * 没有回落的话，一次加载失败就是一片空白；有回落则至少还能看到体块版。
 */
export async function loadBody(): Promise<LoadedBody> {
  try {
    return await loadBodyFromModel()
  } catch (err) {
    console.warn('肌群模型加载失败，回落到代码生成的体块', err)
    return { group: build(), shell: null, fromModel: false }
  }
}
