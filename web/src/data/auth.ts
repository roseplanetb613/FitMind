/**
 * 登录态与会话（`/v1/auth/*`）。
 *
 * 登录/注册页是**免构建**的 `/login`（见 `app/auth.py`），不走这里；
 * 应用内只会用到两个调用：我是谁、退出。
 *
 * ⚠ 请求必须带 Cookie（`credentials: 'same-origin'`，fetch 的默认值，这里显式写
 * 出来当自文档）：**身份的真源是服务端的签名 Cookie**，不是 URL 上的 `?user_id=`
 * —— 后者只是显示，网关会把请求改写成登录者（见 app/auth.py）。
 */

export interface Me {
  ok: boolean
  user_id: string | null
}

/** 我是谁。未登录 / 请求失败 → null（调用方按"不渲染会话条"回落）。 */
export async function fetchMe(): Promise<Me | null> {
  try {
    const res = await fetch('/v1/auth/me', { credentials: 'same-origin' })
    if (!res.ok) return null
    return (await res.json()) as Me
  } catch {
    return null
  }
}

/** 退出登录：服务端清 Cookie。失败返回 false（按钮恢复可点）。 */
export async function logout(): Promise<boolean> {
  try {
    const res = await fetch('/v1/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
    })
    return res.ok
  } catch {
    return false
  }
}
