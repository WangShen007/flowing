import { buildAuthHeaders } from './auth'

export const FEISHU_OAUTH_CONTINUATION_KEY = 'feishu-cli.oauth.continuation'

export const authorizeFeishu = (scopes: string[]): Promise<void> => {
  let popup: Window | null = null
  try {
    popup = window.open('about:blank', '_blank', 'popup,width=620,height=760')
  } catch (_error) {
    // Some embedded browsers throw instead of returning null when popups are blocked.
  }
  return new Promise((resolve, reject) => {
    let state = ''
    let poll = 0
    let timeout = 0
    const cleanup = () => {
      window.removeEventListener('message', receive)
      if (poll) clearInterval(poll)
      if (timeout) clearTimeout(timeout)
      if (popup && !popup.closed) popup.close()
    }
    const fail = (message: string) => { cleanup(); reject(new Error(message)) }
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== popup || !state || event.data?.state !== state || event.data?.type !== 'feishu-authorized') return
      cleanup()
      event.data.success ? resolve() : reject(new Error(event.data.message || '飞书授权未完成'))
    }
    window.addEventListener('message', receive)
    if (popup) poll = window.setInterval(() => { if (popup?.closed) fail('授权窗口已关闭，可以重新连接') }, 500)
    timeout = window.setTimeout(() => fail('授权请求已过期，请重新连接'), 300000)
    void (async () => {
      try {
        const response = await fetch('/api/v1/auth/feishu/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...buildAuthHeaders() },
          body: JSON.stringify({ scopes })
        })
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '无法发起飞书授权')
        if (payload.data.ready) { cleanup(); resolve(); return }
        state = payload.data.state
        const pending = JSON.stringify({
          state, browser_secret: payload.data.browser_secret, popup: Boolean(popup)
        })
        if (popup) {
          popup.sessionStorage.setItem('feishu-cli.oauth.pending', pending)
          popup.location.assign(payload.data.url)
        } else {
          // Keep the server-issued state and browser secret in this tab as a
          // bound pair. Login.vue will submit both values after the redirect.
          sessionStorage.setItem('feishu-cli.oauth.pending', pending)
          window.location.assign(payload.data.url)
        }
      } catch (error: unknown) {
        fail(error instanceof Error ? error.message : '飞书授权失败')
      }
    })()
  })
}
