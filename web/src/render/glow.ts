/**
 * 心脏的辉光。
 *
 * **为什么不用后期 bloom（UnrealBloomPass）：**
 *  1. 它是**全屏**的 —— 会把每一块肌肉的自发光一起晕开。而自发光正是恢复度的
 *     语义通道（spec §5.2），糊成一片之后那个通道就读不准了：一块 90% 恢复的
 *     肌肉晕出的光会盖到邻居身上，看起来像邻居也恢复得不错。
 *  2. 用户此前明确报过卡顿。全屏 bloom 需要多一遍全屏渲染 + 多次降采样模糊，
 *     和当初把 `MAX_PIXEL_RATIO` 压到 1.5 的理由直接冲突。
 * 所以改成**局部**的：心脏位置上一张加色混合的光斑。一次 draw call，不影响别处。
 *
 * **贴图用 `DataTexture` 手工生成，不用 canvas。** 两个理由：
 *  · 不需要 DOM，于是本模块在 node 里可测（与 palette.ts / particles.ts 同一约定）
 *  · 不受页面 CSP 限制（canvas 转贴图在部分 CSP 下会被拒）
 *
 * ⚠ 本文件除 `buildHeartGlow` 外均为纯函数，可在 node 里断言。
 */
import * as THREE from 'three'
import { NON_MUSCLE_COLOR } from './palette'

/** 辉光贴图的边长（像素）。64 够用 —— 它会被拉成几十个世界单位，看不到锯齿。 */
export const GLOW_TEX_SIZE = 64

/** 辉光相对心脏包围盒最长边的放大倍数。1 = 与心脏同大。 */
export const GLOW_SCALE = 2.4

/** 辉光光斑的不透明度。加色混合，所以这个值直接决定"亮多少"。 */
export const GLOW_OPACITY = 0.55

/** 画在所有东西之后（肌肉 0 / other -1 / 外壳 -2）。见 buildHeartGlow 的 depthTest 说明。 */
export const GLOW_RENDER_ORDER = 10

/**
 * 一张径向衰减的白色贴图，用作光斑的形状。
 *
 * alpha 走**三次方**衰减（不是线性）：线性衰减的光斑看起来像一块发白的圆盘，
 * 三次方把能量集中到中心，外圈干净地收掉，才像"发光"。
 * RGB 恒为白 —— 颜色交给 `SpriteMaterial.color` 去染，贴图只管形状。
 */
export function glowTexture(size: number = GLOW_TEX_SIZE): THREE.DataTexture {
  const data = new Uint8Array(size * size * 4)
  const c = (size - 1) / 2
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      // 归一化到 [0,1]：0 = 正中心，1 = 边缘
      const d = Math.min(1, Math.hypot(x - c, y - c) / c)
      const a = 1 - d
      const i = (y * size + x) * 4
      data[i] = 255
      data[i + 1] = 255
      data[i + 2] = 255
      data[i + 3] = Math.round(255 * a * a * a)
    }
  }
  const tex = new THREE.DataTexture(data, size, size)
  tex.needsUpdate = true
  return tex
}

export interface GlowPlacement {
  /** 世界坐标下的中心 */
  center: [number, number, number]
  /** 世界单位下的边长；不可用时为 0 */
  size: number
}

/**
 * 心脏 mesh → 辉光的世界位置与尺寸。
 *
 * 抽成纯函数是为了能在 node 里断言 —— `buildHeartGlow` 要建 Sprite（需要 GPU 侧的
 * 材质），而"位置算得对不对"这件事不该因此失去守卫。
 *
 * 尺寸取包围盒**最长边**：心脏是立体的，按 X 定尺寸会在某些角度露出光斑边界。
 */
export function heartGlowPlacement(heart: THREE.Object3D, scale = GLOW_SCALE): GlowPlacement {
  heart.updateWorldMatrix(true, true)
  const b = new THREE.Box3().setFromObject(heart)
  if (b.isEmpty()) return { center: [0, 0, 0], size: 0 }
  const c = b.getCenter(new THREE.Vector3())
  const extent = Math.max(b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z)
  return {
    center: [c.x, c.y, c.z],
    // 空包围盒 / 退化几何会给出 0 或 NaN，调用方据此跳过（见 buildHeartGlow）
    size: Number.isFinite(extent) ? extent * scale : 0,
  }
}

/**
 * 在心脏位置生成一张加色光斑。
 *
 * **`depthTest: false` 是必须的**，不是可选的美化：心脏在胸腔里、外面裹着半透明
 * 肌肉，而 `transparent` 材质**默认仍然写深度** —— 开着深度测试的话，光斑会被
 * 前面肌肉的深度挡掉，表现就是"加了辉光但完全看不见"。关掉之后它恒可见，
 * 这也符合"光源"的物理直觉（光会从组织里透出来）。
 *
 * 几何不可用时返回 `null` 而不是抛错：辉光是装饰，不该让整个场景挂掉。
 */
export function buildHeartGlow(heart: THREE.Object3D): THREE.Sprite | null {
  const { center, size } = heartGlowPlacement(heart)
  if (!(size > 0)) return null
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: glowTexture(),
      color: NON_MUSCLE_COLOR,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      opacity: GLOW_OPACITY,
    }),
  )
  sprite.position.set(center[0], center[1], center[2])
  sprite.scale.set(size, size, 1) // Sprite 的 scale.z 不参与绘制
  sprite.renderOrder = GLOW_RENDER_ORDER
  sprite.name = 'heart-glow' // 供测试与调试定位
  return sprite
}
