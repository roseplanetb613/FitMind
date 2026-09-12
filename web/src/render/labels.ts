import * as THREE from 'three'
import { interactiveMeshes } from './scene'
import type { MuscleState } from '../data/types'

/** 标签文案（纯函数，可单测）。星号 = 按次数估算，图例里解释。 */
export function labelText(name: string, state: MuscleState | null): string {
  if (!state || !state.has_record) return `${name} 无记录`
  const star = state.confidence === 'low' ? '*' : ''
  return `${name} ${state.pct}%${star}`
}

/**
 * 按 muscle_id 聚合体块：成对肌群（quadriceps 有 L/R 两块）合成一组，
 * 否则 28 组会变成 50 条标签（两块同色同值，重复标签只是噪声）。
 * 无 muscleId 的子节点（scenery 装饰 Group）被剔除——它们不参与肌群语义。
 *
 * **只遍历 `body.children`（单层）**，与 `update()` 里的非递归判定无关：
 * 即便把 scenery 的 6 个装饰 mesh 平铺进 body（去掉那层 Group），本函数
 * 仍然只给出 28 组——"scenery 必须是 Group"是 `intersectObjects` 的约束，不是这里的。
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
 *
 * 前提：`meshes` 非空。空数组会走 `divideScalar(0)` 得到 NaN 锚点（静默：标签
 * 只是投影到 NaN、挪出画面）。当前唯一调用方 `groupByMuscleId` 的每组至少 1 块，
 * 故不可达——本函数不做兜底，避免让"空组"这条真错误被一个默认值掩盖。
 *
 * **已知后果（Task 8 接受，转 Task 9 验收项）**：`mirror()` 是精确取负，故 22 个成对
 * 肌群的锚点 x 恰为 0，6 个中轴肌群本来也是 0——**28 条锚点全部落在 x = 0 平面**
 * （实测 nonZeroAnchorX = 0）。后果不止"会堆叠"：在默认取景 `framingFor('front')`
 * = [0,1.5,2.6] 下（fov 38 / aspect 800:600 / 800×600）**28/28 条的屏幕 x 全是
 * 400.00，maxDX = 0.00px**——一条竖线；378 对里 35 对落在 |dx| < 60 且 |dy| < 18 内。
 * 换四分之三视角 [0.9,1.5,2.6] / [1.8,1.5,2.0] 也只有 maxDX = 17.6 / 36.9px
 * （1200×900 时 26.4 / 55.4px，重叠对数 27/378）——**远小于标签宽度**。
 * 同 y 的实例如：biceps / triceps / latissimus_dorsi 都在世界 y = 1.300，
 * obliques / lower_back / rectus_abdominis 都在 1.140，calves / tibialis_anterior 都在 0.360。
 * 锚到"靠近相机的近侧块"也不解决——成对肌群的近侧块同样对称。
 * 故 §6.2 的标签提升/避让不是可选优化，是 Task 9 的验收项（本任务不改：计划要求锚在中点）。
 */
export function anchorFor(meshes: THREE.Mesh[]): THREE.Vector3 {
  const anchor = new THREE.Vector3()
  const tmp = new THREE.Vector3()
  // **必须是 world 坐标。** `labels.update` 把这个锚点**直接当世界坐标**投影，
  // 而 body 组是带变换的 —— 真实模型的归一化把缩放挂在那里（scale≈0.0106），
  // 且各 mesh 的 `position` 是 glb 里的**厘米级**坐标。
  // 用局部坐标的话 28 条标签会全部飞到头顶上方（实测 y≈134 vs 人体 0~1.8）。
  // 代码体块时代 body 无变换、局部==世界，这个写法才碰巧成立。
  for (const m of meshes) anchor.add(m.getWorldPosition(tmp))
  return anchor.divideScalar(meshes.length)
}

/**
 * 锚点 → 屏幕像素（纯函数，可单测）。`out` 复用，避免每帧 28 次分配。
 *
 * x 与 y 有隐含的不对称：NDC 的 y 向上、屏幕 y 向下，所以 y 要翻号、x 不翻。
 * `out.z` 保留投影后的 NDC z（调用方用它做 `z > 1` 的出画剔除）。
 */
