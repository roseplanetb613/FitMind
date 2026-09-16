/**
 * 训练计划表 —— 抽屉式弹层，入口在工具栏。
 *
 * **为什么是抽屉而不是常驻面板**：左列自上而下已被 `#toolbar` → `#detail`
 * （肌肉详情，`top:64px`）占住，底部是 `#legend`；计划表塞进左列会与详情浮层
 * 正面相撞。右列是对话面板（窄屏下它自己就占 68vh）。而计划是 4 天 × N 个动作
 * 的长内容，需要自己的滚动区 —— 复用 `#profile` 已有的抽屉体系最省事，
 * 窄屏也不打架。
 *
 * **数据只从 `/v1/plan` 来**（图谱里的 PlanVersion），本组件不重算计划：
 * 同一份计划在聊天、网页、后续编辑里必须是同一份。
 */
import {
  fetchPlan, planDayCount, type Plan, type PlanContent, type PlanDay,
  type PlanExercise, type PlanResponse,
} from '../data/plan'

export interface PlanPanelDeps {
  root: HTMLElement
  userId: string
  /** 注入以便测试；默认走 data/plan 的真实实现 */
  load?: (userId: string) => Promise<PlanResponse>
  /** 注入以便测试；默认走 data/plan 的真实实现。有它才渲染删除按钮。 */
  deletePlan?: (userId: string) => Promise<void>
}

export interface PlanPanel {
  open: () => Promise<void>
  close: () => void
  isOpen: () => boolean
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string,
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag)
  if (className) n.className = className
  if (text !== undefined) n.textContent = text
  return n
}

/**
 * 清空子节点。
 * `innerHTML = ''` 对真实 DOM 足够；但 tests/dom-stub.ts 不解析 HTML，它的
 * `children` 是个普通数组、不会因 innerHTML 清空 —— 于是"重渲染后只剩新节点"
 * 这件事在测试里会假性失败。只对数组动手（真实 HTMLCollection 的 Array.isArray
 * 为 false，不会被误改）。
 */
function clear(node: HTMLElement): void {
  node.innerHTML = ''
  const kids = (node as unknown as { children?: unknown }).children
  if (Array.isArray(kids)) kids.length = 0
}

/** ISO → "9月13日 14:35"。解析不了就原样显示，**不显示 NaN**。 */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 一个动作的组次描述。"3-4 组 × 5-12 次"；缺字段时只显示有的那部分。 */
export function setsReps(e: PlanExercise): string {
  const sets = e.sets ? `${e.sets} 组` : ''
  const reps = e.reps ? `${e.reps} 次` : ''
  return [sets, reps].filter(Boolean).join(' × ')
}

/** 顶部摘要行：分化方式 · 天数 · 生成时间。 */
export function planSummary(plan: Plan): string {
  const content = plan.content
  const scheme = content?.training?.scheme
  const days = planDayCount(content)
  const when = formatWhen(plan.created_at)
  return [scheme, days ? `${days} 天` : '', when ? `生成于 ${when}` : '']
    .filter(Boolean).join(' · ')
}

/** 营养摘要行。全缺则返回空串（**不显示一排 0**）。 */
export function macrosLine(content: PlanContent | null | undefined): string {
  const m = content?.macros
  if (!m) return ''
  const bits: string[] = []
  if (m.target_kcal != null) bits.push(`目标热量 ${m.target_kcal} kcal`)
  if (m.protein_g != null) bits.push(`蛋白 ${m.protein_g}g`)
  if (m.fat_g != null) bits.push(`脂肪 ${m.fat_g}g`)
  if (m.carbs_g != null) bits.push(`碳水 ${m.carbs_g}g`)
  return bits.join(' · ')
}

/**
 * 把计划渲染进 `host`（清空后重建）。纯渲染：不发请求、不读全局状态。
 * 导出是为了让测试能直接喂一份 fixture 断言 DOM。
 * 关闭按钮每次重建都是新节点，故由 `onClose` 回调而不是组件闭包里的状态——
 * 回调里走组件的 `close()`，这样 `isOpen()` 不会残留 true。
 */
