<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Brain, Link, RefreshCw, Trash2, Unlink, X } from 'lucide-vue-next'
import { buildAuthHeaders, getAuthAccount } from '../lib/auth'
import { authorizeFeishu } from '../lib/feishuAuth'
import BotChannels from './BotChannels.vue'

const emit = defineEmits<{ close: [] }>()
type Role = 'employee' | 'lead' | 'admin'
interface Member { account: string; name: string; role: Role; enabled: boolean }
interface Connection { enabled: boolean; role: Role; connected: boolean; missing_scopes: string[]; capabilities: { key: string; title: string }[] }
interface WorkflowMemory {
  id: number
  intent_key: string
  label: string
  status: 'candidate' | 'active'
  success_count: number
  failure_count: number
  last_used_at?: number
  updated_at: number
}
const account = getAuthAccount()
const connection = ref<Connection | null>(null)
const members = ref<Member[]>([])
const memories = ref<WorkflowMemory[]>([])
const busy = ref(false)
const message = ref('')
const dialog = ref<HTMLElement | null>(null)
const previousFocus = document.activeElement as HTMLElement | null
const trapFocus = (event: KeyboardEvent) => {
  if (event.key !== 'Tab') return
  const controls = Array.from(dialog.value?.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), input:not(:disabled)') || [])
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) {
    event.preventDefault(); last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first?.focus()
  }
}
const roleNames: Record<Role, string> = { employee: '员工', lead: '项目负责人', admin: '管理员' }