export function projectToScreen(
  anchor: THREE.Vector3,
  camera: THREE.Camera,
  size: { w: number; h: number },
  out: THREE.Vector3,
): THREE.Vector3 {
  out.copy(anchor).project(camera)
  out.x = (out.x * 0.5 + 0.5) * size.w
  out.y = (-out.y * 0.5 + 0.5) * size.h
  return out
}

/**
 * 一条标签该不该显示。**纯函数，可单测** —— 显示与否是个决策，不是 DOM 细节。
 *
 * 未知态的默认值刻意是 false：实测样张里 28 个肌群只有 21 个有记录，
 * 剩下 7 条"XX 无记录"会盖住模型、把有数值的那些挤掉。它们不是噪音
 * （§5.4 要求未知可辨识，材质上的线框已经承担了那件事），但**不该默认抢视线**。
 */
export function labelVisible(
  state: MuscleState | null,
  opts: { layerVisible: boolean; showUnknown: boolean; hovered?: boolean },
): boolean {
  // 总开关优先：显式关掉就是关掉，悬停也不破例 —— 否则那个开关不成立
  // （用户关它是为了让画面干净，悬停时又冒出来会让人以为开关坏了）
  if (!opts.layerVisible) return false

  // **悬停即"用户点名要看这一块"。** 这是"无记录默认折叠"能成立的前提：
  // 默认不抢视线，但你想看哪块就指哪块，不必先去勾开关。
  if (opts.hovered) return true

  const known = !!state && state.has_record
  return known ? true : opts.showUnknown
}

interface LabelItem {
  id: string
  el: HTMLElement
  /** 锚点：该 id 所有 mesh 位置的平均（成对肌群只出一条标签） */
  anchor: THREE.Vector3
}

export interface LabelLayer {
  /** 标签顺序（= 键盘 Tab 遍历顺序），与 groupByMuscleId 的分组顺序一致 */
  ids(): string[]
  /** 元素 → muscle_id。键盘事件里拿 `document.activeElement` 反查用；
   *  不是本层的标签返回 null（按对象同一性判定，不做属性猜测）。 */
  idOf(el: HTMLElement): string | null
  setStates(states: Record<string, MuscleState | null>): void
  /** 悬停高亮：命中的那条加 `is-hovered`，其余去掉（幂等，不残留上一条） */
  setHovered(id: string | null): void
  /** 当前聚焦/选中的肌群。它同时是 roving tabindex 的拥有者——**只有它 tabindex=0**，
   *  于是 Tab 进出本层只有这一个焦点站。传 null 表示"没有聚焦"，此时把进入点复位到
   *  第一条（键盘还得能再进来）。点选也复用它：'当前是哪一块'只留一个概念。 */
  setFocused(id: string | null): void
  /** 把真实键盘焦点移到该 id 的标签上（只动焦点，不动 roving 状态） */
  focusElement(id: string): void
  /** el 是本层的标签时才 blur 它。焦点已经不在本层时（例如用户 Tab 到了工具栏按钮）
   *  必须什么都不做——blur() 会把键盘焦点丢回 body，焦点环消失，读屏用户丢失位置。
   *
   *  **已知行为（未在真实浏览器验证过，属人工验收项）**：Escape 命中时确实会把焦点
   *  blur 回 body，而不是"移回恰好某个标签"。之后下一次 Tab 由浏览器按文档顺序从头算，
   *  落点是否等于 roving 进入点取决于 DOM 顺序——当前 `#labels` 在 `#toolbar` 之前、
   *  且 setFocused(null) 已把 tabindex=0 复位到第一条，两者**按代码推算**重合；
   *  这是从 DOM 顺序推出来的结论，没有实测证据。 */
  blurIfOwned(el: HTMLElement | null): void
  /** 整层可见性 + 是否显示"无记录"的那些。两者独立：一个是开关，一个是默认折叠。 */
  setVisibility(opts: { layerVisible: boolean; showUnknown: boolean }): void
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
  // 无障碍语义（spec §6.2 的键盘交互）：标签是一个可键盘遍历的列表。
  // role/aria 挂在**属性**上，供读屏与测试取用（样式与行为不依赖它们）。
  //
  // 为什么是 list/listitem 而不是 listbox/option：复合控件（listbox/grid）才自带
  // "方向键在内移动、Tab 整体离开"的约定，而本组件的遍历键是 Tab（spec §6.2 明写）。
  // 用 listbox 会让"用 Tab 在项间走"与角色承诺的交互互相矛盾；list/listitem 是**结构**
  // 角色、不承诺任何键盘交互，所以与 Tab 遍历不冲突。选中项另外用 aria-current 暴露
  // （见 setFocused），不让"当前是哪一块"只活在 CSS 类里。
  // 若将来要改成真正的复合控件，那就是"方向键移动 + Tab 离开 + role=listbox/option"，
  // 与本轮的 Tab 遍历语义互斥，需要连 spec §6.2 一起改。
  root.setAttribute('role', 'list')
  root.setAttribute('aria-label', '肌群恢复状态')
  container.appendChild(root)

