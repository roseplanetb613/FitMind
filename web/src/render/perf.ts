/**
 * 性能档位：设备能力探测 + 运行时帧率采样。
 *
 * ## 为什么需要这个文件
 *
 * 本仓此前唯一的"轻量化"开关是 `prefers-reduced-motion`（见 `render/glow.ts`），
 * 但它表达的是**用户不想要动效**，不是**这台设备跑不动**。两件事常被混为一谈：
 * 千元机上用户没开那个系统开关，于是和旗舰跑同一套负载 —— 两个 WebGL 上下文、
 * 每帧 20 次 Jacobi 迭代的流体解算、1.5 倍像素比。
 *
 * 这里只做两件事：**开局猜一次**（能力探测）、**运行中修正**（帧率采样）。
 *
 * ## 为什么线索要注入而不是自己读
 *
 * node 里 `navigator` 是**存在**的（Node 21+ 起带 `hardwareConcurrency`），
 * 所以"直接读 navigator"的实现在测试里会安静地通过 —— 测的却是跑测试那台机器。
 * 线索一律由调用方经 `readDeviceHints()` 注入，判定逻辑保持纯函数。
 */

export type Tier = 'full' | 'lite' | 'eco'

/** 由低到高。降档 = 往后走一格，升档 = 往前一格。 */
export const TIER_ORDER = ['eco', 'lite', 'full'] as const

export interface TierSettings {
  /** 主画布像素比上限。尾流层沿用**同一个值**（背景层没理由比主画面更清晰）。 */
  pixelRatio: number
  /** 染料纹理边长；`null` = 不创建尾流层。 */
  dyeResolution: number | null
  /**
   * 星点闪烁。⚠ 关掉的是"闪"，**不是"星"** —— 星场是恢复度的第二条编码通道
   * （补 emissive 在零线归零造成的非单调），关掉闪烁后静态星云仍能读出密度。
   */
  starTwinkle: boolean
  /** 心脏呼吸。关掉后静态辉光仍在。 */
  heartBreathe: boolean
}

export const TIER_SETTINGS: Record<Tier, TierSettings> = {
  // 桌面默认档，与既有常量一致（scene.ts 的 MAX_PIXEL_RATIO、fluid-trail 的 DYE_RESOLUTION）
  full: { pixelRatio: 1.5, dyeResolution: 720, starTwinkle: true, heartBreathe: true },
  // 关闪烁、降填充量；**保留尾流与心脏**（用户口径：手机上尾流要留着）
  lite: { pixelRatio: 1.0, dyeResolution: 512, starTwinkle: false, heartBreathe: true },
  // 终态：整个尾流层不创建，动效全关，于是静止时可以完全不渲染
  eco: { pixelRatio: 0.75, dyeResolution: null, starTwinkle: false, heartBreathe: false },
}

/**
 * 能否在"无事可做"时停帧。
 *
 * ⚠ 这是**派生**结论，不是独立字段。停帧的前提是没有常驻动效；若单列一个
 * `idlePause` 字段，改档位表时它就成了一份会漂移的真相 —— 本仓在
 * `clarify_options` 上吃过这个亏（同一份数据两个产出点，差异不报错只静默降级）。
 */
export function canIdlePause(tier: Tier): boolean {
  const s = TIER_SETTINGS[tier]
  return !s.starTwinkle && !s.heartBreathe
}

/** 设备线索。**每个字段都可能缺失**，缺失一律按"未知"处理。 */
export interface DeviceHints {
  /** `navigator.hardwareConcurrency`。Android Chrome 有，部分环境为 undefined。 */
  hardwareConcurrency?: number
  /** `navigator.deviceMemory`（GB）。**Chrome 有、Safari 没有**。 */
  deviceMemory?: number
  /** `matchMedia('(pointer: coarse)')`：手指为主输入的设备。 */
  coarsePointer?: boolean
  devicePixelRatio?: number
}

/**
 * 开局定档。
 *
 * ⚠ 缺失一律当"未知"跳过，**绝不能当 0**：Safari 没有 `deviceMemory`，
 * 若把它当 0，所有 iPhone 会掉进 `eco`（尾流直接消失）。
 * ⚠ 线索全缺时返回 `full` —— 宁可先给满效果，让帧率采样在运行时降下来；
 * 反过来（缺线索就判弱）会让桌面用户平白少一半效果。
 */
export function detectInitialTier(hints: DeviceHints): Tier {
  const cores = hints.hardwareConcurrency
  const mem = hints.deviceMemory
  if ((cores !== undefined && cores <= 2) || (mem !== undefined && mem <= 2)) return 'eco'
  if ((cores !== undefined && cores <= 4) || (mem !== undefined && mem <= 4)) return 'lite'
  // 严格 === true：undefined（读不到该媒体查询）不算粗指针
  if (hints.coarsePointer === true) return 'lite'
  return 'full'
}

