// 肌群摆位表（纯数据，零 three.js 依赖）。
//
// 坐标系（spec §4）：身高归一化为 1.8，原点在双脚中间地面，+Y 向上，+Z 指向观察者。
// 真实身高差异不进几何——这是可视化，不是人体测量。
//
// 摆位值是**设计判断**（让图好读），不是解剖学测量值；调整它们是正常的。
// tests/layout.test.ts 的包围盒断言分两层：全局 min/max 只防「放大 / 位置飞出人体」；
// 逐块自查另防非正尺寸与单块越界。但「体块单纯缩小、仍留在人体包络内」两层都抓不到。

export type Side = 'L' | 'R' | 'C'
export type Shape = 'capsule' | 'ellipsoid' | 'box'
export type Vec3 = [number, number, number]

export interface PartSpec {
  id: string
  shape: Shape
  pos: Vec3
  rot?: Vec3
  /** 半尺寸：pos ± scale 即该体块的 AABB。
   *  box: 三轴半边长；ellipsoid: 三轴半径；capsule: x/z 为半径、y 为含端帽的半高
   *  （故圆柱段长度 = 2*(scale.y - scale.x)）。 */
  scale: Vec3
  /** 'non-muscle' 用半透明外壳，与骨骼肌在视觉上区分（spec §4.4） */
  style?: 'muscle' | 'non-muscle'
}

export interface MusclePart extends PartSpec {
  side: Side
}

/** 成对肌群：只写左侧（+X），右侧由 mirror() 生成。22 个。 */
export const PAIRED: PartSpec[] = [
  // —— 下肢 ——
  { id: 'quadriceps', shape: 'capsule', pos: [0.11, 0.75, 0.03], scale: [0.085, 0.2, 0.085] },
  { id: 'hamstrings', shape: 'capsule', pos: [0.11, 0.74, -0.05], scale: [0.08, 0.19, 0.08] },
  { id: 'glutes', shape: 'ellipsoid', pos: [0.11, 0.95, -0.09], scale: [0.1, 0.09, 0.09] },
  { id: 'abductors', shape: 'ellipsoid', pos: [0.16, 0.92, -0.02], scale: [0.05, 0.09, 0.06] },
  { id: 'adductors', shape: 'ellipsoid', pos: [0.055, 0.88, -0.02], scale: [0.045, 0.1, 0.055] },
  { id: 'hip_flexors', shape: 'ellipsoid', pos: [0.075, 0.99, 0.05], scale: [0.045, 0.06, 0.05] },
  { id: 'calves', shape: 'capsule', pos: [0.1, 0.36, -0.03], scale: [0.07, 0.16, 0.07] },
  { id: 'tibialis_anterior', shape: 'capsule', pos: [0.1, 0.36, 0.04], scale: [0.05, 0.15, 0.05] },
  { id: 'ankle_stabilizers', shape: 'ellipsoid', pos: [0.1, 0.12, 0.0], scale: [0.05, 0.05, 0.05] },
  // —— 躯干 ——
  { id: 'obliques', shape: 'box', pos: [0.1, 1.14, -0.02], scale: [0.055, 0.13, 0.09] },
  { id: 'latissimus_dorsi', shape: 'box', pos: [0.13, 1.3, -0.08], scale: [0.07, 0.14, 0.05] },
  { id: 'serratus_anterior', shape: 'box', pos: [0.135, 1.32, 0.03], scale: [0.045, 0.09, 0.04] },
  { id: 'pectorals', shape: 'ellipsoid', pos: [0.085, 1.38, 0.07], scale: [0.075, 0.075, 0.045] },
  { id: 'rhomboids', shape: 'box', pos: [0.05, 1.36, -0.1], scale: [0.045, 0.07, 0.03] },
  { id: 'trapezius', shape: 'box', pos: [0.1, 1.46, -0.07], scale: [0.085, 0.1, 0.045] },
  { id: 'levator_scapulae', shape: 'box', pos: [0.07, 1.52, -0.06], scale: [0.04, 0.06, 0.035] },
  // —— 上肢 ——
  { id: 'deltoids', shape: 'ellipsoid', pos: [0.2, 1.47, 0.0], scale: [0.07, 0.075, 0.07] },
  { id: 'rotator_cuff', shape: 'ellipsoid', pos: [0.175, 1.44, -0.04], scale: [0.045, 0.045, 0.045] },
  { id: 'biceps', shape: 'capsule', pos: [0.225, 1.3, 0.03], scale: [0.055, 0.11, 0.055] },
  { id: 'triceps', shape: 'capsule', pos: [0.225, 1.3, -0.04], scale: [0.055, 0.12, 0.055] },
  { id: 'forearms', shape: 'capsule', pos: [0.235, 1.06, -0.01], scale: [0.05, 0.13, 0.05] },
  { id: 'sternocleidomastoid', shape: 'capsule', pos: [0.045, 1.59, 0.03], scale: [0.03, 0.06, 0.03] },
]

/** 中轴肌群：只渲染一份。6 个。 */
export const MIDLINE: PartSpec[] = [
  { id: 'spine', shape: 'box', pos: [0, 1.28, -0.1], scale: [0.045, 0.22, 0.035] },
  { id: 'lower_back', shape: 'box', pos: [0, 1.14, -0.095], scale: [0.1, 0.11, 0.045] },
  { id: 'upper_back', shape: 'box', pos: [0, 1.4, -0.09], scale: [0.115, 0.11, 0.04] },
  { id: 'core', shape: 'box', pos: [0, 1.12, 0.0], scale: [0.1, 0.14, 0.085] },
  { id: 'rectus_abdominis', shape: 'box', pos: [0, 1.14, 0.055], scale: [0.075, 0.13, 0.035] },
  // 心脏在躯干内部，由非肌肉材质透出（spec §4.4）
  { id: 'cardio_system', shape: 'ellipsoid', pos: [0, 1.37, 0.02], scale: [0.055, 0.06, 0.05], style: 'non-muscle' },
]

/** 左侧 → 右侧：x → −x；rot 欧拉角按 (x, −y, −z) 镜像（与 three.js 默认 XYZ 序一致）。
 *  反射 x→−x 即 S·R·S⁻¹（S = diag(−1,1,1)），故 rx 不变、ry/rz 取反；
 *  该映射对欧拉顺序不敏感（XYZ 与 ZYX 序均已数值验证）。 */
export function mirror(p: PartSpec): PartSpec {
  return {
    ...p,
    pos: [-p.pos[0], p.pos[1], p.pos[2]] as Vec3,
    ...(p.rot ? { rot: [p.rot[0], -p.rot[1], -p.rot[2]] as Vec3 } : {}),
  }
}

/** 22 成对 × 2 + 6 中轴 = 50 个体块。 */
export function buildLayout(): MusclePart[] {
  const out: MusclePart[] = []
  for (const p of PAIRED) {
    out.push({ ...p, side: 'L' })
    out.push({ ...mirror(p), side: 'R' })
  }
  for (const m of MIDLINE) out.push({ ...m, side: 'C' })
  return out
}