  // anchorFor 读的是 **world** 坐标，所以先确保 matrixWorld 是最新的
  // （否则 getWorldPosition 会返回上一次刷新的值）。body 是静态的，
  // 建层时刷一次就够；若将来 body 会被移动，这里要挪到 update 里。
  body.updateMatrixWorld(true)

  // 每个 muscle_id 只挂一条标签，锚在该 id 所有 mesh 的中心——
  // 成对肌群（quadriceps 有 L/R 两个 mesh）只出一个标签，否则 28 组会变成 50 条。
  const byId = groupByMuscleId(body)

  const items: LabelItem[] = []
  const idByEl = new Map<HTMLElement, string>()
  const elById = new Map<string, HTMLElement>()
  for (const [id, meshes] of byId) {
    const el = document.createElement('div')
    el.className = 'muscle-label'
    el.setAttribute('role', 'listitem')
    // roving tabindex 的**进入点**：没有它，整层一个可 Tab 元素都没有，
    // 键盘用户永远进不来。默认落在第一条，setFocused 负责之后跟着焦点走。
    el.setAttribute('tabindex', items.length === 0 ? '0' : '-1')
    root.appendChild(el)
    items.push({ id, el, anchor: anchorFor(meshes) })
    idByEl.set(el, id)
    elById.set(id, el)
  }

  // 上一次 update 的相机指纹。**相机没动就整段跳过** ——
  // 遮挡剔除要对 27 个肌群网格（共 7.6 万三角面）各打一条射线，
  // 实测每帧最坏 210 万次三角面测试，而**静止时它一点产出都没有**：
  // 锚点是静态的，相机不动则投影与遮挡都不变。这是本组件唯一的重开销。
  let lastCamKey = ''

  // 最近一次 setStates 的数据。可见性变化时要按它重算哪些该显示
  // （否则先 setStates 后 setVisibility 时，隐藏/显示用的是旧数据）
  let lastStates: Record<string, MuscleState | null> = {}

  function applyVisibility(): void {
    hidden.clear()
    for (const it of items) {
      const state = lastStates[it.id] ?? null
      const show = labelVisible(state, { ...vis, hovered: it.id === hoveredId })
      if (!show) hidden.add(it.id)
      it.el.style.display = show ? '' : 'none'
    }
    // 可见性变了 → 指纹作废，下一帧必须重算（否则要等到相机动才生效）
    lastCamKey = ''
  }

  const raycaster = new THREE.Raycaster()
  const projected = new THREE.Vector3() // 每次迭代复用：x/y = 屏幕像素，z = NDC z
  const dir = new THREE.Vector3()
  const origin = new THREE.Vector3()

  // 悬停/聚焦的 id。只用来在 update() 里让它们不被遮挡淡化（"提升"要看得见才算提升）。
  // 可见性：整层开关 + 是否显示未知态。`hidden` 缓存每条当前该不该显示，
  // update() 靠它跳过被隐藏的条目 —— 不只是省 DOM，更省掉那些条目的射线。
  let vis = { layerVisible: true, showUnknown: false }
  const hidden = new Set<string>()

  let hoveredId: string | null = null
  let focusedId: string | null = null

