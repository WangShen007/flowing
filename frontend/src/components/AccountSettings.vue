<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Brain, Copy, Link, RefreshCw, Trash2, Unlink, UserPlus, X } from 'lucide-vue-next'
import { buildAuthHeaders, getAuthAccount } from '../lib/auth'
import { authorizeFeishu } from '../lib/feishuAuth'
import BotChannels from './BotChannels.vue'

const emit = defineEmits<{ close: [] }>()
type Role = 'employee' | 'lead' | 'admin'
interface Member { account: string; name: string; role: Role; enabled: boolean; has_feishu_grant?: boolean; feishu_connected?: boolean; feishu_app_id?: string | null; feishu_tenant_key?: string | null }
interface Enterprise { app_id: string; app_name: string; tenant_key: string; tenant_name: string; identity_app_id: string | null; identity_tenant_key: string | null; matches_config: boolean }
interface Connection { enabled: boolean; role: Role; connected: boolean; has_grant?: boolean; enterprise?: Enterprise; configuration_issues?: string[]; missing_scopes: string[]; capabilities: { key: string; title: string }[] }
type InvitationStatus = 'pending' | 'redeemed' | 'revoked' | 'expired'
interface Invitation { id: string; status: InvitationStatus; created_at: number; expires_at: number; redeemed_by: string | null }
interface WorkflowMemory {
  id: number
  intent_key: string
  label: string
  display_label?: string
  status: 'candidate' | 'active'
  success_count: number
  failure_count: number
  last_used_at?: number
  updated_at: number
  created_at: number
}
interface WorkflowMemoryDetail extends WorkflowMemory {
  source_request: string | null
  procedure: string
  command_shapes: string[]
  relevant_skills: string[]
  requires_confirmation: boolean
}
const expandedMemory = ref<number | null>(null)
const memoryDetails = ref<Record<number, WorkflowMemoryDetail>>({})
const memoryLoading = ref<Record<number, boolean>>({})
const memoryErrors = ref<Record<number, string>>({})
const formatTime = (value?: number) => value
  ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未使用'
const toggleMemory = async (id: number) => {
  if (expandedMemory.value === id) { expandedMemory.value = null; return }
  expandedMemory.value = id
  if (memoryDetails.value[id] || memoryLoading.value[id]) return
  memoryLoading.value[id] = true
  memoryErrors.value[id] = ''
  try { memoryDetails.value[id] = await api(`/workflow-memories/${id}`) }
  catch (error: unknown) { memoryErrors.value[id] = error instanceof Error ? error.message : '详情加载失败' }
  finally { memoryLoading.value[id] = false }
}
const account = getAuthAccount()
const connection = ref<Connection | null>(null)
const members = ref<Member[]>([])
const invitations = ref<Invitation[]>([])
const invitationUrl = ref('')
const currentInvitationId = ref('')
const invitationCopyMessage = ref('')
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
const invitationStatusNames: Record<InvitationStatus, string> = {
  pending: '待使用', redeemed: '已使用', revoked: '已撤销', expired: '已过期'
}

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
  memoryDetails.value = {}
  expandedMemory.value = null
  if (connection.value?.role === 'admin') {
    const [nextMembers, nextInvitations] = await Promise.all([api('/admin/members'), api('/admin/invitations')])
    members.value = Array.isArray(nextMembers) ? nextMembers : []
    invitations.value = Array.isArray(nextInvitations) ? nextInvitations : []
  } else {
    members.value = []
    invitations.value = []
  }
}
const run = async (action: () => Promise<void>) => {
  busy.value = true
  message.value = ''
  try { await action() } catch (error: unknown) {
    message.value = error instanceof Error ? error.message : '操作未成功'
  } finally { busy.value = false }
}
const enterpriseLabel = (appId?: string | null, tenantKey?: string | null) => {
  const ent = connection.value?.enterprise
  if (!appId && !tenantKey) return ''
  const tenant = ent && ent.tenant_key === tenantKey ? ent.tenant_name : tenantKey || ''
  const app = ent && ent.app_id === appId ? ent.app_name : appId || ''
  return [tenant, app].filter(Boolean).join(' · ')
}
const connect = () => run(async () => {
  // '更新授权' must round-trip through Feishu even when the grant is already complete.
  await authorizeFeishu([], { force: Boolean(connection.value?.connected) })
  await load()
  message.value = '飞书连接已更新'
})
const pendingDisconnect = ref<string | null>(null)
const disconnectTargetName = (member?: string) =>
  member ? members.value.find((item) => item.account === member)?.name || member : '当前账号'
