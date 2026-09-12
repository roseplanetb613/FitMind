import * as THREE from 'three'
import { buildLayout } from './layout'
import { geometryFor } from './primitives'
import { buildScenery } from './scenery'

/**
 * layout → THREE.Group。**只在加载时调用一次**（spec §3.2）。
 * 每个 mesh 带 userData.{muscleId, side, style}，供点选与材质更新定位。
 * 材质逐 mesh 独立——共享材质会让"改一块肌肉"变成"改所有肌肉"。
 */
export function build(): THREE.Group {
  const group = new THREE.Group()
  group.name = 'muscle-body'

  for (const part of buildLayout()) {
    const mesh = new THREE.Mesh(
      geometryFor(part.shape, part.scale),
      new THREE.MeshStandardMaterial({
        color: 0x8a8f96,
        emissive: 0x000000,
        emissiveIntensity: 0,
        roughness: 0.55,
        metalness: 0.05,
        transparent: true,
        opacity: 1,
      }),
    )
    mesh.position.set(part.pos[0], part.pos[1], part.pos[2])
    if (part.rot) mesh.rotation.set(part.rot[0], part.rot[1], part.rot[2])
    mesh.userData.muscleId = part.id
    mesh.userData.side = part.side
    mesh.userData.style = part.style ?? 'muscle'
    // 心脏在躯干内部：关深度写入并后画，让半透明外壳透出来
    if (part.style === 'non-muscle') {
      mesh.renderOrder = 1
      ;(mesh.material as THREE.MeshStandardMaterial).depthWrite = false
    }
    group.add(mesh)
  }

  group.add(buildScenery()) // Group 本身带 isScenery，无 muscleId
  return group
}
