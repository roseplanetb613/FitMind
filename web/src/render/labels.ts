import * as THREE from 'three'
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
  for (const m of meshes) anchor.add(m.position)
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

  const raycaster = new THREE.Raycaster()
  const projected = new THREE.Vector3() // 每次迭代复用：x/y = 屏幕像素，z = NDC z
  const dir = new THREE.Vector3()
  const origin = new THREE.Vector3()

  // 悬停/聚焦的 id。只用来在 update() 里让它们不被遮挡淡化（"提升"要看得见才算提升）。
  let hoveredId: string | null = null
  let focusedId: string | null = null

  return {
    ids: () => items.map((it) => it.id),
    idOf: (el) => idByEl.get(el) ?? null,
    setStates(states): void {
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
      hoveredId = id
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
        const hits = raycaster.intersectObjects(body.children, false)
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
