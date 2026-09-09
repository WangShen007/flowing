<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, LockKeyhole, UserRound } from 'lucide-vue-next'
import BrandMark from './BrandMark.vue'
import { setAuthSession } from '../lib/auth'
import { FEISHU_OAUTH_CONTINUATION_KEY } from '../lib/feishuAuth'

const router = useRouter()
const account = ref('')
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const enterpriseEnabled = ref(false)
const pendingLoginKey = 'feishu-cli.oauth.pending'

const readOAuthContinuation = (): { session: string; resume: boolean } | null => {
  try {
    const raw = sessionStorage.getItem(FEISHU_OAUTH_CONTINUATION_KEY)
    if (!raw) return null
    sessionStorage.removeItem(FEISHU_OAUTH_CONTINUATION_KEY)
    const value = JSON.parse(raw)
    if (typeof value?.session !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(value.session)) return null
    return { session: value.session, resume: value.resume === true }
  } catch (_error) {
    return null
  }
}

const startFeishuLogin = async () => {
  isSubmitting.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/v1/auth/feishu/start', { method: 'POST' })
    const payload = await parseApiJson(response)
    if (!response.ok) throw new Error(payload.detail || '无法发起飞书登录')
    sessionStorage.setItem(pendingLoginKey, JSON.stringify({
      state: payload.data.state, browser_secret: payload.data.browser_secret
    }))
    window.location.assign(payload.data.url)
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '无法发起飞书登录'
    isSubmitting.value = false
  }
}

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const state = params.get('state')
  if (code || params.has('error')) {
    window.history.replaceState(null, '', window.location.pathname)
    isSubmitting.value = true
    let popupFlow = false
    try {
      const pending = JSON.parse(sessionStorage.getItem(pendingLoginKey) || 'null')
      popupFlow = Boolean(pending?.popup && window.opener)
      sessionStorage.removeItem(pendingLoginKey)
      if (!state || !pending || pending.state !== state) throw new Error('登录请求已失效，请重新登录')
      if (!code || params.has('error')) throw new Error('飞书授权未完成，请重新登录')
      const response = await fetch('/api/v1/auth/feishu/complete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...pending, code })
      })
      const payload = await parseApiJson(response)
      if (!response.ok) throw new Error(payload.detail || '飞书登录失败')
      setAuthSession(payload.data.token, payload.data.account)
      if (popupFlow) {
        window.opener.postMessage({ type: 'feishu-authorized', state, success: true }, location.origin)
        window.close()
        return
      }
      const continuation = readOAuthContinuation()
      await router.replace(continuation
        ? { path: '/', query: { session: continuation.session, ...(continuation.resume ? { resume: '1' } : {}) } }
        : '/')
      return
    } catch (error: unknown) {
      errorMessage.value = error instanceof Error ? error.message : '飞书登录失败'
      if (popupFlow) window.opener.postMessage({ type: 'feishu-authorized', state, success: false, message: errorMessage.value }, location.origin)
    } finally {
      isSubmitting.value = false
    }
  }
  try {
    const response = await fetch('/api/v1/auth/feishu/config')
    if (!response.ok) return
    enterpriseEnabled.value = Boolean((await response.json()).data?.enabled)
  } catch {
    errorMessage.value ||= '无法读取登录配置，请刷新重试'
  }
})

const parseApiJson = async (response: Response) => {
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    throw new Error('后端没有返回 JSON，请确认前端代理已指向正确的后端服务。')
  }
  return response.json()
}

const handleLogin = async () => {
  if (!account.value.trim() || !password.value) {
    errorMessage.value = '请输入账号和密码'
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account: account.value.trim(),
        password: password.value
      })
    })
    const data = await parseApiJson(response)
    if (!response.ok || data.code !== 0) {
      throw new Error(data.detail || data.message || '登录失败')
    }
    setAuthSession(data.data.token, data.data.account)
    router.replace('/')
  } catch (error: any) {
    errorMessage.value = error.message || '登录失败'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <BrandMark :size="48" />
        <span class="brand-wordmark">飞序 Flowing<small>AI WORKSPACE</small></span>
      </div>
      <h1 class="login-title">欢迎回来</h1>
      <p class="login-subtitle">登录到你的工作区，继续处理飞书事项。</p>

      <button v-if="enterpriseEnabled" class="login-button enterprise-login-button" type="button" :disabled="isSubmitting" @click="startFeishuLogin">
        <span>{{ isSubmitting ? '登录中...' : '使用企业飞书登录' }}</span>
        <ArrowRight :size="16" aria-hidden="true" />
      </button>

      <form class="login-form" @submit.prevent="handleLogin">
        <label class="login-label">
          <span>账号</span>
          <span class="login-input-wrap">
            <UserRound :size="16" aria-hidden="true" />
            <input v-model="account" type="text" placeholder="例如 admin 或 local" autocomplete="username" />
          </span>
        </label>

        <label class="login-label">
          <span>密码</span>
          <span class="login-input-wrap">
            <LockKeyhole :size="16" aria-hidden="true" />
            <input v-model="password" type="password" placeholder="请输入密码" autocomplete="current-password" />
          </span>
        </label>

        <div v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</div>

        <button class="login-button" type="submit" :disabled="isSubmitting">
          <span>{{ isSubmitting ? '登录中...' : '登录' }}</span>
          <ArrowRight v-if="!isSubmitting" :size="16" aria-hidden="true" />
        </button>
      </form>

    </div>
  </div>
</template>

<style scoped>
.enterprise-login-button {
  width: 100%;
  margin-bottom: 24px;
}
</style>
