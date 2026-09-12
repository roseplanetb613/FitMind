/**
 * 环 形 遍 历（纯函数，可单测，spec §6.2 的"Tab 遍历肌群"）。
 *
 * `current` 为 null（还没聚焦任何肌群）时：正方向取第一个、反方向取最后一个——
 * 也就是"从这个方向进入列表"的自然落点。
 *
 * `current` 不在 `ids` 里（数据换了一轮、id 消失）时**按 null 处理**，而不是让
 * `indexOf` 的 -1 直接参与取模：`(-1 + dir + n) % n` 在 dir=-1 时落到 `n-2`，
 * 即"从倒数第二个继续"——一个没人想要的隐式位置。
 *
 * 注意：本函数**环形**，但 main.ts 在两端（见 atEdge）不接管按键、由浏览器把焦点
 * 移出本层。所以"从最后一条绕回第一条"目前不由 UI 路径触发，它是本函数的契约与
 * 兜底（调用方漏拦边界时得到的是绕回而不是 undefined），不是当前的交互行为。
 *
 * 前提：`ids` 非空（与 labels.anchorFor 一样不做兜底）。空数组是配置错误，
 * 用兜底值掩盖只会让它更晚、更难定位地暴露。
 */
export function nextId(ids: string[], current: string | null, dir: 1 | -1): string {
  const i = current === null ? -1 : ids.indexOf(current)
  if (i < 0) return dir === 1 ? ids[0] : ids[ids.length - 1]
  return ids[(i + dir + ids.length) % ids.length]
}

/**
 * 这次 Tab 是否已经走到遍历边界（该放手让浏览器把焦点移出本组件）。
 *
 * 调用方据此决定**不接管**该按键 —— 键盘焦点因此能循 Tab / Shift+Tab 正常离开，
 * 本组件不构成键盘陷阱（WCAG 2.1.2 的判据就是这两个键能否离开）。
 * 边界是**方向相关**的：正方向的边界是最后一条，反方向是第一条。
 *
 * `current` 为 null 或不在 ids 里时返回 false（不是边界）：那是"还没进来"或
 * "数据换了一轮"，该由 nextId 给出落点，而不是把按键丢给浏览器。
 */
export function atEdge(ids: string[], current: string | null, dir: 1 | -1): boolean {
  return current === ids[dir === 1 ? ids.length - 1 : 0]
}