const requestDisconnect = (member?: string) => { pendingDisconnect.value = member || 'self' }
const cancelDisconnect = () => { pendingDisconnect.value = null }
const confirmDisconnect = (member?: string) => {
  pendingDisconnect.value = null
  void run(async () => {
    const result = await api(member ? `/admin/members/${encodeURIComponent(member)}/feishu` : '/auth/feishu/connection', 'DELETE')
    await load()
    message.value = result.message || `已断开${disconnectTargetName(member)}的飞书连接`
  })
}
const update = (member: Member, change: Partial<Member>) => run(async () => {
  await api(`/admin/members/${encodeURIComponent(member.account)}`, 'PATCH', change)
  await load()
  message.value = '成员权限已更新'
})
const createInvitation = () => run(async () => {
  const result = await api('/admin/invitations', 'POST')
  invitationUrl.value = result.url
  currentInvitationId.value = result.id
  invitationCopyMessage.value = ''
  await load()
  message.value = '邀请链接已生成，请私下发送给同事。'
})
const copyInvitation = async () => {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(invitationUrl.value)
    invitationCopyMessage.value = '邀请链接已复制。'
  } catch {
    invitationCopyMessage.value = '无法自动复制，请选中链接后手动复制。'
  }
}
const pendingRevoke = ref<string | null>(null)
const requestRevoke = (invitation: Invitation) => { pendingRevoke.value = invitation.id }
const cancelRevoke = () => { pendingRevoke.value = null }
const confirmRevoke = (invitation: Invitation) => {
  pendingRevoke.value = null
  void run(async () => {
    await api(`/admin/invitations/${encodeURIComponent(invitation.id)}`, 'DELETE')
    if (currentInvitationId.value === invitation.id) {
      invitationUrl.value = ''
      currentInvitationId.value = ''
      invitationCopyMessage.value = ''
    }
    await load()
    message.value = '邀请已撤销。'
  })
}
const invitationMemberName = (accountId: string | null) =>
  members.value.find((member) => member.account === accountId)?.name || accountId || ''
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
        <h3>企业飞书连接</h3>
        <p>{{ !connection.enabled ? '企业飞书登录尚未配置' : connection.connected ? '已连接' : '未连接' }}<span v-if="connection.connected && connection.missing_scopes.length"> · 有待补充权限</span></p>
        <p v-if="connection.enabled && connection.enterprise" class="account-muted member-intro">
          企业：{{ connection.enterprise.tenant_name }}<small v-if="connection.enterprise.tenant_key">（{{ connection.enterprise.tenant_key }}）</small>
          · 应用：{{ connection.enterprise.app_name }}<small v-if="connection.enterprise.app_id">（{{ connection.enterprise.app_id }}）</small>
        </p>
        <p v-if="connection.connected && connection.enterprise && !connection.enterprise.matches_config" class="configuration-issues" role="alert">
          当前授权来自 {{ connection.enterprise.identity_tenant_key }} / {{ connection.enterprise.identity_app_id }}，与本服务器配置的企业或应用不一致，请重新连接。
        </p>
        <p v-if="!connection.enabled" class="account-muted member-intro">管理员需先配置企业飞书应用。若只连接个人飞书，可关闭此面板后从左下角「重新授权」开始。</p>
        <ul v-if="connection.role === 'admin' && connection.configuration_issues?.length" class="configuration-issues" role="alert">
          <li v-for="issue in connection.configuration_issues" :key="issue">{{ issue }}</li>
        </ul>
        <div class="account-actions">
          <button :disabled="busy || !connection.enabled || !!connection.configuration_issues?.length" @click="connect"><Link :size="15" />{{ connection.connected ? '更新授权' : '连接飞书' }}</button>
          <template v-if="pendingDisconnect === 'self'">
            <button class="confirm-disconnect" :disabled="busy" @click="confirmDisconnect()"><Unlink :size="15" />确认断开</button>
            <button :disabled="busy" @click="cancelDisconnect">取消</button>
          </template>
          <button v-else :disabled="busy || !(connection.has_grant ?? connection.connected)" @click="requestDisconnect()"><Unlink :size="15" />断开连接</button>
          <button class="icon-button" :disabled="busy" aria-label="刷新连接状态" title="刷新状态" @click="run(load)"><RefreshCw :size="15" /></button>
        </div>
        <details class="capability-details">
          <summary>查看角色可申请的能力（{{ connection.capabilities.length }}）</summary>
          <ul class="capability-list"><li v-for="item in connection.capabilities" :key="item.key">{{ item.title }}</li></ul>
        </details>
      </section>
      <section v-if="connection?.role === 'admin'" class="account-section">
        <h3>工作区成员</h3>
        <p class="account-muted member-intro">这里管理已加入 Flowing 的账号，不会自动导入完整飞书通讯录。</p>
        <button :disabled="busy || !connection.enabled || !!connection.configuration_issues?.length" @click="createInvitation"><UserPlus :size="15" />邀请成员</button>
        <p v-if="!connection.enabled" class="account-muted member-intro">请先配置企业飞书登录，才能生成邀请链接。</p>
        <template v-else>
          <p class="account-muted member-intro">链接 7 天有效、仅可使用一次。任何持有链接的同企业成员都可使用，请私下发送给指定同事；加入后默认是员工。被邀请人必须在飞书开放平台「版本管理与发布 → 可用范围」内，否则授权页会提示没有使用权限。</p>
          <p class="account-muted member-intro">应用可用范围内的同企业成员也可直接通过飞书登录加入；邀请链接不限制其他人登录。</p>
        </template>
        <div v-if="invitationUrl" class="invitation-link">
          <label for="invitation-url">本次邀请链接</label>
          <div class="invitation-link-controls">
            <input id="invitation-url" :value="invitationUrl" readonly @focus="($event.target as HTMLInputElement).select()" />
            <button @click="copyInvitation"><Copy :size="15" />复制</button>
          </div>
          <small>链接只在生成时显示；离开此面板后需重新生成。</small>
          <small v-if="invitationCopyMessage" role="status">{{ invitationCopyMessage }}</small>
        </div>
        <ul v-if="invitations.length" class="invitation-list" aria-label="邀请记录">
          <li v-for="invitation in invitations" :key="invitation.id" class="invitation-row">
            <span>{{ invitationStatusNames[invitation.status] }} · {{ formatTime(invitation.expires_at) }} 到期<span v-if="invitation.redeemed_by"> · {{ invitationMemberName(invitation.redeemed_by) }}</span></span>
            <span v-if="invitation.status === 'pending' && pendingRevoke === invitation.id" class="inline-confirm">
              <button :disabled="busy" @click="confirmRevoke(invitation)">确认撤销</button>
              <button :disabled="busy" @click="cancelRevoke">取消</button>
            </span>
            <button v-else-if="invitation.status === 'pending'" :disabled="busy" @click="requestRevoke(invitation)">撤销</button>
          </li>
        </ul>
        <div class="member-table-wrap"><table>
          <thead><tr><th>成员</th><th>角色</th><th>启用</th><th>企业授权</th></tr></thead>
          <tbody><tr v-for="member in members" :key="member.account">
            <td>{{ member.name }}<small>{{ member.account }}</small></td>
            <td><select :value="member.role" :disabled="busy" :aria-label="`${member.name}的角色`" @change="update(member, { role: ($event.target as HTMLSelectElement).value as Role })">
              <option value="employee">员工</option><option value="lead">项目负责人</option><option value="admin">管理员</option>
            </select></td>
            <td><input type="checkbox" :checked="member.enabled" :disabled="busy" :aria-label="`启用${member.name}`" @change="update(member, { enabled: ($event.target as HTMLInputElement).checked })" /></td>
            <td class="member-connection">
              <span v-if="member.has_feishu_grant !== undefined">{{ member.feishu_connected ? '授权有效期内' : member.has_feishu_grant ? '授权失效或待撤销' : '未保存授权' }}</span>
              <small v-if="member.feishu_app_id || member.feishu_tenant_key">{{ enterpriseLabel(member.feishu_app_id, member.feishu_tenant_key) }}</small>
              <span v-if="pendingDisconnect === member.account" class="inline-confirm">
                <button :disabled="busy" @click="confirmDisconnect(member.account)">确认断开</button>
                <button :disabled="busy" @click="cancelDisconnect">取消</button>
              </span>
              <button v-else class="icon-button" :disabled="busy || member.has_feishu_grant === false" :aria-label="`断开${member.name}的企业飞书授权`" title="断开企业飞书授权" @click="requestDisconnect(member.account)"><Unlink :size="16" /></button>
            </td>
          </tr></tbody>
        </table></div>
      </section>
      <BotChannels />
      <section class="account-section">
        <h3><Brain :size="16" /> AI 工作流记忆</h3>
        <p class="account-muted memory-intro">只保存已成功执行的工作流结构；群 ID、人员、权限和写操作确认每次都会重新检查。</p>
        <p v-if="!memories.length" class="account-muted">暂时没有可复用的成功经验。</p>
        <div v-else class="memory-list">
          <div v-for="memory in memories" :key="memory.id" class="memory-item">
            <div class="memory-row">
            <div>
              <strong>{{ memory.display_label || memory.label }}</strong>
              <small>#{{ memory.id }} · {{ memory.status === 'active' ? '已记住' : '候选经验' }} · 已成功 {{ memory.success_count }} 次</small>
            </div>
            <div class="memory-actions">
              <button :aria-expanded="expandedMemory === memory.id" :aria-controls="`memory-detail-${memory.id}`" @click="toggleMemory(memory.id)">{{ expandedMemory === memory.id ? '收起详情' : '查看详情' }}</button>
              <button v-if="memory.status === 'candidate'" :disabled="busy" @click="activateMemory(memory)">记住</button>
              <button class="icon-button" :disabled="busy" :aria-label="`停用${memory.label}`" title="停用并移除" @click="forgetMemory(memory)"><Trash2 :size="15" /></button>
            </div>
            </div>
            <div v-if="expandedMemory === memory.id" :id="`memory-detail-${memory.id}`" class="memory-detail" :aria-busy="memoryLoading[memory.id]">
              <p v-if="memoryLoading[memory.id]" role="status">正在加载记忆内容…</p>
              <p v-else-if="memoryErrors[memory.id]" role="alert">{{ memoryErrors[memory.id] }}，请收起后重新展开重试。</p>
              <template v-else-if="memoryDetails[memory.id]">
                <h4>最近一次成功的来源需求</h4>
                <p class="memory-content">{{ memoryDetails[memory.id].source_request || '原始执行记录已不存在，无法还原来源需求。' }}</p>
                <h4>已保存的工作流步骤</h4>
                <p class="memory-content">{{ memoryDetails[memory.id].procedure || '这条旧记忆没有保存文字步骤；下方仅展示实际保存的操作结构。' }}</p>
                <h4>涉及的操作</h4>
                <ul v-if="memoryDetails[memory.id].command_shapes.length"><li v-for="(command, index) in memoryDetails[memory.id].command_shapes" :key="index"><code>{{ command }}</code></li></ul>
                <p v-else>未保存操作结构。</p>
                <p v-if="memoryDetails[memory.id].relevant_skills.length">相关技能：{{ memoryDetails[memory.id].relevant_skills.join('、') }}</p>
                <p>历史流程{{ memoryDetails[memory.id].requires_confirmation ? '包含需确认的操作' : '未标记需确认的操作' }}；再次执行仍以当前权限及确认流程为准。</p>
                <dl class="memory-times"><dt>创建时间</dt><dd>{{ formatTime(memoryDetails[memory.id].created_at) }}</dd><dt>更新时间</dt><dd>{{ formatTime(memoryDetails[memory.id].updated_at) }}</dd><dt>最近复用</dt><dd>{{ formatTime(memoryDetails[memory.id].last_used_at) }}</dd><dt>失败次数</dt><dd>{{ memoryDetails[memory.id].failure_count }}</dd></dl>
              </template>
            </div>
          </div>
        </div>
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
.configuration-issues { color: #a52b2b; padding-left: 20px; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; border: 1px solid #dedede; background: #fafafa; border-radius: 4px; padding: 6px 12px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; } .icon-button { width: 36px; height: 36px; padding: 0; flex-shrink: 0; }
.capability-details { margin-top: 16px; font-size: 14px; }
.capability-details summary { cursor: pointer; }
.capability-list { padding-left: 20px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; }
.memory-intro { margin-left: 0; }
.memory-list { display: grid; gap: 8px; }
.memory-row > div:first-child { min-width: 0; overflow-wrap: anywhere; }
.memory-detail { padding: 16px; background: #f7f8fa; border-radius: 6px; margin-bottom: 12px; overflow-wrap: anywhere; }
.memory-detail h4 { font-size: 13px; margin: 12px 0 6px; }
.memory-detail h4:first-child { margin-top: 0; }
.memory-detail p { margin: 6px 0 14px; }
.memory-content { white-space: pre-wrap; }
.memory-detail ul { padding-left: 20px; }
.memory-detail code { white-space: pre-wrap; overflow-wrap: anywhere; }
.memory-times { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; font-size: 12px; color: #666; }
.memory-times dd { margin: 0; }
@media (max-width: 600px) { .memory-row { flex-wrap: wrap; } .memory-actions { flex-wrap: wrap; } }
.memory-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; border-bottom: 1px solid #eee; }
.memory-row strong { display: block; font-size: 14px; font-weight: 550; }
.memory-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.member-intro { margin-left: 0; line-height: 1.5; }
.inline-confirm, .confirm-disconnect { display: inline-flex; gap: 8px; align-items: center; }
.inline-confirm button, .confirm-disconnect { color: #c0392b; }
.invitation-link { margin: 12px 0; }
.invitation-link label { display: block; font-size: 14px; margin-bottom: 6px; }
.invitation-link-controls { display: flex; gap: 8px; }
.invitation-link-controls input { flex: 1; min-width: 0; border: 1px solid #dedede; border-radius: 4px; padding: 8px; font-size: 14px; }
.invitation-list { list-style: none; padding: 0; margin: 12px 0; }
.invitation-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid #eee; }
.member-table-wrap { overflow-x: auto; } table { width: 100%; min-width: 540px; border-collapse: collapse; text-align: left; }
.member-connection { white-space: nowrap; } .member-connection span { margin-right: 8px; }
th, td { padding: 12px 8px; border-bottom: 1px solid #eee; } th { font-weight: 500; color: #666; } small { display: block; max-width: 220px; overflow-wrap: anywhere; margin-top: 4px; }
select { border: 1px solid #ddd; padding: 6px; border-radius: 4px; background: white; } input[type=checkbox] { width: 18px; height: 18px; accent-color: #168cab; }
@media (max-width: 480px) { .account-dialog { padding: 16px; } .capability-list { grid-template-columns: 1fr; } .invitation-row { flex-wrap: wrap; } }
</style>
