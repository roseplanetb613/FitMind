// `vite/client` 声明 `*.css` 等副作用导入模块。tsconfig 未设 `types`（那会顺带
// 关掉其它自动 @types 收录），故在此就地引一次——否则 `import './styles.css'`
// 报 TS2882 "Cannot find module ... for side-effect import"。没有它就得新增
// src/vite-env.d.ts，而本任务的交付面刻意只有 3 个 web 文件。
/// <reference types="vite/client" />
import * as THREE from 'three'
import './styles.css'
import { ApiSource } from './data/api'
import type { MuscleMapSource } from './data/source'
import { muscleState, resolveLabel, type MuscleMapData } from './data/types'
import { createLabelLayer } from './render/labels'
import { applyStates, createScene, framingFor } from './render/scene'
import { createLegend } from './ui/legend'
import { hideDetail, showDetail } from './ui/detail'

const params = new URLSearchParams(location.search)
const uid = params.get('user_id') ?? 'local'
const days = Number(params.get('days') ?? 7)

const canvas = document.querySelector<HTMLCanvasElement>('#stage')!
const handle = createScene(canvas)
const detailEl = document.querySelector<HTMLElement>('#detail')!
createLegend(document.querySelector<HTMLElement>('#legend')!)

// 标签名走 resolveLabel：labels 降级成 {} 时回落显示 id，不丢块（spec §3.3）
let latest: MuscleMapData | null = null
const labels = createLabelLayer(
  document.querySelector<HTMLElement>('#labels')!,
  handle.body,
  (id) => (latest ? resolveLabel(latest.labels, id) : id),
)

// 点选：只命中带 muscleId 的 mesh。
//
// **非递归遍历（第三个参数 false）是硬性要求**，理由见 scenery.ts 模块注释。
// 另注：Raycaster 依赖 matrixWorld，而 build() 返回的 Group 在加入场景并渲染前
// 不会自动更新矩阵。这里没有问题（用户点击时渲染循环已跑过多帧），但若将来
// 改成"首帧渲染前就做射线判定"，必须先 updateMatrixWorld(true)，否则所有 mesh
// 都被当作落在原点，命中的是完全错误的目标。
const raycaster = new THREE.Raycaster()
const ndc = new THREE.Vector2()
canvas.addEventListener('click', (ev) => {
  const rect = canvas.getBoundingClientRect()
  ndc.set(
    ((ev.clientX - rect.left) / rect.width) * 2 - 1,
    -((ev.clientY - rect.top) / rect.height) * 2 + 1,
  )
  raycaster.setFromCamera(ndc, handle.camera)
  const hit = raycaster.intersectObjects(handle.body.children, false)[0]
  const id = hit?.object.userData.muscleId as string | undefined
  if (!id || !latest) {
    hideDetail(detailEl)
    return
  }
  // muscleState 而非 latest.muscles[id]：缺键与 null 都要归一（见 types.ts）
  showDetail(detailEl, resolveLabel(latest.labels, id), muscleState(latest, id))
})

// 走 scene.ts 的 framingFor，不在这里另写一套坐标——"正面"的姿态只能有一个来源。
// 审查发现过真实的不一致：曾是场景加载时相机在 (0.9,1.5,2.6)（四分之三视角）、
// 而按钮回到 (0,1.5,2.6)，同一个"正面"两个姿态。现在 createScene 的初始相机也走
// framingFor('front')，本行是那条单源的唯一兑现点——若在这里硬编码坐标，不一致会复发。
document.querySelectorAll<HTMLButtonElement>('#toolbar button').forEach((b) => {
  b.addEventListener('click', () => {
    const view = b.dataset.view === 'back' ? 'back' : 'front'
    const f = framingFor(view)
    handle.camera.position.set(...f.position)
    handle.controls.target.set(...f.target)
    handle.controls.update()
  })
})

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') hideDetail(detailEl)
})

// loader 以 **MuscleMapSource 接口**为形参（不是具体的 ApiSource）——
// 这是那个接口唯一的兑现点：测试/故事书可以注入假实现而无需 stub fetch。
async function refresh(source: MuscleMapSource): Promise<void> {
  try {
    const data = await source.fetch()
    latest = data
    applyStates(handle.body, data)
    labels.setStates(data.muscles)
  } catch (err) {
    // 降级可见，不静默（与全仓 diag 留痕一致）
    console.error('肌群数据加载失败', err)
    document.querySelector('#legend')!.insertAdjacentHTML(
      'beforeend',
      '<div class="legend-note" style="color:#e0a458">数据加载失败，显示为全部未知态</div>',
    )
    labels.setStates({})
  }
}

const source: MuscleMapSource = new ApiSource(uid, days)
setInterval(() => void refresh(source), 60_000)
void refresh(source)

// 标签层跟随投影（几何与渲染由 scene.ts 自己的 RAF 驱动）
const tick = (): void => {
  requestAnimationFrame(tick)
  labels.update(handle.camera, { w: canvas.clientWidth, h: canvas.clientHeight })
}
tick()