const api = async (path: string, method = 'GET', body?: unknown) => {
  const response = await fetch(`/api/v1${path}`, {
    method, headers: { ...buildAuthHeaders(), 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '操作未成功')
  return payload.data
}
const load = async () => {
  const [nextConnection, nextMemories] = await Promise.all([
    api('/auth/feishu/status'),
    api('/workflow-memories')
  ])
  connection.value = nextConnection
  memories.value = Array.isArray(nextMemories) ? nextMemories : []
  members.value = connection.value?.role === 'admin' ? await api('/admin/members') : []
}
const run = async (action: () => Promise<void>) => {
  busy.value = true
  message.value = ''
  try { await action() } catch (error: unknown) {
    message.value = error instanceof Error ? error.message : '操作未成功'
  } finally { busy.value = false }
}
const connect = () => run(async () => { await authorizeFeishu([]); await load(); message.value = '飞书连接已更新' })
const disconnect = (member?: string) => run(async () => {
  const result = await api(member ? `/admin/members/${encodeURIComponent(member)}/feishu` : '/auth/feishu/connection', 'DELETE')
  await load()
  message.value = result.message || '已断开飞书连接'
})
const update = (member: Member, change: Partial<Member>) => run(async () => {
  await api(`/admin/members/${encodeURIComponent(member.account)}`, 'PATCH', change)
  await load()
  message.value = '成员权限已更新'
})
const activateMemory = (memory: WorkflowMemory) => run(async () => {
  await api(`/workflow-memories/${memory.id}/activate`, 'POST')
  await load()
  message.value = '已记住这个工作流；下次会优先参考已验证经验'
})
const forgetMemory = (memory: WorkflowMemory) => run(async () => {
  await api(`/workflow-memories/${memory.id}`, 'DELETE')
  await load()
  message.value = '已停用并移除这条工作流记忆'
})
onMounted(() => { dialog.value?.focus(); void run(load) })
onUnmounted(() => previousFocus?.focus())
</script>

<template>
  <div class="account-backdrop" @click.self="emit('close')" @keydown.esc="emit('close')">
    <section ref="dialog" class="account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-title" tabindex="-1" @keydown="trapFocus">
      <header><h2 id="account-title">账号与权限</h2><button class="icon-button" aria-label="关闭账号设置" title="关闭" @click="emit('close')"><X :size="18" /></button></header>
      <p>{{ account?.name }} <span v-if="connection" class="account-muted">{{ roleNames[connection.role] }}</span></p>
      <p v-if="message" role="status">{{ message }}</p>
      <section v-if="connection" class="account-section">
        <h3>飞书连接</h3>
        <p>{{ !connection.enabled ? '企业飞书登录尚未配置' : connection.connected ? '已连接' : '未连接' }}<span v-if="connection.connected && connection.missing_scopes.length"> · 有待补充权限</span></p>
        <div class="account-actions">
          <button :disabled="busy || !connection.enabled" @click="connect"><Link :size="15" />{{ connection.connected ? '更新授权' : '连接飞书' }}</button>
          <button :disabled="busy || !connection.enabled" @click="disconnect()"><Unlink :size="15" />断开连接</button>
          <button class="icon-button" :disabled="busy" aria-label="刷新连接状态" title="刷新状态" @click="run(load)"><RefreshCw :size="15" /></button>
        </div>
        <h3>可用能力</h3>
        <ul class="capability-list"><li v-for="item in connection.capabilities" :key="item.key">{{ item.title }}</li></ul>
      </section>
      <BotChannels />
      <section class="account-section">
        <h3><Brain :size="16" /> AI 工作流记忆</h3>
        <p class="account-muted memory-intro">只保存已成功执行的工作流结构；群 ID、人员、权限和写操作确认每次都会重新检查。</p>
        <p v-if="!memories.length" class="account-muted">暂时没有可复用的成功经验。</p>
        <div v-else class="memory-list">
          <div v-for="memory in memories" :key="memory.id" class="memory-row">
            <div>
              <strong>{{ memory.label }}</strong>
              <small>{{ memory.status === 'active' ? '已记住' : '候选经验' }} · 已成功 {{ memory.success_count }} 次</small>
            </div>
            <div class="memory-actions">
              <button v-if="memory.status === 'candidate'" :disabled="busy" @click="activateMemory(memory)">记住</button>
              <button class="icon-button" :disabled="busy" :aria-label="`停用${memory.label}`" title="停用并移除" @click="forgetMemory(memory)"><Trash2 :size="15" /></button>
            </div>
          </div>
        </div>
      </section>
      <section v-if="connection?.role === 'admin'" class="account-section">
        <h3>企业成员</h3>
        <div class="member-table-wrap"><table>
          <thead><tr><th>成员</th><th>角色</th><th>启用</th><th>飞书连接</th></tr></thead>
          <tbody><tr v-for="member in members" :key="member.account">
            <td>{{ member.name }}<small>{{ member.account }}</small></td>
            <td><select :value="member.role" :disabled="busy" :aria-label="`${member.name}的角色`" @change="update(member, { role: ($event.target as HTMLSelectElement).value as Role })">
              <option value="employee">员工</option><option value="lead">项目负责人</option><option value="admin">管理员</option>
            </select></td>
            <td><input type="checkbox" :checked="member.enabled" :disabled="busy" :aria-label="`启用${member.name}`" @change="update(member, { enabled: ($event.target as HTMLInputElement).checked })" /></td>
            <td><button class="icon-button" :disabled="busy || !connection.enabled" :aria-label="`断开${member.name}的飞书连接`" title="断开飞书连接" @click="disconnect(member.account)"><Unlink :size="16" /></button></td>
          </tr></tbody>
        </table></div>
      </section>
    </section>
  </div>
</template>

<style scoped>
.account-backdrop { position: fixed; inset: 0; z-index: 200; background: #0006; display: grid; place-items: center; padding: 16px; }
.account-dialog { width: min(760px, 100%); max-height: calc(100dvh - 32px); overflow: auto; background: #fff; color: #242424; border: 1px solid #dedede; border-radius: 8px; padding: 24px; box-sizing: border-box; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h2 { font-size: 20px; margin: 0; } h3 { font-size: 15px; margin: 20px 0 12px; }
h3 { display: flex; align-items: center; gap: 7px; }
p, li, td, th, button, select { font-size: 14px; } p { overflow-wrap: anywhere; }
.account-muted, small { color: #707070; } .account-muted { margin-left: 12px; }
.account-section { border-top: 1px solid #e5e5e5; margin-top: 20px; }
.account-actions { display: flex; gap: 10px; flex-wrap: wrap; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; border: 1px solid #dedede; background: #fafafa; border-radius: 4px; padding: 6px 12px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; } .icon-button { width: 36px; height: 36px; padding: 0; flex-shrink: 0; }
.capability-list { padding-left: 20px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; }
.memory-intro { margin-left: 0; }
.memory-list { display: grid; gap: 8px; }
.memory-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; border-bottom: 1px solid #eee; }
.memory-row strong { display: block; font-size: 14px; font-weight: 550; }
.memory-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.member-table-wrap { overflow-x: auto; } table { width: 100%; min-width: 540px; border-collapse: collapse; text-align: left; }
th, td { padding: 12px 8px; border-bottom: 1px solid #eee; } th { font-weight: 500; color: #666; } small { display: block; max-width: 220px; overflow-wrap: anywhere; margin-top: 4px; }
select { border: 1px solid #ddd; padding: 6px; border-radius: 4px; background: white; } input[type=checkbox] { width: 18px; height: 18px; accent-color: #168cab; }
@media (max-width: 480px) { .account-dialog { padding: 16px; } .capability-list { grid-template-columns: 1fr; } }
</style>