  return {
    ids: () => items.map((it) => it.id),
    idOf: (el) => idByEl.get(el) ?? null,
    setVisibility(opts): void {
      vis = opts
      applyVisibility()
    },
    setStates(states): void {
      lastStates = states
      applyVisibility()
      for (const it of items) {
        const state = states[it.id] ?? null
        const text = labelText(resolveName(it.id), state)
        it.el.textContent = text
        // 读屏读到的 == 眼睛看到的（同一份文案，不另拼一套说法）
        it.el.setAttribute('aria-label', text)
        it.el.classList.toggle('is-unknown', !state || !state.has_record)
      }
    },
    setHovered(id): void {
      if (id === hoveredId) return
      hoveredId = id
      // 悬停会改变**谁该显示**（见 labelVisible 的 hovered 分支），
      // 所以不能只切 CSS 类，得重算一次可见性
      applyVisibility()
      // 逐条与 id 比对而非"记住上一条再撤销"：幂等，切来切去也不会残留两条
      for (const it of items) it.el.classList.toggle('is-hovered', it.id === id)
    },
    setFocused(id): void {
      focusedId = id
      // roving tabindex 的进入点：没有聚焦时回到第一条，键盘才能再次进来
      const entry = id ?? items[0]?.id ?? null
      for (const it of items) {
        it.el.classList.toggle('is-focused', it.id === id)
        it.el.setAttribute('tabindex', it.id === entry ? '0' : '-1')
        // 选中态不能只活在 CSS 里：读屏用户听不出"焦点在这条"与"选中了这条"的差别。
        // 用 aria-current（aria-selected 在 listitem 上无效），"false" 是它的标准
        // 非当前值，比 removeAttribute 少一条分支。
        it.el.setAttribute('aria-current', it.id === id ? 'true' : 'false')
      }
    },
    focusElement(id): void {
      elById.get(id)?.focus()
    },
    blurIfOwned(el): void {
      if (el && idByEl.has(el)) el.blur()
    },
    update(camera, size): void {
      // 指纹必须含**所有影响输出的输入**：相机 + 画布尺寸 + 悬停/聚焦态。
      // 漏掉后两者会让"悬停提升""聚焦不淡化"这些在相机静止时失效
      // —— 实测就是这么被测试抓到的（漏掉 hoveredId/focusedId → 两条断言红）。
      const key = `${camera.position.x},${camera.position.y},${camera.position.z},` +
                  `${camera.quaternion.x},${camera.quaternion.y},${camera.quaternion.z},` +
                  `${camera.quaternion.w},${size.w},${size.h},${camera.fov}|` +
                  `${hoveredId ?? ''}|${focusedId ?? ''}`
      if (key === lastCamKey) return // 一切都没变 → 投影与遮挡都不变，白算
      lastCamKey = key

      for (const it of items) {
        projectToScreen(it.anchor, camera, size, projected)
        it.el.style.transform =
          `translate(-50%, -50%) translate(${projected.x}px, ${projected.y}px)`
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
        // 这个后果是**相机相关**的（实测把 false 改成 true）：俯视 (0,5,0.001) 最明显
        // ——28/28 全暗；背面 (0,1,-3) 只差 1 条（{1:10,0.28:18} → {1:9,0.28:19}）；
        // 正面 (0,1,3) **零差异**（都是 {1:17,0.28:11}）。即正/背面人工验收看不出这个回归
        // ——tests/label-layer.test.ts 用俯视那条把它钉住。
        // 复现：tests/label-layer.test.ts 的 cameraAt + 同一组相机位置（800×600）；
        // 这些数字只取决于相机位置与 build() 的摆位，与实现无关。
        //
        // 另注：同 id 的命中已被上面的 `!==` 排除，所以成对肌群的左右两块
        // **不会**互相遮挡；锚点落在两块中间，遮挡只可能来自**其它** id 的块。
        origin.copy(camera.position)
        dir.copy(it.anchor).sub(origin)
        raycaster.far = Math.max(dir.length() - 0.02, 0)
        raycaster.set(origin, dir.normalize())
        // **只打可交互 mesh**：`other` 与外壳若参与，会被下面的 `!== it.id`
        // 判成遮挡物，于是所有标签一起变暗。见 scene.ts 的 interactiveMeshes。
        const hits = raycaster.intersectObjects(interactiveMeshes(body), false)
        const occluded = hits.some((h) => h.object.userData.muscleId !== it.id)
        // 悬停/聚焦的标签"提升"：不被遮挡淡化。它是用户此刻指着/选着的那一块，
        // 被压在 0.28 里就等于没有提升（28 条锚点全在中轴，重叠是常态）。
        // 注：出画（上面 z > 1 的分支）仍然压成 0——那时它不在画面上，没什么可提升的。
        const raised = it.id === hoveredId || it.id === focusedId
        it.el.style.opacity = occluded && !raised ? '0.28' : '1'
      }
    },
    dispose(): void {
      root.remove()
    },
  }
}