/**
 * 滑动窗口帧率。
 *
 * ⚠ **停帧恢复前必须 `reset()`**：停帧期间那一大段静止时间会在恢复后的第一帧
 * 表现为一个巨大的 `frameMs`，不清窗口的话它会被算成"卡顿"，于是刚唤醒就降档 ——
 * 一个自己把自己拖下水的环。
 */
export class FpsWindow {
  private readonly samples: number[] = []

  constructor(readonly size = 60) {}

  /** 喂一帧的间隔毫秒。非法值（0 / 负 / NaN / Infinity）静默丢弃。 */
  push(frameMs: number): void {
    if (!Number.isFinite(frameMs) || frameMs <= 0) return
    this.samples.push(frameMs)
    if (this.samples.length > this.size) this.samples.shift()
  }

  reset(): void {
    this.samples.length = 0
  }

  get ready(): boolean {
    return this.samples.length >= this.size
  }

  get fps(): number {
    if (this.samples.length === 0) return 0
    let sum = 0
    for (const s of this.samples) sum += s
    return 1000 / (sum / this.samples.length)
  }
}

export interface GovernorOptions {
  /**
   * 探测得到的开局档，**同时是升档上限** ——
   * 探测说这台是弱机，就不许运行期再升回去。
   */
  initial: Tier
  /** 低于此帧率算"卡"。 */
  lowFps?: number
  /** 高于此帧率算"富裕"。 */
  highFps?: number
  /** 连续几个低帧窗口才降档。 */
  lowWindows?: number
  /** 连续几个高帧窗口才升档（比降档更迟钝，避免在阈值上反复横跳）。 */
  highWindows?: number
}

/**
 * 档位调度：带迟滞的升降档。
 *
 * 迟滞不是锦上添花 —— 没有它，帧率在阈值附近波动会让 effect 反复创建/销毁
 * （尾流层重建 FBO 是重操作），看起来像画面在抽搐。
 */
export class TierGovernor {
  private tier: Tier
  private lowStreak = 0
  private highStreak = 0
  private readonly lowFps: number
  private readonly highFps: number
  private readonly lowWindows: number
  private readonly highWindows: number
  private readonly cap: Tier

  constructor(opts: GovernorOptions) {
    this.tier = opts.initial
    this.cap = opts.initial
    this.lowFps = opts.lowFps ?? 45
    this.highFps = opts.highFps ?? 55
    this.lowWindows = opts.lowWindows ?? 2
    this.highWindows = opts.highWindows ?? 3
  }

  get current(): Tier {
    return this.tier
  }

  /**
   * 一个采样窗口结束时调用。返回**当前**档位（没变就返回原值），
   * 调用方据此判断要不要真正动作。
   */
  onWindow(fps: number): Tier {
    // eco 是终态。弱机在 lite/eco 之间反复切换，比稳定待在低档更糟。
    if (this.tier === 'eco') return this.tier

    if (fps < this.lowFps) {
      this.highStreak = 0
      if (++this.lowStreak >= this.lowWindows) {
        this.lowStreak = 0
        this.downgrade()
      }
    } else if (fps > this.highFps) {
      this.lowStreak = 0
      if (++this.highStreak >= this.highWindows) {
        this.highStreak = 0
        this.upgrade()
      }
    } else {
      // 中间区间（45~55）：两种趋势都不成立 → 清零而不是保留。
      // 帧率在阈值附近来回跳时，不该攒出一个"趋势"。
      this.lowStreak = 0
      this.highStreak = 0
    }
    return this.tier
  }

  private downgrade(): void {
    const i = TIER_ORDER.indexOf(this.tier)
    const next = i > 0 ? TIER_ORDER[i - 1] : undefined
    if (next) this.tier = next
  }

  private upgrade(): void {
    const i = TIER_ORDER.indexOf(this.tier)
    const capIndex = TIER_ORDER.indexOf(this.cap)
    const next = i < capIndex ? TIER_ORDER[i + 1] : undefined
    if (next) this.tier = next
  }
}

/**
 * 从浏览器读线索。**全降级**：任何一步失败都只是让对应线索保持"未知"，
 * 绝不抛出 —— 读不到设备信息不该拦住页面启动。
 */
export function readDeviceHints(): DeviceHints {
  const hints: DeviceHints = {}
  try {
    if (typeof navigator !== 'undefined') {
      const nav = navigator as Navigator & { deviceMemory?: number }
      if (typeof nav.hardwareConcurrency === 'number') {
        hints.hardwareConcurrency = nav.hardwareConcurrency
      }
      if (typeof nav.deviceMemory === 'number') {
        hints.deviceMemory = nav.deviceMemory
      }
    }
    if (typeof matchMedia === 'function') {
      hints.coarsePointer = matchMedia('(pointer: coarse)').matches
    }
    if (typeof devicePixelRatio === 'number') {
      hints.devicePixelRatio = devicePixelRatio
    }
  } catch {
    // 读线索失败 → 保持"未知"，交给 detectInitialTier 的缺省分支（full）
  }
  return hints
}
