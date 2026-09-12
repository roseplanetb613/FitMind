import * as THREE from 'three'
import type { Shape, Vec3 } from './layout'

/** 参数化图元 → BufferGeometry。scale 是**半尺寸**，直接烘进几何。 */
export function geometryFor(shape: Shape, scale: Vec3): THREE.BufferGeometry {
  const [sx, sy, sz] = scale
  if (shape === 'box') {
    return new THREE.BoxGeometry(sx * 2, sy * 2, sz * 2)
  }
  if (shape === 'ellipsoid') {
    const g = new THREE.SphereGeometry(1, 14, 10)
    g.scale(sx, sy, sz)
    return g
  }
  // capsule：圆柱段长度取 sy*2 - 直径，使总高恰为 sy*2。
  // 实测 three 0.186 的 CapsuleGeometry(radius, height, ...) 中 height 是**中段**高度，
  // 总高 = height + 2*radius，故传入 sy*2 - sx*2 得到总高 sy*2（与包围盒断言一致）。
  const g = new THREE.CapsuleGeometry(sx, Math.max(sy * 2 - sx * 2, 0.01), 4, 12)
  g.scale(1, 1, sz / Math.max(sx, 1e-6))
  return g
}
