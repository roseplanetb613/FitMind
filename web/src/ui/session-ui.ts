import { fetchMe, logout } from '../data/auth'

/**
 * 左下角的会话条：当前身份 + 退出登录。
 *
 * **什么时候不渲染**：`/v1/auth/me` 拿不到 user_id —— 未启用登录的初始化态
 * （账号表为空，网关不拦）或请求失败。这时整条隐藏，别让单机用户看到
 * 一个点了没反应的"退出"。
 *
 * ⚠ 退出时**同时清掉该账号的对话上下文键**（`fitmind.chat.session.<uid>`，
 * 见 main.ts）：同一台浏览器换人登录，不能让下一个人接上这个人的对话语境。
 * 数据本来就不串（网关改写 user_id），要清的只是"话头"。
 */
export function createSessionBar(container: HTMLElement): void {
  void fetchMe().then((me) => {
    if (!me?.ok || !me.user_id) return
    const uid = me.user_id

    container.innerHTML = ''
    container.classList.add('is-on')

    const label = document.createElement('span')
    label.className = 'session-user'
    label.textContent = uid
    label.title = `当前账号：${uid}`

    const btn = document.createElement('button')
    btn.className = 'session-logout'
    btn.type = 'button'
    btn.textContent = '退出登录'
    btn.addEventListener('click', () => {
      btn.disabled = true
      void logout().then((ok) => {
        if (!ok) {
          btn.disabled = false
          return
        }
        try {
          localStorage.removeItem(`fitmind.chat.session.${uid}`)
        } catch {
          // 隐私模式等拿不到 localStorage → 只是不清话头，登出本身不受影响
        }
        location.href = '/login'
      })
    })

    container.append(label, btn)
  })
}