export function renderPlan(
  host: HTMLElement, res: PlanResponse, onClose?: () => void,
  onDelete?: () => Promise<void>,
): void {
  clear(host)

  const card = el('div', 'plan-card')
  card.setAttribute('role', 'dialog')
  card.setAttribute('aria-modal', 'true')
  card.setAttribute('aria-label', '训练计划')

  const head = el('div', 'plan-head')
  head.appendChild(el('h3', 'plan-title', '训练计划'))
  const close = el('button', 'plan-close', '×')
  close.setAttribute('type', 'button')
  close.setAttribute('aria-label', '关闭')
  head.appendChild(close)
  // 删除按钮：**两态确认**（点一次变「确认删除？」，再点才执行）——
  // 软删虽可恢复，但误触清掉整个计划表的观感太差；不用 window.confirm
  // （dom-stub 环境没有它，测试跑不了）。
  if (res.plan && onDelete) {
    const del = el('button', 'plan-delete', '删除计划')
    del.setAttribute('type', 'button')
    let armed = false
    del.addEventListener('click', () => {
      if (!armed) {
        armed = true
        del.textContent = '确认删除？'
        del.classList.add('is-armed')
        return
      }
      del.disabled = true
      void onDelete().catch(() => {
        del.disabled = false
        del.textContent = '删除失败，重试？'
        armed = false
      })
    })
    head.appendChild(del)
  }
  card.appendChild(head)

  const body = el('div', 'plan-body')

  if (res.degraded) {
    // 降级 ≠ 没有计划：读不到和"从没生成过"要分得开，否则用户会以为计划丢了。
    // 附加类走 classList.add 而非写进 className —— 后者会让 className 变成
    // "plan-empty is-degraded"，按类名取节点（含 dom-stub 的 byClass）就取不到
    const p = el('p', 'plan-empty',
                 '暂时读不到计划（记忆图谱不可用）。恢复后这里会自动显示。')
    p.classList.add('is-degraded')
    body.appendChild(p)
  } else if (!res.plan) {
    body.appendChild(el('p', 'plan-empty',
      '还没有训练计划。在对话里说「帮我排一个一周四天的计划」就会生成。'))
  } else {
    const plan = res.plan
    const summary = planSummary(plan)
    if (summary) body.appendChild(el('p', 'plan-sub', summary))

    const items = plan.content?.training?.items ?? []
    if (items.length) {
      const list = el('div', 'plan-days')
      for (const day of items) list.appendChild(renderDay(day))
      body.appendChild(list)
    } else {
      body.appendChild(el('p', 'plan-empty', '这份计划里没有训练安排。'))
    }

    const macros = macrosLine(plan.content)
    if (macros) {
      const box = el('div', 'plan-macros')
      box.appendChild(el('h4', 'plan-section', '营养'))
      box.appendChild(el('p', 'plan-macros-line', macros))
      body.appendChild(box)
    }

    const scr = plan.content?.screening
    if (scr?.level && scr.level !== 'green') {
      const box = el('div', 'plan-screening')
      box.appendChild(el('h4', 'plan-section', '身体筛查'))
      if (scr.level_label) {
        box.appendChild(el('p', 'plan-screening-line', scr.level_label))
      }
      for (const w of scr.warnings ?? []) {
        box.appendChild(el('p', 'plan-screening-warn', w))
      }
      body.appendChild(box)
    }
  }

  card.appendChild(body)
  host.appendChild(card)
  close.addEventListener('click', () => onClose?.())
}

/** 一天：日期 + 名称 + 动作表。休息日只显示提示，不画空表。 */
function renderDay(day: PlanDay): HTMLElement {
  const box = el('section', 'plan-day')
  if (day.type === 'rest') box.classList.add('is-rest')

  const head = el('div', 'plan-day-head')
  head.appendChild(el('span', 'plan-day-date', day.date || ''))
  head.appendChild(el('span', 'plan-day-name', day.day || ''))
  if (day.deload) head.appendChild(el('span', 'plan-badge', '减量'))
  box.appendChild(head)

  if (day.type === 'rest') {
    box.appendChild(el('p', 'plan-day-note', day.note || '好好恢复'))
    if (day.blocked_from) {
      box.appendChild(el('p', 'plan-day-note',
        '（因身体筛查，原定训练改为休息）'))
    } else if (day.rest_from) {
      // 用户自己要求的休息日——说成筛查原因就是替筛查背锅（后端 llm 提示词同款约束）
      box.appendChild(el('p', 'plan-day-note',
        '（按你的要求，原定训练改为休息）'))
    }
    return box
  }

  const rows = day.exercises ?? []
  if (!rows.length) {
    box.appendChild(el('p', 'plan-day-note', '（这天没有安排动作）'))
    return box
  }

  const table = el('table', 'plan-table')
  const thead = el('thead')
  const hr = el('tr')
  for (const h of ['动作', '组次', '休息']) hr.appendChild(el('th', undefined, h))
  thead.appendChild(hr)
  table.appendChild(thead)

  const tbody = el('tbody')
  for (const e of rows) {
    const tr = el('tr')
    tr.appendChild(el('td', 'plan-ex-name', e.name || ''))
    tr.appendChild(el('td', 'plan-ex-sets', setsReps(e)))
    tr.appendChild(el('td', 'plan-ex-rest', e.rest_sec ? `${e.rest_sec}s` : ''))
    tbody.appendChild(tr)
  }
  table.appendChild(tbody)
  box.appendChild(table)
  return box
}

export function createPlanPanel(deps: PlanPanelDeps): PlanPanel {
  const load = deps.load ?? ((uid: string) => fetchPlan(uid))
  let open = false
  const root = deps.root

  function setOpen(next: boolean): void {
    open = next
    root.classList.toggle('is-open', next)
  }

  const panel: PlanPanel = {
    async open(): Promise<void> {
      setOpen(true)
      clear(root)
      root.appendChild(el('p', 'plan-loading', '读取中…'))
      try {
        renderPlan(root, await load(deps.userId), () => setOpen(false), wipe)
      } catch (err) {
        // 请求失败与"没有计划"是两件事：这里如实说失败，不冒充空计划
        clear(root)
        const card = el('div', 'plan-card')
        card.appendChild(el('h3', 'plan-title', '训练计划'))
        const msg = el('p', 'plan-empty',
                       `读取失败：${err instanceof Error ? err.message : String(err)}`)
        msg.classList.add('is-error')
        card.appendChild(msg)
        root.appendChild(card)
      }
      // renderPlan 只动内容，不动 root 的 class；但 open() 期间被 close() 过就尊重 close
      root.classList.toggle('is-open', open)
    },
    close: () => setOpen(false),
    isOpen: () => open,
  }

  // wipe 要回调 panel.open()（删除后重拉到空态），所以只能在 panel 之后绑——
  // 先定义的话 `open()` 会解析到上面的 let open 布尔状态（tsc 实测报错）。
  let wipe: (() => Promise<void>) | undefined
  if (deps.deletePlan) {
    wipe = async (): Promise<void> => {
      await deps.deletePlan?.(deps.userId)
      await panel.open()
    }
  }

  return panel
}
