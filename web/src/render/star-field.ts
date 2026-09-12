import * as THREE from 'three'
import { muscleState, type MuscleMapData } from '../data/types'
import { MAX_STARS, starSpec } from './particles'

/**
 * 星场：每块肌肉表面浮着一层星点，**密度/尺寸/亮度随恢复度**。
 *
 * 这是恢复度的第二条编码通道，补上 `emissive` 那条的缺口（渲染亮度在零线归零、
 * 不是单调函数）。语义映射本身在 `particles.ts` 的纯函数里，**本文件只负责画**。
 *
 * 形态选择：
 *   · **加性混合**（AdditiveBlending）—— 星点叠在一起会更亮，像真的星云
 *   · `depthWrite: false` —— 星点不写深度，所以不会挡住彼此或肌肉本体
 *   · 星场是**独立的 Group**（`scene.add(stars)`），**不在 `body.children` 里** ——
 *     这一点是承重的：`interactiveMeshes` 与 `applyStates` 都只遍历 `body.children`，
 *     所以它们天然看不到星点。否则点选会命中一个没有实体的点云，
 *     而 `applyStates` 会试图往 `PointsMaterial` 上设 `wireframe` 这种它没有的属性。
 *     星点自己带 `userData.muscleId`，那是给 `applyStars` 找目标用的，不是给点选的。
 */

/** 确定性伪随机（同 seed 同序列）—— 星点位置抖动必须可复现，不能用 Math.random */
function rng(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s ^= s << 13
    s ^= s >>> 17
    s ^= s << 5
    return ((s >>> 0) % 100000) / 100000
  }
}

function hashSeed(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/**
 * 在 mesh 表面采样 `MAX_STARS` 个点（局部坐标，已含 mesh 自身变换）。
 *
 * 直接跳着取顶点 —— 减面后的肌群网格顶点数远多于 MAX_STARS，
 * 均匀步长采样就足够散开，不需要真的做面积加权。
 */
export function sampleSurface(mesh: THREE.Mesh, maxCount = MAX_STARS): Float32Array {
  const pos = mesh.geometry.attributes.position
  const n = pos.count
  const take = Math.min(maxCount, n)
  const out = new Float32Array(take * 3)
  const v = new THREE.Vector3()
  const step = n / take
  const jitter = rng(hashSeed(mesh.userData.muscleId ?? mesh.name ?? 'x'))
  for (let i = 0; i < take; i++) {
    const idx = Math.min(n - 1, Math.floor(i * step))
    v.fromBufferAttribute(pos, idx)
    // 极小的抖动：避免多个星点正好落在同一顶点上（会看起来像一个大点）
    v.x += (jitter() - 0.5) * 0.004
    v.y += (jitter() - 0.5) * 0.004
    v.z += (jitter() - 0.5) * 0.004
    v.applyMatrix4(mesh.matrix)
    out[i * 3] = v.x
    out[i * 3 + 1] = v.y
    out[i * 3 + 2] = v.z
  }
  return out
}

/**
 * 为 `body` 里每个肌群 mesh 建一层星点，返回一个 Group。
 * **只在加载时建一次**（与几何同一条约定：数据只驱动材质/绘制范围，不重建几何）。
 */
export function buildStarField(body: THREE.Group, maxCount = MAX_STARS): THREE.Group {
  const field = new THREE.Group()
  field.name = 'star-field'
  // `sampleSurface` 用的是 `mesh.matrix`（局部变换），而它由 updateMatrix() 写入 ——
  // 那要到第一次渲染/updateMatrixWorld 才发生。这里显式刷一次，否则采到的点
  // 全落在"变换前"的位置上（模型是 glb 来的，各 mesh 的 position 不为零）。
  body.updateMatrixWorld(true)

  for (const child of body.children) {
    const mesh = child as THREE.Mesh
    const id = child.userData.muscleId as string | undefined
    if (!mesh.isMesh || !id) continue

    const points = new THREE.Points(
      new THREE.BufferGeometry().setAttribute(
        'position', new THREE.BufferAttribute(sampleSurface(mesh, maxCount), 3),
      ),
      new THREE.PointsMaterial({
        color: 0xbfe3ff, // 冷白偏蓝 —— 与 palette 的暖金/冷青都不同族，不会被读成恢复度色
        size: 3,
        sizeAttenuation: true,
        transparent: true,
        opacity: 1,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    )
    points.userData.muscleId = id
    points.userData.isStars = true
    points.renderOrder = 2 // 画在肌肉与外壳之上
    points.frustumCulled = false
    field.add(points)
  }
  return field
}

/**
 * 数据 → 星点。**只改 `drawRange` / 尺寸 / 不透明度，不重建几何**。
 *
 * 无记录的肌群 `starSpec(null).count === 0` → `drawRange` 为 0 → 一颗不画，
 * 于是"没有记录"与"恢复 0%"在视觉上分得开（后者仍有极少几颗暗星）。
 */
export function applyStars(field: THREE.Group | null, data: MuscleMapData): void {
  if (!field) return
  for (const child of field.children) {
    const id = child.userData.muscleId as string | undefined
    if (!id) continue
    const points = child as THREE.Points
    const mat = points.material as THREE.PointsMaterial
    const state = muscleState(data, id)
    const spec = starSpec(state && state.has_record ? state.recovery : null)

    points.geometry.setDrawRange(0, spec.count)
    mat.size = spec.size
    mat.opacity = spec.opacity
    mat.needsUpdate = true
  }
}
