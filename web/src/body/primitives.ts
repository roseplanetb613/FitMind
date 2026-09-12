import * as THREE from 'three'
import type { Shape, Vec3 } from './layout'

/** 参数化图元 → BufferGeometry。scale 是**半尺寸**，直接烘进几何。 */
export function geometryFor(shape: Shape, scale: Vec3): THREE.BufferGeometry {
  const [sx, sy, sz] = scale
  if (shape === 'box') {
    return new THREE.BoxGeometry(sx * 2, sy * 2, sz * 2)
  }
  if (shape === 'ellipsoid') {
    // widthSegments 必须取 4 的倍数：唯有 φ = π/2 处有顶点时 z 跨度才恰为 2·sz。
    // 14 段时最大 sin φ = sin(3π/7) ≈ 0.974928，z 跨度只有 1.94986·sz（x 不受影响，
    // 因为 cos φ 在 φ=0 处取到 1，y 由 10 个 theta 段在 θ=π/2 取满）。实测 16 段
    // 的 x/y/z 跨度均为精确的 2.000000。
    const g = new THREE.SphereGeometry(1, 16, 10)
    g.scale(sx, sy, sz)
    return g
  }
  // capsule：圆柱段长度 = sy*2 − 直径，故总高 = 中段 + 2·半径 = sy*2（前提 sy ≥ sx）。
  // three 0.186 的 CapsuleGeometry(radius, height, ...) 里 height 是**中段**高度
  // （源码 cylinderPartLength = height，且内部本就 clamp 到 ≥ 0），故传入 sy*2 − sx*2。
  // 不再额外设下限：sy < sx 属非法输入，用一个人为小值顶替只会让失真更隐蔽。
  const g = new THREE.CapsuleGeometry(sx, Math.max(sy * 2 - sx * 2, 0), 4, 12)
  g.scale(1, 1, sz / sx)
  return g
}
