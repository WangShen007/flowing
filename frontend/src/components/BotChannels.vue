<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { buildAuthHeaders } from '../lib/auth'

interface Binding { id: string; channel: 'weixin' | 'telegram'; peer_id: string; session_id: string; state: string; uncertain_count: number }
interface Status { enabled: boolean; worker_running: boolean; telegram_configured: boolean; bindings: Binding[] }
interface Pairing { id: string; image: string; expires_at: number }
const status = ref<Status | null>(null)
const pairing = ref<Pairing | null>(null)
const phase = ref('')
const peer = ref('')
const code = ref('')
const busy = ref(false)
const error = ref('')
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
const names = { weixin: '个人微信', telegram: 'Telegram' }
const connectionState = (binding: Binding) => !status.value?.worker_running ? '后台未运行' : binding.state === 'ready' ? '连接正常' : binding.state === 'unavailable' ? '连接异常，请检查网络或重新绑定' : '等待渠道连接'

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/bot-channels${path}`, {
    method, signal: controller.signal,
    headers: { ...buildAuthHeaders(), 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '机器人连接操作失败')
  return payload.data as T
}
async function load() { status.value = await api<Status>('') }
async function run(action: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await action() } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '操作失败，请重试'
  } finally { busy.value = false }
}
async function poll(expected = generation) {
  if (!pairing.value || expected !== generation || controller.signal.aborted) return
  const id = pairing.value.id
  try {
    const result = await api<{ status: string; peer_id: string }>(`/pairings/${id}/poll`, 'POST', { verify_code: code.value })
    if (expected !== generation || pairing.value?.id !== id) return
    phase.value = result.status; peer.value = result.peer_id
    if (['waiting', 'wait', 'scaned', 'scaned_but_redirect'].includes(result.status)) {
      code.value = ''
      timer = setTimeout(() => { void poll(expected) }, 1500)
    }
  } catch (e) {
    if (!controller.signal.aborted && expected === generation) error.value = e instanceof Error ? e.message : '获取扫码状态失败'
  }
}
const start = (channel: 'weixin' | 'telegram') => run(async () => {
  generation++; clearTimeout(timer)
  phase.value = 'waiting'; peer.value = ''; code.value = ''
  pairing.value = await api<Pairing>(`/${channel}/pair`, 'POST')
  void poll()
})
const cancel = () => run(async () => {
  generation++; clearTimeout(timer)
  if (pairing.value) await api(`/pairings/${pairing.value.id}`, 'DELETE')
  pairing.value = null
})
const confirm = () => run(async () => {
  if (!pairing.value) return
  await api(`/pairings/${pairing.value.id}/confirm`, 'POST')
  generation++; clearTimeout(timer); pairing.value = null
  await load()
})
const unbind = (binding: Binding) => run(async () => {
  await api(`/bindings/${binding.id}`, 'DELETE'); await load()
})
onMounted(() => { void run(load) })
onUnmounted(() => { generation++; clearTimeout(timer); controller.abort() })
</script>

<template>
  <section class="bot-channels" aria-labelledby="bot-channel-title">
    <h3 id="bot-channel-title">我的微信 / Telegram 助手</h3>
    <p>扫码只绑定当前网站账号。聊天与 AI 记忆按员工隔离，飞书操作使用本人的授权。</p>
    <p class="privacy">网站管理员没有他人聊天查看入口；服务器运维权限不等于端到端加密保护。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="status">
      <p v-if="!status.enabled">部署管理员尚未启用机器人渠道。</p>
      <button :disabled="busy" @click="run(load)">刷新连接状态</button>
      <div v-for="channel in (['weixin', 'telegram'] as const)" :key="channel" class="channel-row">
        <strong>{{ names[channel] }}</strong>
        <template v-if="status.bindings.find(b => b.channel === channel)">
          <span>{{ connectionState(status.bindings.find(b => b.channel === channel)!) }} · 仅本人私聊</span>
          <span v-if="status.bindings.find(b => b.channel === channel)!.uncertain_count">有未确认执行/送达结果，请在网站核对，不要直接重复任务。</span>
          <button :disabled="busy || !!pairing" @click="unbind(status.bindings.find(b => b.channel === channel)!)">解绑</button>
        </template>
        <template v-else>
          <span v-if="channel === 'telegram' && !status.telegram_configured">待管理员配置 Bot Token</span>
          <button :disabled="busy || !!pairing || !status.enabled || (channel === 'telegram' && !status.telegram_configured)" @click="start(channel)">扫码绑定</button>
        </template>
      </div>
      <div v-if="pairing" class="pairing" aria-live="polite">
        <template v-if="phase !== 'scanned'">
          <img :src="pairing.image" width="220" height="220" alt="使用本人手机扫描此二维码绑定机器人，请勿转发" />
          <p>请用本人手机扫码并确认，二维码 5 分钟有效，不要转发给他人。</p>
        </template>
        <template v-if="phase === 'scanned'">
          <p>已收到扫码身份：{{ peer }}。确认是你本人操作后，才会启用此账号的飞书助手。</p>
          <button :disabled="busy" @click="confirm">是我本人，确认绑定</button>
        </template>
        <template v-else-if="phase === 'need_verifycode'">
          <label>微信显示的验证码 <input v-model="code" inputmode="numeric" maxlength="12" autocomplete="off" /></label>
          <button :disabled="busy || !code" @click="run(() => poll())">提交验证码</button>
        </template>
        <p v-else-if="['expired', 'verify_code_blocked', 'binded_redirect'].includes(phase)">二维码已失效、验证受限或微信报告已有绑定。请取消后检查现有连接，再重新扫码。</p>
        <p v-else>等待扫码确认…</p>
        <button v-if="error" :disabled="busy" @click="run(() => poll())">重试获取状态</button>
        <button :disabled="busy" @click="cancel">取消绑定</button>
      </div>
      <p>目前支持纯文字私聊。写操作需核对后发送专属确认码；完整记录可在网站会话列表查看。解绑不会删除历史记录或撤回已提交操作。</p>
    </template>
  </section>
</template>

<style scoped>
.bot-channels { border-top: 1px solid #e5e5e5; margin-top: 20px; font-size: 14px; }
h3 { font-size: 15px; margin: 20px 0 12px; }
p { line-height: 1.6; overflow-wrap: anywhere; }
.privacy { color: #707070; font-size: 12px; }
.channel-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
button { min-height: 36px; border: 1px solid #dedede; background: #fafafa; border-radius: 4px; padding: 6px 12px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.pairing { border: 1px solid #dedede; padding: 16px; border-radius: 6px; }
.pairing img { display: block; max-width: 100%; background: white; }
.pairing button { margin: 8px 8px 0 0; }
input { max-width: 140px; padding: 6px; }
</style>
