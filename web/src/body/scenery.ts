import * as THREE from 'three'

/**
 * 中性装饰体块（头/颈/手/脚）——**spec 之外的小补充**（见本任务开头的说明）。
 *
 * 约束：**不可交互、不进 layout.ts**。它们没有 userData.muscleId，
 * 所以点选不会命中，Task 2 的"28 个 id 覆盖"断言也不受影响。
 */
const SCENERY: Array<{ pos: [number, number, number]; scale: [number, number, number] }> = [
  { pos: [0, 1.72, 0], scale: [0.085, 0.1, 0.09] }, // 头
  { pos: [0, 1.6, -0.01], scale: [0.055, 0.06, 0.055] }, // 颈
  { pos: [0.245, 0.93, -0.01], scale: [0.045, 0.06, 0.04] }, // 手（左）
  { pos: [-0.245, 0.93, -0.01], scale: [0.045, 0.06, 0.04] }, // 手（右）
  { pos: [0.1, 0.03, 0.02], scale: [0.055, 0.03, 0.1] }, // 脚（左）
  { pos: [-0.1, 0.03, 0.02], scale: [0.055, 0.03, 0.1] }, // 脚（右）
]

export function buildScenery(): THREE.Group {
  const g = new THREE.Group()
  g.name = 'scenery'
  g.userData.isScenery = true
  for (const s of SCENERY) {
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(1, 14, 10),
      new THREE.MeshStandardMaterial({ color: 0x4a5058, roughness: 0.9, metalness: 0 }),
    )
    mesh.position.set(s.pos[0], s.pos[1], s.pos[2])
    mesh.scale.set(s.scale[0], s.scale[1], s.scale[2])
    mesh.userData.isScenery = true // 无 muscleId → 点选不会命中
    g.add(mesh)
  }
  return g
}
