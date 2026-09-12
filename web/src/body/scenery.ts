import * as THREE from 'three'

/**
 * 中性装饰体块（头/颈/手/脚）——**spec 之外的小补充**（见本任务开头的说明）。
 *
 * 约束：**不可交互、不进 layout.ts**。它们不参与肌群语义，因此 Task 2 的
 * "28 个 id 覆盖"断言不受影响。
 *
 * 注意「不可交互」的真实依赖**不是**"没有 muscleId"，而是两件本模块管不到的事：
 * Group 自身的 `Object3D.raycast` 是空实现，以及调用方必须做**非递归**遍历
 * `intersectObjects(..., false)`。实测（射线起点 (0,5,0)、方向 (0,-1,0)，且先
 * `updateMatrixWorld(true)`——不更新则 matrixWorld 仍是单位阵，所有 mesh 都落在原点，
 * 测出来的命中表没有意义）：`false` 只命中 ["cardio_system", "core", "core"] 三处肌肉；
 * `true` 时**首个命中就是无 muscleId 的装饰 mesh**（userData.muscleId 为 undefined）
 * ——一旦递归遍历，Task 9 的遮挡判定
 * `hits.some(h => h.object.userData.muscleId !== it.id)` 就会把这类命中当成遮挡物，
 * 导致所有标签一起变暗。
 *
 * 整体移除本模块需同时改**三个**文件：删本文件、去掉 build.ts 里的 import 与
 * `group.add(buildScenery())`、删掉 build.test.ts 中两条依赖装饰块的断言
 * （"装饰体块（Group）不带 muscleId" 与 "装饰体块内部 6 个 mesh"）。
 */
const SCENERY: Array<{ pos: [number, number, number]; scale: [number, number, number] }> = [
  { pos: [0, 1.7, 0], scale: [0.085, 0.1, 0.09] }, // 头（y 0.1 半径 → 1.60..1.80，不出身高 1.8）
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
      new THREE.SphereGeometry(1, 16, 10), // 段数同 primitives：4 的倍数才让 z 跨度恰为 2·半径
      new THREE.MeshStandardMaterial({ color: 0x4a5058, roughness: 0.9, metalness: 0 }),
    )
    mesh.position.set(s.pos[0], s.pos[1], s.pos[2])
    mesh.scale.set(s.scale[0], s.scale[1], s.scale[2])
    g.add(mesh)
  }
  return g
}
