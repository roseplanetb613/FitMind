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
        // build 期不设 transparent：opacity 是数据的函数（spec §3.2），且 50 块
        // 肌肉的常态就是不透明。设成 true 会把它们全塞进透明渲染列表（opaque 之后
        // 绘制、按物体中心排序、每片元开混合），而 applyStates 一来就会覆写。
        opacity: 1,
      }),
    )
    mesh.position.set(part.pos[0], part.pos[1], part.pos[2])
    if (part.rot) mesh.rotation.set(part.rot[0], part.rot[1], part.rot[2])
    mesh.userData.muscleId = part.id
    mesh.userData.side = part.side
    mesh.userData.style = part.style ?? 'muscle'
    if (part.style === 'non-muscle') {
      // 心脏在躯干内部：走透明通道、关深度写入并后画。
      // 但 build 期 opacity 恒为 1（palette 只在 unknown 态给 0.35），所以此处
      // 此刻**并不产生**"半透明外壳透出来"的观感——spec §4.4 的视觉区分要等
      // Task 7 的 applyStates 按 userData.style 把 opacity 降下来才成立。
      const material = mesh.material as THREE.MeshStandardMaterial
      material.transparent = true
      material.depthWrite = false
      mesh.renderOrder = 1
    }
    group.add(mesh)
  }

  group.add(buildScenery()) // Group 本身带 isScenery，无 muscleId
  return group
}
