import * as THREE from 'three'
import type { MuscleState } from '../data/types'

/** 标签文案（纯函数，可单测）。星号 = 按次数估算，图例里解释。 */
export function labelText(name: string, state: MuscleState | null): string {
  if (!state || !state.has_record) return `${name} 无记录`
  const pct = Math.round(state.recovery * 100)
  const star = state.confidence === 'low' ? '*' : ''
  return `${name} ${pct}%${star}`
}

/**
 * 按 muscle_id 聚合体块：成对肌群（quadriceps 有 L/R 两块）合成一组，
 * 否则 28 组会变成 50 条标签（两块同色同值，重复标签只是噪声）。
 * 无 muscleId 的子节点（scenery 装饰 Group）被剔除——它们不参与肌群语义。
 */
export function groupByMuscleId(body: THREE.Group): Map<string, THREE.Mesh[]> {
  const byId = new Map<string, THREE.Mesh[]>()
  for (const c of body.children) {
    const id = c.userData.muscleId as string | undefined
    if (!id) continue
    byId.set(id, [...(byId.get(id) ?? []), c as THREE.Mesh])
  }
  return byId
}

/**
 * 一组同 id 体块的锚点：各 mesh 位置的算术平均。
 * 读的是 mesh 的**局部** position。body 组自身不带变换、始终放在场景原点，
 * 所以当前局部坐标 == 世界坐标；将来若给 body 加位移/缩放，这里要改读
 * `getWorldPosition()`，否则标签会错位。
 * 返回新向量（不与任何 mesh.position 别名——那向量是几何真源，原地改写会挪动模型）。
 */
export function anchorFor(meshes: THREE.Mesh[]): THREE.Vector3 {
  const anchor = new THREE.Vector3()
  for (const m of meshes) anchor.add(m.position)
  return anchor.divideScalar(meshes.length)
}

interface LabelItem {
  id: string
  el: HTMLElement
  /** 锚点：该 id 所有 mesh 位置的平均（成对肌群只出一条标签） */
  anchor: THREE.Vector3
}

export interface LabelLayer {
  setStates(states: Record<string, MuscleState | null>): void
  update(camera: THREE.PerspectiveCamera, size: { w: number; h: number }): void
  dispose(): void
}

/**
 * 遮挡剔除：每条标签向相机方向 raycast，被前方体块挡住的淡化。
 * 28 条标签每帧 28 次 raycast，开销可接受（spec §6.1）。
 */
export function createLabelLayer(
  container: HTMLElement,
  body: THREE.Group,
  resolveName: (id: string) => string,
): LabelLayer {
  const root = document.createElement('div')
  root.className = 'label-layer'
  container.appendChild(root)

  // 每个 muscle_id 只挂一条标签，锚在该 id 所有 mesh 的中心——
  // 成对肌群（quadriceps 有 L/R 两个 mesh）只出一个标签，否则 28 组会变成 50 条。
  const byId = groupByMuscleId(body)

  const items: LabelItem[] = []
  for (const [id, meshes] of byId) {
    const el = document.createElement('div')
    el.className = 'muscle-label'
    root.appendChild(el)
    items.push({ id, el, anchor: anchorFor(meshes) })
  }

  const raycaster = new THREE.Raycaster()
  const projected = new THREE.Vector3() // 只存投影后的 NDC
  const dir = new THREE.Vector3()
  const origin = new THREE.Vector3()

  return {
    setStates(states): void {
      for (const it of items) {
        const state = states[it.id] ?? null
        it.el.textContent = labelText(resolveName(it.id), state)
        it.el.classList.toggle('is-unknown', !state || !state.has_record)
      }
    },
    update(camera, size): void {
      for (const it of items) {
        projected.copy(it.anchor).project(camera)
        const x = (projected.x * 0.5 + 0.5) * size.w
        const y = (-projected.y * 0.5 + 0.5) * size.h
        it.el.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px)`
        if (projected.z > 1) {
          it.el.style.opacity = '0'
          continue
        }

        // 遮挡剔除：被别的肌群块挡在前面的标签淡化。
        //
        // **第三个参数必须是 `false`（非递归）。** scenery 的装饰 mesh 没有
        // `muscleId`——但"没有 muscleId"**并不足以**让它们不被命中：递归遍历会命中它们，
        // 于是下面这行 `muscleId !== it.id`（undefined !== 'quadriceps'）恒为 true，
        // **所有标签会一起被判为被遮挡、整屏变暗**。见 scenery.ts 的模块注释。
        //
        // 另注：`hits.some(...)` 里"命中物不是自己"就当作遮挡，这对同 id 的
        // 左右两块（quadriceps 的 L/R）也成立——即看左腿时右腿会挡住左腿的标签。
        // 当前可接受（两块同色同值，读哪个都一样），但若将来改成分侧数据需要重新审视。
        origin.copy(camera.position)
        dir.copy(it.anchor).sub(origin)
        raycaster.far = Math.max(dir.length() - 0.02, 0)
        raycaster.set(origin, dir.normalize())
        const hits = raycaster.intersectObjects(body.children, false)
        const occluded = hits.some((h) => h.object.userData.muscleId !== it.id)
        it.el.style.opacity = occluded ? '0.28' : '1'
      }
    },
    dispose(): void {
      root.remove()
    },
  }
}
