<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, shallowReactive } from 'vue'
import { authorizeFeishu, FEISHU_OAUTH_CONTINUATION_KEY } from '../lib/feishuAuth'
import AccountSettings from './AccountSettings.vue'
import { useRouter } from 'vue-router'
import { renderMarkdown } from '../lib/markdown'
import { AiPptResourceError, fetchAiPptBlob, safeAiPptResourceUrl } from '../lib/aiPptResources'
import {
  Blocks,
  Brain,
  CalendarClock,
  ChevronRight,
  Command as CommandIcon,
  Download,
  LayoutTemplate,
  LogOut,
  PanelLeft,
  Plus,
  RefreshCw,
  Send,
  SlidersHorizontal,
  Square,
  Trash2,
  Upload,
  UserRound,
  Users
} from 'lucide-vue-next'
import WelcomeView from './WelcomeView.vue'
import BrandMark from './BrandMark.vue'
import { buildAuthHeaders, clearAuthSession, getAuthAccount } from '../lib/auth'

interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  loading?: boolean
  metadata?: Record<string, any>
}

interface HistoryItem {
  id: string
  title: string
  time: string
}

interface Skill {
  id: string
  name: string
  icon: string
  description: string
}

interface SetupStep {
  key: string
  title: string
  command?: string
  display_command?: string
  description?: string
  status?: 'pending' | 'running' | 'success' | 'failed'
}

interface ScenarioField {
  key: string
  label: string
  placeholder: string
}

interface ScenarioTemplate {
  id: string
  title: string
  category: string
  description: string
  fields: ScenarioField[]
  requires_ai_content_generation?: boolean
  content_generation_label?: string
  enterprise_ready?: boolean
  enterprise_reason?: string
}

interface PlanCommand {
  command: string
  reason: string
  expected: string
  write: boolean
}

interface PendingTool {
  operation: string
  arguments: Record<string, unknown>
  command?: string
}

interface WritePreviewField {
  label: string
  value: string
}

interface WritePreview {
  title: string
  summary: string
  fields: WritePreviewField[]
}

interface PlanPreview {
  requires_input?: boolean
  query: string
  summary: string
  relevant_skills: string[]
  references: string[]
  reason_for_confirmation: string
  need_confirmation: boolean
  commands: PlanCommand[]
  planning_source?: 'langgraph' | 'deterministic' | 'normalized_deterministic' | 'model' | 'memory' | 'memory_assisted_model' | 'memory_assisted_fallback' | 'fallback'
  planning_duration_ms?: number
  confidence?: number
  corrections?: string[]
  workflow_memory?: WorkflowMemorySummary
  cli_state?: Record<string, any>
}

interface WorkflowMemorySummary {
  id: number
  intent_key: string
  label: string
  status: 'candidate' | 'active'
  success_count: number
  failure_count: number
  match_score?: number
  exact_match?: boolean
}

interface ScheduledTaskConfig {
  enabled: boolean
  poll_seconds: number
  timezone: string
}

interface ScheduledTaskItem {
  id: number
  user_id: string
  session_id: string
  original_request: string
  task_message: string
  schedule_type: string
  time_of_day: string
  timezone: string
  next_run_at: number
  last_run_at?: number
  status: string
  run_count: number
  last_result?: {
    status?: string
    success?: boolean
    requires_confirmation?: boolean
    message?: string
    [key: string]: unknown
  }
}

const router = useRouter()
const messages = ref<Message[]>([])
const inputText = ref('')
const isLoading = ref(false)
const isInitializing = ref(true)
const messagesContainer = ref<HTMLElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const shouldStickToBottom = ref(true)
let scrollFrame = 0
const sessionId = ref('')
const historyList = ref<HistoryItem[]>([])
const showSidebar = ref(false)
const currentSkill = ref('lark_cli')
let viewVersion = 0
let requestController: AbortController | null = null
let planController: AbortController | null = null
let setupController: AbortController | null = null
const PLAN_REQUEST_TIMEOUT_MS = 30_000
let activeRunId = ''
const drafts = new Map<string, string>()
const runningChats = shallowReactive(new Map<string, { controller: AbortController; runId: string; messages: Message[] }>())
const leaveConversation = () => {
  viewVersion += 1
  releaseAiPptObjectUrls()
  planController?.abort()
  planController = null
  setupController?.abort()
  setupController = null
  larkSetupRunning.value = false
  requestController = null
  activeRunId = ''
  isLoading.value = false
  planLoading.value = false
  showWriteConfirm.value = false
  pendingWriteTool.value = null
  clearPlanPreview()
}
const pendingResume = ref<{ id: string; query: string; session: string } | null>(null)
const isBusy = computed(() => isLoading.value || planLoading.value)

const stopActiveRequest = async () => {
  viewVersion += 1
  planController?.abort()
  planController = null
  setupController?.abort()
  setupController = null
  larkSetupRunning.value = false
  const controller = requestController
  const runId = activeRunId
  runningChats.delete(sessionId.value)
  requestController = null
  activeRunId = ''
  isLoading.value = false
  planLoading.value = false
  for (const message of messages.value) {
    if (message.loading || (!message.content && message.role === 'assistant')) {
      message.loading = false
      message.content = '已停止。已提交的操作可在执行详情中核对。'
    }
  }
  clearPlanPreview()
  showWriteConfirm.value = false
  pendingWriteMessage.value = ''
  try {
    if (runId) await fetch(`/api/v1/chat/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST', headers: authHeaders(), signal: AbortSignal.timeout(5000)
    })
  } catch (error) {
    console.error('停止请求未确认:', error)
  } finally {
    controller?.abort()
  }
}
const showWriteConfirm = ref(false)
const pendingWriteMessage = ref('')
const pendingWriteSkill = ref('lark_cli')
const pendingWriteTool = ref<PendingTool | null>(null)
const currentAccount = ref(getAuthAccount())
const isAdmin = computed(() => currentAccount.value?.role === 'admin')
const planPreview = ref<PlanPreview | null>(null)
const planLoading = ref(false)
const planElapsed = ref(0)
const planError = ref('')
const pendingPlanMessage = ref('')
const pendingPlanSkill = ref('lark_cli')
const planProgressText = computed(() => planElapsed.value < 2 ? '正在识别指令' : '正在生成安全执行计划')
const planSourceLabel = (source?: PlanPreview['planning_source']) => {
  if (source === 'langgraph') return 'AI 自主执行'
  if (source === 'deterministic') return '本地确定性识别'
  if (source === 'normalized_deterministic') return '规范化后本地识别'
  if (source === 'model') return 'AI 辅助规划'
  if (source === 'memory') return '你的历史成功流程'
  if (source === 'memory_assisted_model') return '历史经验 + AI 规划'
  if (source === 'memory_assisted_fallback') return '历史经验匹配，等待补充'
  if (source === 'fallback') return '安全降级计划'
  return ''
}
const scenarioTemplates = ref<ScenarioTemplate[]>([])
const showScenarioPanel = ref(false)
const selectedScenario = ref<ScenarioTemplate | null>(null)
const scenarioValues = ref<Record<string, string>>({})
const scenarioAiContentGeneration = ref(true)
const showSchedulePanel = ref(false)
const scheduleLoading = ref(false)
const scheduleSaving = ref(false)
const scheduledConfig = ref<ScheduledTaskConfig>({ enabled: true, poll_seconds: 30, timezone: 'Asia/Shanghai' })
const scheduledTasks = ref<ScheduledTaskItem[]>([])
const scheduleStatus = ref('')
const aiPptViewAll = ref<Record<number, boolean>>({})
const aiPptObjectUrls = new Set<string>()
const aiPptActions = ref<Record<number, {
  action: 'upload' | 'send_group' | 'send_person' | ''
  target: string
  folderToken: string
  wikiToken: string
  message: string
  loading: boolean
  result: string
  error: string
}>>({})

const releaseAiPptObjectUrls = () => {
  for (const url of aiPptObjectUrls) URL.revokeObjectURL(url)
  aiPptObjectUrls.clear()
  const messageLists = [messages.value, ...Array.from(runningChats.values(), (run) => run.messages)]
  for (const list of messageLists) {
    for (const message of list) {
      const preview = message.metadata?.ai_ppt_preview
      if (!preview || !Array.isArray(preview.slides)) continue
      message.metadata = {
        ...(message.metadata || {}),
        ai_ppt_preview: {
          ...preview,
          slides: preview.slides.map((slide: Record<string, unknown> | null) => {
            if (!slide || typeof slide !== 'object') return slide
            const { image_src: _imageSrc, ...rest } = slide
            return rest
          })
        }
      }
    }
  }
}

const showLarkSetup = ref(false)
const larkSetupRunning = ref(false)
const larkSetupMessage = ref('')
const larkSetupAuthUrl = ref('')
const larkSetupUserCode = ref('')
const larkSetupExpiresIn = ref('')
const larkSetupTerminal = ref('')
const larkSetupShowLog = ref(false)
const larkSetupSteps = ref<SetupStep[]>([])
const larkSetupScopes = ref<string[]>([])
const larkSetupForceAuth = ref(false)
const larkSetupTitle = computed(() => {
  if (larkSetupForceAuth.value) return '重新授权飞书账号'
  return larkSetupScopes.value.length ? '补充飞书权限' : '连接飞书账号'
})
const larkSetupActionText = computed(() => {
  if (larkSetupRunning.value) return '处理中...'
  if (larkSetupForceAuth.value) return '开始重新授权'
  return larkSetupScopes.value.length ? '补充授权' : '开始连接'
})
const larkSetupHint = computed(() => {
  if (larkSetupForceAuth.value) return '会先退出当前飞书授权，再按首次连接流程生成新的授权链接。'
  if (larkSetupScopes.value.length) return '完成补充授权后，将从中断的步骤继续执行。'
  return '首次连接会先准备当前账号的独立 CLI 环境，再生成飞书授权链接。'
})

const showModelPanel = ref(false)
const modelPresets = ref<any[]>([])
const modelPreset = ref('qwen')
const modelApiKey = ref('')
const modelBaseUrl = ref('')
const modelName = ref('')
const modelStatus = ref('')
const modelSaving = ref(false)
const currentModel = ref<Record<string, string>>({})

const skills: Skill[] = [
  {
    id: 'auto',
    name: '自动',
    icon: 'auto',
    description: '自动识别飞书需求并交给飞书 CLI 处理'
  },
  {
    id: 'lark_cli',
    name: '飞书CLI',
    icon: 'ops',
    description: '执行飞书 CLI 命令，支持消息、日历、文档、云空间、表格、多维表格等'
  }
]

const authHeaders = () => buildAuthHeaders()
const memoryActionBusy = ref<Record<number, boolean>>({})
const workflowMemoryFor = (msg: Message): WorkflowMemorySummary | null => {
  const memory = msg.metadata?.workflow_memory
  return memory && typeof memory.id === 'number' ? memory as WorkflowMemorySummary : null
}
const workflowMemoryBusy = (msg: Message) => {
  const memory = workflowMemoryFor(msg)
  return memory ? Boolean(memoryActionBusy.value[memory.id]) : false
}
const updateMessageMemory = (msg: Message, memory: WorkflowMemorySummary | null) => {
  msg.metadata = { ...(msg.metadata || {}), workflow_memory: memory }
}
const activateWorkflowMemory = async (msg: Message) => {
  const memory = workflowMemoryFor(msg)
  if (!memory || memoryActionBusy.value[memory.id]) return
  memoryActionBusy.value = { ...memoryActionBusy.value, [memory.id]: true }
  try {
    const response = await fetch(`/api/v1/workflow-memories/${memory.id}/activate`, {
      method: 'POST', headers: authHeaders()
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '记忆保存失败')
    updateMessageMemory(msg, payload.data)
  } catch (error: unknown) {
    msg.metadata = {
      ...(msg.metadata || {}),
      workflow_memory_error: error instanceof Error ? error.message : '记忆保存失败'
    }
  } finally {
    memoryActionBusy.value = { ...memoryActionBusy.value, [memory.id]: false }
  }
}
const forgetWorkflowMemory = async (msg: Message) => {
  const memory = workflowMemoryFor(msg)
  if (!memory || memoryActionBusy.value[memory.id]) return
  memoryActionBusy.value = { ...memoryActionBusy.value, [memory.id]: true }
  try {
    const response = await fetch(`/api/v1/workflow-memories/${memory.id}`, {
      method: 'DELETE', headers: authHeaders()
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '记忆移除失败')
    updateMessageMemory(msg, null)
  } catch (error: unknown) {
    msg.metadata = {
      ...(msg.metadata || {}),
      workflow_memory_error: error instanceof Error ? error.message : '记忆移除失败'
    }
  } finally {
    memoryActionBusy.value = { ...memoryActionBusy.value, [memory.id]: false }
  }
}

const parseApiJson = async (response: Response) => {
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    throw new Error('后端没有返回 JSON，请确认服务已重启并且 /api 已正确转发到后端。')
  }
  return response.json()
}

const apiErrorMessage = async (response: Response, fallback: string) => {
  try {
    const payload = await parseApiJson(response)
    return payload.detail || payload.message || fallback
  } catch (_error) {
    return fallback
  }
}

const currentUserId = computed(() => currentAccount.value?.account || 'local')

const handleMessagesScroll = () => {
  const el = messagesContainer.value
  if (!el) return
  shouldStickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 96
}

const scrollToBottom = async (force = false) => {
  if (!force && !shouldStickToBottom.value) return
  await nextTick()
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    const el = messagesContainer.value
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: force ? 'auto' : 'smooth' })
  })
}

const syncSessionUrl = (id: string, replace = true) => {
  const url = id ? `?session=${encodeURIComponent(id)}` : window.location.pathname
  if (replace) window.history.replaceState({}, '', url)
  else window.history.pushState({}, '', url)
}

const formatTime = (timestamp: number) => {
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const formatDateTime = (timestamp?: number) => {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const scheduleTypeText = (type: string) => (type === 'daily' ? '每天重复' : '一次性')

const scheduleStatusText = (status: string) => {
  const map: Record<string, string> = {
    active: '待执行',
    paused: '已暂停',
    running: '执行中',
    completed: '已完成',
    failed: '失败'
  }
  return map[status] || status
}

const canDeleteScheduledTask = (task: ScheduledTaskItem) => ['paused', 'completed', 'failed'].includes(task.status)

const scheduledTaskNeedsVerification = (task: ScheduledTaskItem) => (
  task.last_result?.status === 'unknown' || task.last_result?.requires_confirmation === true
)

const scheduledTaskResultText = (task: ScheduledTaskItem) => {
  const result = task.last_result || {}
  if (!Object.keys(result).length) return ''
  if (scheduledTaskNeedsVerification(task)) {
    const message = typeof result.message === 'string' && result.message.trim() ? `：${result.message.trim()}` : ''
    return `结果待核实，可能已经执行成功${message}`
  }
  if (typeof result.message === 'string' && result.message.trim()) return result.message.trim()
  if (typeof result.success === 'boolean') return result.success ? '最近执行成功' : '最近执行失败'
  return ''
}

const normalizeDisplayText = (value: string) => (value || '').replace(/\\n/g, '\n')

const formatContent = (content: string) => renderMarkdown(content)

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

const normalizePendingTool = (value: unknown): PendingTool | null => {
  const candidate = asRecord(value)
  const operation = candidate?.operation
  const args = asRecord(candidate?.arguments)
  if (!candidate || typeof operation !== 'string' || !operation.trim() || !args) return null
  return {
    operation: operation.trim(),
    arguments: args,
    command: typeof candidate.command === 'string' ? candidate.command : undefined
  }
}

const setPendingWriteTool = (value: unknown) => {
  pendingWriteTool.value = normalizePendingTool(value)
}

const parseArgumentObject = (value: unknown): Record<string, unknown> | null => {
  if (typeof value !== 'string') return asRecord(value)
  try { return asRecord(JSON.parse(value)) } catch (_error) { return null }
}

const argumentCandidates = (tool: PendingTool): Record<string, unknown>[] => {
  const candidates = [tool.arguments]
  for (const key of ['params', 'data', 'body']) {
    const nested = parseArgumentObject(tool.arguments[key])
    if (nested) candidates.push(nested)
  }
  return candidates
}

const getArgument = (tool: PendingTool, keys: string[]): unknown => {
  for (const candidate of argumentCandidates(tool)) {
    for (const key of keys) {
      if (candidate[key] !== undefined && candidate[key] !== null && candidate[key] !== '') return candidate[key]
    }
  }
  return undefined
}

const displayArgumentValue = (value: unknown, maxLength = 900): string => {
  let text = ''
  if (typeof value === 'string') text = value
  else if (typeof value === 'number' || typeof value === 'boolean') text = String(value)
  else if (value !== undefined && value !== null) {
    try { text = JSON.stringify(value, null, 2) } catch (_error) { text = String(value) }
  }
  if (!text) return '未提供'
  return text.length > maxLength ? `${text.slice(0, maxLength)}\n…（其余内容请展开原始参数查看）` : text
}

const displayCalendarTime = (value: unknown): string => {
  const candidate = asRecord(value)
  if (typeof candidate?.datetime === 'string') {
    return `${candidate.datetime.replace('T', ' ')}（${candidate.timezone || 'Asia/Shanghai · 北京时间'}）`
  }
  const rawTimestamp = candidate?.timestamp
  if (rawTimestamp === undefined || rawTimestamp === null || rawTimestamp === '') return displayArgumentValue(value)
  const timestamp = Number(rawTimestamp)
  const timezone = typeof candidate?.timezone === 'string' ? candidate.timezone : 'Asia/Shanghai'
  if (!Number.isFinite(timestamp)) return displayArgumentValue(value)
  const milliseconds = Math.abs(timestamp) >= 1_000_000_000_000 ? timestamp : timestamp * 1000
  try {
    const dateText = new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium',
      timeStyle: 'short',
      ...(timezone ? { timeZone: timezone } : {})
    }).format(new Date(milliseconds))
    return `${dateText}${timezone ? `（${timezone}；timestamp: ${rawTimestamp}）` : `（timestamp: ${rawTimestamp}）`}`
  } catch (_error) {
    return displayArgumentValue(value)
  }
}

const displayMentions = (text: string): string => text.replace(
  /<at\s+user_id="([^"]+)">([^<]*)<\/at>/g,
  (_match, id: string, name: string) => name ? `@${name}（${id}）` : `@${id}`
)

const messageTextFromTool = (tool: PendingTool): string => {
  const content = getArgument(tool, ['text', 'message', 'content'])
  const contentObject = parseArgumentObject(content)
  const text = contentObject?.text ?? content
  return typeof text === 'string' ? displayMentions(text) : displayArgumentValue(text)
}

const operationTitle = (operation: string): string => {
  const normalized = operation.toLowerCase()
  if (normalized === 'business_resources save') return '保存业务资源配置'
  if (normalized === 'business_resources delete') return '删除业务资源配置'
  if (normalized.includes('messages-send')) return '发送飞书消息'
  if (normalized.includes('calendar') && normalized.includes('update')) return '更新日历安排'
  if (normalized.includes('calendar') && (normalized.includes('create') || normalized.includes('attendees'))) return '创建日历/会议安排'
  if (normalized.startsWith('vc ')) return '发布视频会议安排'
  if (normalized.startsWith('base ')) return '修改多维表格'
  if (normalized.startsWith('task ')) return '修改任务'
  if (normalized.startsWith('docs ')) return '修改文档'
  return `确认执行飞书写操作：${operation}`
}

const addPreviewField = (
  fields: WritePreviewField[],
  label: string,
  value: unknown,
  formatter: (value: unknown) => string = displayArgumentValue
) => {
  if (value === undefined || value === null || value === '') return
  fields.push({ label, value: formatter(value) })
}

const buildWritePreview = (tool: PendingTool): WritePreview => {
  const operation = tool.operation.toLowerCase()
  const fields: WritePreviewField[] = []
  const isMessage = operation.includes('messages-send')
  const isCalendar = operation.includes('calendar')
  const isMeeting = operation.startsWith('vc ')
  const isBitable = operation.startsWith('base ')
  const isResourceConfig = operation.startsWith('business_resources ')

  if (isResourceConfig) {
    addPreviewField(fields, '资源别名', getArgument(tool, ['alias']))
    addPreviewField(fields, '多维表格', getArgument(tool, ['base_token']))
    addPreviewField(fields, '数据表', getArgument(tool, ['table_id']))
    addPreviewField(fields, '业务字段映射', getArgument(tool, ['fields']))
  } else if (isMessage) {
    addPreviewField(fields, '发送目标', getArgument(tool, ['chat-id', 'chat_id', 'receive-id', 'receive_id', 'user-id', 'user_id']))
    addPreviewField(fields, '消息正文', messageTextFromTool(tool))
  } else if (isCalendar || isMeeting) {
    addPreviewField(fields, '标题', getArgument(tool, ['summary', 'title', 'subject', 'name']))
    addPreviewField(fields, '开始时间', getArgument(tool, ['start-time', 'start_time', 'start', 'start_at']), displayCalendarTime)
    addPreviewField(fields, '结束时间', getArgument(tool, ['end-time', 'end_time', 'end', 'end_at']), displayCalendarTime)
    addPreviewField(fields, '时区', getArgument(tool, ['timezone', 'time-zone', 'time_zone']))
    addPreviewField(fields, '参会人', getArgument(tool, ['attendees', 'invitees', 'participants', 'user_ids']))
    addPreviewField(fields, '日历/会议', getArgument(tool, ['calendar-id', 'calendar_id', 'meeting-id', 'meeting_id', 'event-id', 'event_id']))
  } else if (isBitable) {
    addPreviewField(fields, '多维表格', getArgument(tool, ['base-token', 'base_token', 'app-token', 'app_token', 'app-id', 'app_id']))
    addPreviewField(fields, '数据表', getArgument(tool, ['table-id', 'table_id']))
    addPreviewField(fields, '记录', getArgument(tool, ['record-id', 'record_id', 'records', 'record']))
    addPreviewField(fields, '字段变更', getArgument(tool, ['fields', 'data', 'values']))
    const payload = parseArgumentObject(tool.arguments.json)
    if (payload) {
      const updates = asRecord(payload.update_records)
      if (updates) {
        addPreviewField(fields, '记录', Object.keys(updates))
        addPreviewField(fields, '字段变更', updates)
      } else {
        addPreviewField(fields, '字段变更', payload.create_records ?? payload)
      }
    } else if (tool.arguments.json !== undefined) {
      addPreviewField(fields, '字段变更（原始 JSON）', tool.arguments.json)
    }
  } else {
    addPreviewField(fields, '目标', getArgument(tool, ['task-id', 'task_id', 'task-guid', 'task_guid', 'doc', 'doc-token', 'doc_token', 'file-token', 'file_token']))
    addPreviewField(fields, '内容/变更', getArgument(tool, ['title', 'content', 'markdown', 'fields', 'data', 'params']))
  }

  if (!fields.length) {
    for (const [key, value] of Object.entries(tool.arguments).slice(0, 6)) {
      if (key === 'params' || key === 'data') continue
      addPreviewField(fields, key, value)
    }
  }
  return {
    title: operationTitle(tool.operation),
    summary: isResourceConfig
      ? '仅保存或删除当前网站账号的资源配置，可跨设备使用；不会删除飞书数据，也不会授予额外权限。保存同名配置将替换旧映射。'
      : isMessage
      ? '请核对发送对象和正文；@ 后的名称来自当前操作参数，括号内保留原始用户 ID。'
      : isCalendar || isMeeting
        ? '请核对时间、时区和参会人；无法解析的 ID 会原样展示。'
        : isBitable
          ? '请核对目标多维表格、数据表和字段变更；系统不会替你猜测名称。'
          : '请核对以下目标和变更内容后再执行。',
    fields
  }
}

const pendingWritePreview = computed<WritePreview | null>(() => (
  pendingWriteTool.value ? buildWritePreview(pendingWriteTool.value) : null
))

const pendingWriteRaw = (tool: PendingTool | null): string => {
  if (!tool) return ''
  return JSON.stringify({ operation: tool.operation, arguments: tool.arguments, command: tool.command }, null, 2)
}

const setupStepCopy: Record<string, { title: string; description: string }> = {
  install_cli: {
    title: '安装飞书 CLI',
    description: '当前服务器还没有检测到 lark-cli，需要先安装官方 CLI。'
  },
  install_skills: {
    title: '安装飞书能力包',
    description: '安装官方飞书 CLI skills，让系统能识别消息、日程、文档、多维表格等命令。'
  },
  config_init: {
    title: '初始化飞书应用配置',
    description: '为当前 Web 账号准备独立的飞书 CLI 配置，后续授权会写入这个隔离环境。'
  },
  clear_auth: {
    title: '退出旧授权',
    description: '先清除当前账号已有的飞书登录态，避免新旧授权混用。'
  },
  auth_login: {
    title: '打开飞书授权链接',
    description: '系统会生成一个授权链接，请在浏览器里完成登录和授权。'
  }
}

const normalizeSetupStep = (step: any): SetupStep => {
  const copy = setupStepCopy[step.key] || {}
  return {
    key: step.key,
    title: copy.title || step.title,
    description: copy.description || step.description || '',
    command: step.display_command || step.command || '',
    display_command: step.display_command || step.command || '',
    status: step.status || 'pending'
  }
}

const loadScenarios = async () => {
  scenarioError.value = ''
  try {
    const response = await fetch('/api/v1/scenarios', { headers: authHeaders() })
    const payload = await parseApiJson(response)
    if (!response.ok || payload.code !== 0) throw new Error('模板加载失败，请重试。')
    scenarioTemplates.value = payload.data || []
  } catch (error) {
    scenarioError.value = error instanceof Error ? error.message : '模板加载失败，请重试。'
  }
}

const loadScheduledTasks = async () => {
  scheduleLoading.value = true
  scheduleStatus.value = ''
  try {
    const [configResponse, tasksResponse] = await Promise.all([
      fetch(`/api/v1/scheduled-tasks/config?_=${Date.now()}`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`/api/v1/scheduled-tasks?limit=500&_=${Date.now()}`, { headers: authHeaders(), cache: 'no-store' })
    ])
    if (configResponse.status === 401 || tasksResponse.status === 401) {
      clearAuthSession()
      await router.replace('/login')
      return
    }
    const configPayload = await parseApiJson(configResponse)
    const tasksPayload = await parseApiJson(tasksResponse)
    scheduledConfig.value = {
      enabled: Boolean(configPayload.data?.enabled),
      poll_seconds: Number(configPayload.data?.poll_seconds || 30),
      timezone: configPayload.data?.timezone || 'Asia/Shanghai'
    }
    scheduledTasks.value = tasksPayload.data || []
  } catch (error: any) {
    scheduleStatus.value = error.message || '定时任务加载失败'
  } finally {
    scheduleLoading.value = false
  }
}

const toggleSchedulePanel = async () => {
  showSchedulePanel.value = !showSchedulePanel.value
  showScenarioPanel.value = false
  showModelPanel.value = false
  if (showSchedulePanel.value) await loadScheduledTasks()
}

const setScheduledTasksEnabled = async (enabled: boolean) => {
  scheduleSaving.value = true
  scheduleStatus.value = ''
  try {
    const response = await fetch('/api/v1/scheduled-tasks/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled })
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    const payload = await parseApiJson(response)
    scheduledConfig.value = {
      enabled: Boolean(payload.data?.enabled),
      poll_seconds: Number(payload.data?.poll_seconds || scheduledConfig.value.poll_seconds),
      timezone: payload.data?.timezone || scheduledConfig.value.timezone
    }
    scheduleStatus.value = enabled ? '定时任务已开启' : '定时任务已关闭'
  } catch (error: any) {
    scheduleStatus.value = error.message || '配置保存失败'
  } finally {
    scheduleSaving.value = false
  }
}

const saveScheduledTaskConfig = async () => {
  scheduleSaving.value = true
  scheduleStatus.value = ''
  try {
    const response = await fetch('/api/v1/scheduled-tasks/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        enabled: scheduledConfig.value.enabled,
        poll_seconds: scheduledConfig.value.poll_seconds
      })
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    const payload = await parseApiJson(response)
    scheduledConfig.value = {
      enabled: Boolean(payload.data?.enabled),
      poll_seconds: Number(payload.data?.poll_seconds || 30),
      timezone: payload.data?.timezone || 'Asia/Shanghai'
    }
    scheduleStatus.value = '定时任务配置已保存'
  } catch (error: any) {
    scheduleStatus.value = error.message || '配置保存失败'
  } finally {
    scheduleSaving.value = false
  }
}

const updateScheduledTaskStatus = async (task: ScheduledTaskItem, action: 'pause' | 'resume', confirmUnknown = false) => {
  scheduleStatus.value = ''
  try {
    const response = await fetch(`/api/v1/scheduled-tasks/${task.id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      ...(confirmUnknown ? { body: JSON.stringify({ confirm_unknown: true }) } : {})
    })
    if (!response.ok) throw new Error(await apiErrorMessage(response, `HTTP error! status: ${response.status}`))
    await loadScheduledTasks()
  } catch (error: any) {
    scheduleStatus.value = error.message || '任务状态更新失败'
  }
}

const toggleScheduledTask = async (task: ScheduledTaskItem) => {
  if (task.status === 'active') {
    await updateScheduledTaskStatus(task, 'pause')
  } else if (task.status === 'paused') {
    if (scheduledTaskNeedsVerification(task)) {
      const confirmed = window.confirm(
        `这次定时任务的外部操作结果待核实，可能已经执行成功。\n\n` +
        `请先在飞书中核对实际结果；只有确认“需要重新执行”后，才会恢复任务。\n\n` +
        `${task.task_message}\n\n确认已核实并重新执行？`
      )
      if (!confirmed) return
      await updateScheduledTaskStatus(task, 'resume', true)
      return
    }
    await updateScheduledTaskStatus(task, 'resume')
  }
}

const deleteScheduledTask = async (task: ScheduledTaskItem) => {
  scheduleStatus.value = ''
  if (!canDeleteScheduledTask(task)) {
    scheduleStatus.value = '请先关闭任务，再删除。'
    return
  }
  const confirmed = window.confirm(`确认删除这个定时任务吗？\n\n${task.task_message}\n\n删除后不可恢复。`)
  if (!confirmed) return
  try {
    const response = await fetch(`/api/v1/scheduled-tasks/${task.id}`, {
      method: 'DELETE',
      headers: authHeaders()
    })
    if (response.status === 409) {
      scheduleStatus.value = await apiErrorMessage(response, '请先关闭任务，再删除。')
      return
    }
    if (!response.ok) throw new Error(await apiErrorMessage(response, `HTTP error! status: ${response.status}`))
    scheduleStatus.value = '定时任务已删除'
    await loadScheduledTasks()
  } catch (error: any) {
    scheduleStatus.value = error.message || '任务删除失败'
  }
}

const scenarioError = ref('')

const selectScenarioTemplate = (template: ScenarioTemplate) => {
  scenarioError.value = ''
  selectedScenario.value = template
  scenarioValues.value = {}
  scenarioAiContentGeneration.value = Boolean(template.requires_ai_content_generation)
  for (const field of template.fields || []) {
    scenarioValues.value[field.key] = ''
  }
}

const applyScenarioTemplate = async () => {
  if (!selectedScenario.value) return
  scenarioError.value = ''
  if (selectedScenario.value.enterprise_ready === false) {
    scenarioError.value = selectedScenario.value.enterprise_reason || '该模板暂不可在企业模式执行。'
    return
  }
  try {
    const response = await fetch('/api/v1/scenarios/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        template_id: selectedScenario.value.id,
        values: scenarioValues.value,
        enable_ai_content_generation: scenarioAiContentGeneration.value
      })
    })
    const payload = await parseApiJson(response)
    if (!response.ok || payload.code !== 0) {
      throw new Error(typeof payload.detail === 'string' ? payload.detail : '模板填入失败，请稍后重试。')
    }
    const missingFields = payload.data?.missing_fields || []
    if (missingFields.length) {
      scenarioError.value = `请先补充：${missingFields.map((field: { label?: string; key: string }) => field.label || field.key).join('、')}`
      return
    }
    inputText.value = payload.data?.message || ''
    showScenarioPanel.value = false
    await nextTick()
    adjustTextareaHeight()
  } catch (error: unknown) {
    scenarioError.value = error instanceof Error ? error.message : '模板填入失败，请稍后重试。'
  }
}

const clearPlanPreview = () => {
  planPreview.value = null
  planError.value = ''
  planElapsed.value = 0
  pendingPlanMessage.value = ''
}

const editPendingPlan = async () => {
  const message = pendingPlanMessage.value
  clearPlanPreview()
  inputText.value = message
  await nextTick()
  adjustTextareaHeight()
  textareaRef.value?.focus()
}

const previewPlan = async (message: string, skill = currentSkill.value) => {
  const version = viewVersion
  planController?.abort()
  const controller = new AbortController()
  planController = controller
  planLoading.value = true
  planElapsed.value = 0
  const started = Date.now()
  const timer = window.setInterval(() => {
    if (version === viewVersion) planElapsed.value = Math.floor((Date.now() - started) / 1000)
  }, 1000)
  let timedOut = false
  const deadline = window.setTimeout(() => { timedOut = true; controller.abort() }, PLAN_REQUEST_TIMEOUT_MS)
  planError.value = ''
  planPreview.value = null
  try {
    const response = await fetch('/api/v1/chat/plan', {
      signal: controller.signal,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        user_id: currentUserId.value,
        message,
        session_id: sessionId.value,
        skill: skill === 'auto' ? undefined : skill
      })
    })
    if (response.status === 401) {
      clearAuthSession()
      await router.replace('/login')
      return
    }
    if (!response.ok) throw new Error(await apiErrorMessage(response, '生成计划失败，请重试。'))
    const payload = await parseApiJson(response)
    if (version !== viewVersion || controller.signal.aborted) return
    if (payload.data?.session_id) {
      sessionId.value = payload.data.session_id
      syncSessionUrl(sessionId.value)
    }
    planPreview.value = payload.data?.plan || null
  } catch (error: any) {
    if (version !== viewVersion || (controller.signal.aborted && !timedOut)) return
    planError.value = timedOut
      ? '计划服务等待超过 30 秒，已安全停止。你可以重试或修改指令。'
      : error.message || '计划生成失败，请重试或修改指令。'
  } finally {
    window.clearInterval(timer)
    window.clearTimeout(deadline)
    if (version === viewVersion && planController === controller) {
      planController = null
      planLoading.value = false
    }
  }
}

const getLarkTrace = (msg: Message) => {
  const trace = msg.metadata?.execution_trace
  const progress = msg.metadata?.lark_progress
  const entries = Array.isArray(trace) && trace.length ? trace : Array.isArray(progress) ? progress : []
  const concise = entries.filter((item: unknown): item is string => typeof item === 'string')
    .map((item: string) => {
      // Old sessions can contain complete commands and raw tool responses.
      if (/准备执行命令|命令执行|返回摘要/.test(item)) {
        return item.includes('失败') ? '操作未完成，请查看最终回复。' : item.includes('成功') ? '操作已完成。' : '正在执行并核验操作…'
      }
      const text = item.trim().replace(/\s+/g, ' ')
      return text.length > 120 ? `${text.slice(0, 120)}…` : text
    })
    .filter((item: string, index: number, all: string[]) => item && (index === 0 || item !== all[index - 1]))
  if (concise.length) return concise
  const commands = msg.metadata?.executed_commands
  if (!Array.isArray(commands)) return []
  return commands.map((item: { success?: boolean; status?: string }, index: number) =>
    `操作 ${index + 1}：${item.success ? '已完成' : item.status === 'unknown' ? '结果待核实，请勿重复执行' : '未完成，请查看回复'}`
  )
}

const getLarkProgressSummary = (msg: Message) => {
  const trace = getLarkTrace(msg)
  if (!trace.length) return ''
  if (msg.content) return '执行详情'
  const last = trace[trace.length - 1]
  if (last.includes('规划')) return '处理中：正在规划'
  if (last.includes('修复')) return '处理中：正在修复命令'
  return '处理中'
}

const extractMissingScopes = (text?: string) => {
  const matches = String(text || '').match(/\b[a-z][a-z0-9_]*:[A-Za-z0-9_.:-]+\b/g) || []
  return Array.from(new Set(matches.filter((scope) => scope.includes(':'))))
}

const buildScopeSetupMetadataFromMessage = (message?: string) => {
  const scopes = extractMissingScopes(message)
  if (!scopes.length || !/缺少|权限|scope|permission|forbidden|unauthorized/i.test(String(message || ''))) return null
  return {
    setup_required: true,
    setup_scopes: scopes,
    setup_steps: [
      {
        key: 'auth_login',
        title: '补充授权',
        command: `lark-cli auth login --scope "${scopes.join(' ')}"`
      }
    ],
    setup_guide: ''
  }
}

const applyLarkSetupMetadata = (metadata?: Record<string, any>, fallbackMessage = '') => {
  const setupMetadata = metadata?.setup_required ? metadata : buildScopeSetupMetadataFromMessage(fallbackMessage)
  if (!setupMetadata?.setup_required) return
  showLarkSetup.value = true
  larkSetupForceAuth.value = false
  larkSetupScopes.value = Array.isArray(setupMetadata.setup_scopes) ? setupMetadata.setup_scopes : []
  larkSetupMessage.value = larkSetupScopes.value.length
    ? '当前任务需要补充飞书权限，授权成功后将继续处理。'
    : '当前账号需要连接飞书。授权和后续命令都会绑定当前登录账号。'
  if (Array.isArray(setupMetadata.setup_steps)) {
    larkSetupSteps.value = setupMetadata.setup_steps.map((step: any) => normalizeSetupStep({ ...step, status: 'pending' }))
  }
  void scrollToBottom(true)
}

const appendLarkProgress = (msgIndex: number, content: string) => {
  const text = normalizeDisplayText(content || '').trim()
  if (!text) return
  const current = messages.value[msgIndex].metadata || {}
  const progress = Array.isArray(current.lark_progress) ? [...current.lark_progress] : []
  progress.push(text)
  messages.value[msgIndex].metadata = { ...current, lark_progress: progress.slice(-80) }
}

const applyStreamMetadata = (msgIndex: number, payload: any) => {
  const data = payload.metadata || payload.data || {}
  const current = messages.value[msgIndex].metadata || {}
  messages.value[msgIndex].metadata = { ...current, ...data }
  if (data.resume_id) pendingResume.value = { id: data.resume_id, query: data.resume_query, session: sessionId.value }
  if (Object.prototype.hasOwnProperty.call(data, 'pending_tool')) setPendingWriteTool(data.pending_tool)
  applyLarkSetupMetadata(messages.value[msgIndex].metadata)
  if (messages.value[msgIndex].metadata?.ai_ppt) {
    loadAiPptPreview(msgIndex)
  }
}

const getAiPpt = (msg: Message) => msg.metadata?.ai_ppt || msg.metadata?.last_ai_ppt || null

const getAiPptPreviewSlides = (msg: Message) => {
  const slides = msg.metadata?.ai_ppt_preview?.slides
  if (!Array.isArray(slides)) return []
  return aiPptViewAll.value[msg.id] ? slides : slides.slice(0, 4)
}

const getAiPptSlideCount = (msg: Message) => {
  const slides = msg.metadata?.ai_ppt_preview?.slides
  return Array.isArray(slides) ? slides.length : 0
}

const getAiPptAction = (msg: Message) => {
  if (!aiPptActions.value[msg.id]) {
    aiPptActions.value[msg.id] = {
      action: '',
      target: '',
      folderToken: '',
      wikiToken: '',
      message: '',
      loading: false,
      result: '',
      error: ''
    }
  }
  return aiPptActions.value[msg.id]
}

const setAiPptAction = (msg: Message, action: 'upload' | 'send_group' | 'send_person') => {
  const current = getAiPptAction(msg)
  aiPptActions.value[msg.id] = { ...current, action, result: '', error: '' }
}

const executeAiPptAction = async (msg: Message) => {
  const ppt = getAiPpt(msg)
  const actionState = getAiPptAction(msg)
  if (!ppt?.filename || !actionState.action || actionState.loading) return
  actionState.loading = true
  actionState.result = ''
  actionState.error = ''
  try {
    const response = await fetch('/api/v1/ai-ppt/actions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        filename: ppt.filename,
        action: actionState.action,
        target: actionState.target,
        folder_token: actionState.folderToken,
        wiki_token: actionState.wikiToken,
        message: actionState.message,
        session_id: sessionId.value
      })
    })
    if (response.status === 401) {
      clearAuthSession()
      await router.replace('/login')
      return
    }
    const payload = await parseApiJson(response)
    const actionMetadata = payload.data?.metadata || payload.metadata
    const actionMessage = payload.data?.message || payload.message || payload.detail || ''
    applyLarkSetupMetadata(actionMetadata, actionMessage)
    if (!response.ok || payload.code !== 0) throw new Error(payload.detail || payload.message || '飞书操作失败')
    actionState.result = payload.data?.message || '飞书操作已完成'
  } catch (error: any) {
    applyLarkSetupMetadata(undefined, error.message)
    actionState.error = error.message || '飞书操作失败'
  } finally {
    actionState.loading = false
  }
}

const loadAiPptPreview = async (msgIndex: number) => {
  const version = viewVersion
  const msg = messages.value[msgIndex]
  const ppt = getAiPpt(msg)
  const existingSlides = msg.metadata?.ai_ppt_preview?.slides
  const hasLoadedImages = Array.isArray(existingSlides) && existingSlides.length > 0 && existingSlides.every(
    (slide: any) => typeof slide?.image_src === 'string' && slide.image_src.length > 0
  )
  if (!ppt?.preview_url || msg.metadata?.ai_ppt_preview_loading || hasLoadedImages) return
  messages.value[msgIndex].metadata = { ...(msg.metadata || {}), ai_ppt_preview_loading: true }
  const loadedUrls: string[] = []
  try {
    const response = await fetch(safeAiPptResourceUrl(ppt.preview_url), { headers: authHeaders(), cache: 'no-store' })
    if (response.status === 401) {
      clearAuthSession()
      currentAccount.value = null
      await router.replace('/login')
      return
    }
    if (!response.ok) throw new Error(`预览生成失败（HTTP ${response.status}）`)
    const payload = await parseApiJson(response)
    const preview = payload.data || {}
    const slides = Array.isArray(preview.slides) ? await Promise.all(preview.slides.map(async (slide: any) => {
      if (!slide?.image_url || typeof slide.image_url !== 'string') return { ...slide, image_src: '' }
      try {
        const blob = await fetchAiPptBlob(slide.image_url)
        const imageSrc = URL.createObjectURL(blob)
        aiPptObjectUrls.add(imageSrc)
        loadedUrls.push(imageSrc)
        return { ...slide, image_src: imageSrc }
      } catch (error) {
        if (error instanceof AiPptResourceError && error.status === 401) {
          clearAuthSession()
          currentAccount.value = null
          await router.replace('/login')
        }
        return { ...slide, image_src: '' }
      }
    })) : []
    if (version !== viewVersion || !messages.value[msgIndex]) {
      for (const url of loadedUrls) {
        aiPptObjectUrls.delete(url)
        URL.revokeObjectURL(url)
      }
      return
    }
    const current = messages.value[msgIndex].metadata || {}
    messages.value[msgIndex].metadata = {
      ...current,
      ai_ppt_preview: { ...preview, slides },
      ai_ppt_preview_loading: false
    }
  } catch (_error) {
    if (version !== viewVersion || !messages.value[msgIndex]) return
    const current = messages.value[msgIndex].metadata || {}
    messages.value[msgIndex].metadata = { ...current, ai_ppt_preview_loading: false }
  }
}

const downloadAiPpt = async (msg: Message) => {
  const ppt = getAiPpt(msg)
  if (!ppt?.download_url) return
  try {
    const blob = await fetchAiPptBlob(ppt.download_url)
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = typeof ppt.filename === 'string' && ppt.filename ? ppt.filename : 'presentation.pptx'
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  } catch (error) {
    if (error instanceof AiPptResourceError && error.status === 401) {
      clearAuthSession()
      currentAccount.value = null
      await router.replace('/login')
      return
    }
    msg.metadata = {
      ...(msg.metadata || {}),
      ai_ppt_download_error: error instanceof Error ? error.message : 'PPT 下载失败，请稍后重试'
    }
  }
}

const loadHistory = async () => {
  try {
    const response = await fetch('/api/v1/sessions?limit=50', { headers: authHeaders() })
    if (response.status === 401) {
      await router.replace('/login')
      return
    }
    const data = await parseApiJson(response)
    historyList.value = (data.data || []).map((item: any) => ({
      id: item.session_id,
      title: item.title || '新会话',
      time: formatTime(item.updated_at)
    }))
  } catch (error) {
    console.error('加载会话列表失败:', error)
  }
}

const loadChat = async (id: string, silent = false) => {
  drafts.set(sessionId.value, inputText.value || pendingPlanMessage.value)
  leaveConversation()
  const version = viewVersion
  if (version !== viewVersion) return
  sessionId.value = id
  inputText.value = drafts.get(id) || ''
  pendingResume.value = null
  showLarkSetup.value = false
  if (!silent) messages.value = []
  const running = runningChats.get(id)
  if (running) {
    messages.value = running.messages
    requestController = running.controller
    activeRunId = running.runId
    isLoading.value = true
    syncSessionUrl(id, silent)
    showSidebar.value = false
    return
  }
  try {
    const response = await fetch(`/api/v1/sessions/${encodeURIComponent(id)}/messages`, { headers: authHeaders() })
    if (!response.ok) return
    const data = await parseApiJson(response)
    if (version !== viewVersion) return
    const list = data.data?.messages || data.data || []
    messages.value = list.map((m: any) => ({
      id: Date.now() + Math.random(),
      role: m.role === 'assistant' ? 'assistant' : 'user',
      content: m.content || '',
      metadata: m.metadata || {}
    }))
    messages.value.forEach((msg, index) => {
      if (getAiPpt(msg)) loadAiPptPreview(index)
    })
    const latest = messages.value[messages.value.length - 1]
    if (latest?.metadata?.resume_id) {
      pendingResume.value = { id: latest.metadata.resume_id, query: latest.metadata.resume_query, session: id }
      applyLarkSetupMetadata(latest.metadata)
      if (latest.metadata.approval_required) {
        pendingWriteMessage.value = latest.metadata.resume_query
        pendingWriteSkill.value = 'lark_cli'
        setPendingWriteTool(latest.metadata.pending_tool)
        showWriteConfirm.value = true
      }
    } else {
      setPendingWriteTool(null)
    }
    syncSessionUrl(id, silent)
    shouldStickToBottom.value = true
    await scrollToBottom(true)
  } catch (error) {
    console.error('加载历史消息失败:', error)
  }
  if (!silent) showSidebar.value = false
}

const deleteHistory = async (event: Event, id: string) => {
  event.stopPropagation()
  if (runningChats.has(id)) {
    await loadChat(id)
    return
  }
  await fetch(`/api/v1/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders()
  })
  if (sessionId.value === id) newChat()
  await loadHistory()
}

const newChat = async () => {
  drafts.set(sessionId.value, inputText.value || pendingPlanMessage.value)
  leaveConversation()
  const version = viewVersion
  if (version !== viewVersion) return
  sessionId.value = ''
  inputText.value = drafts.get('') || ''
  showLarkSetup.value = false
  pendingResume.value = null
  messages.value = []
  shouldStickToBottom.value = true
  syncSessionUrl('', false)
  showSidebar.value = false
}

const runChatRequest = async (message: string, assistantMsgIndex: number, confirmWrite = false, skill = currentSkill.value, confirmPlan = false, resumeId = '') => {
  const controller = new AbortController()
  requestController = controller
  activeRunId = crypto.randomUUID()
  if (!sessionId.value) sessionId.value = crypto.randomUUID()
  const ownerSession = sessionId.value
  const ownerMessages = messages.value
  const assistant = ownerMessages[assistantMsgIndex]
  const runId = activeRunId
  runningChats.set(ownerSession, { controller, runId, messages: ownerMessages })
  const visible = () => sessionId.value === ownerSession && runningChats.get(ownerSession)?.runId === runId
  try {
  const response = await fetch('/api/v1/chat', {
    signal: controller.signal,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      user_id: currentUserId.value,
      message,
      session_id: ownerSession,
      stream: true,
      confirm_write: confirmWrite,
      confirm_plan: confirmPlan,
      run_id: runId,
      resume_id: resumeId,
      skill: skill === 'auto' ? undefined : skill
    })
  })
  if (response.status === 401) {
    clearAuthSession()
    await router.replace('/login')
    return
  }
  if (!response.ok || !response.body) throw new Error(await apiErrorMessage(response, `HTTP error! status: ${response.status}`))

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let firstContentReceived = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''

    for (const block of blocks) {
      const line = block.split('\n').find((item) => item.startsWith('data:'))
      if (!line) continue
      const data = JSON.parse(line.slice(5).trim())
      if (data.type === 'session') {
        if (visible()) syncSessionUrl(ownerSession)
        void loadHistory()
      } else if (data.type === 'progress') {
        assistant.loading = false
        const metadata = assistant.metadata || {}
        assistant.metadata = { ...metadata, lark_progress: [...(metadata.lark_progress || []), data.content || ''].slice(-80) }
      } else if (data.type === 'content') {
        if (!firstContentReceived) {
          assistant.loading = false
          firstContentReceived = true
        }
        assistant.content += data.content || ''
      } else if (data.type === 'metadata') {
        assistant.metadata = { ...assistant.metadata, ...data.metadata }
        if (visible()) applyStreamMetadata(assistantMsgIndex, data)
      } else if (data.type === 'error') {
        assistant.content += data.content || '请求失败'
      } else if (data.type === 'done') {
        if (visible()) syncSessionUrl(ownerSession)
      }
    }
    if (visible()) await scrollToBottom()
  }

  if (!assistant.content) {
    assistant.content = '抱歉，没有收到回复。'
  }
  } catch (error: any) {
    if (!controller.signal.aborted) assistant.content = error.message || '请求失败'
  } finally {
    assistant.loading = false
    if (visible()) {
      isLoading.value = false
      activeRunId = ''
      requestController = null
      if (isWriteConfirmationMessage(assistant.content)) {
        pendingWriteMessage.value = message
        pendingWriteSkill.value = skill
        showWriteConfirm.value = true
      }
    }
    if (runningChats.get(ownerSession)?.runId === runId) runningChats.delete(ownerSession)
    void loadHistory()
  }
}

const isWriteConfirmationMessage = (content: string) => {
  return content.includes('confirm_write=true') || content.includes('确认执行')
}

const sendMessage = async () => {
  let text = inputText.value.trim()
  if (!text) return
  if (pendingResume.value && /^(继续|继续任务|重试)$/.test(text) && !isBusy.value) {
    inputText.value = ''
    await resumeAfterAuthorization()
    return
  }
  if (planPreview.value?.requires_input) text = `${pendingPlanMessage.value}\n补充信息：${text}`
  if (isBusy.value) {
    const stopping = stopActiveRequest()
    const version = viewVersion
    await stopping
    if (version !== viewVersion) return
  }
  viewVersion += 1
  setupController?.abort()
  setupController = null
  larkSetupRunning.value = false
  pendingResume.value = null
  showLarkSetup.value = false
  showWriteConfirm.value = false
  pendingWriteMessage.value = ''
  pendingWriteTool.value = null
  clearPlanPreview()
  shouldStickToBottom.value = true
  inputText.value = ''
  if (textareaRef.value) textareaRef.value.style.height = 'auto'
  pendingPlanMessage.value = text
  pendingPlanSkill.value = currentSkill.value
  await previewPlan(text, currentSkill.value)
  if (planPreview.value && !planPreview.value.requires_input && !planPreview.value.need_confirmation) {
    await executePlannedMessage()
  }
  await scrollToBottom()
}

const openLarkReauth = () => {
  showLarkSetup.value = true
  larkSetupForceAuth.value = true
  larkSetupScopes.value = []
  larkSetupAuthUrl.value = ''
  larkSetupUserCode.value = ''
  larkSetupExpiresIn.value = ''
  larkSetupTerminal.value = ''
  larkSetupShowLog.value = false
  larkSetupMessage.value = '将为当前登录账号重新生成飞书授权链接，完成后后续命令会使用新的授权状态。'
  larkSetupSteps.value = [
    {
      key: 'clear_auth',
      title: setupStepCopy.clear_auth.title,
      description: setupStepCopy.clear_auth.description,
      command: 'lark-cli auth logout',
      status: 'pending'
    },
    {
      key: 'auth_login',
      title: setupStepCopy.auth_login.title,
      description: setupStepCopy.auth_login.description,
      command: 'lark-cli auth login --recommend --no-wait --json',
      status: 'pending'
    }
  ]
}

const executePlannedMessage = async () => {
  const text = pendingPlanMessage.value.trim()
  if (!text || isLoading.value) return
  const skill = pendingPlanSkill.value
  const confirmWrite = Boolean(planPreview.value?.need_confirmation)
  const version = viewVersion
  clearPlanPreview()

  messages.value.push({ id: Date.now(), role: 'user', content: text })
  const assistantMsgIndex = messages.value.length
  messages.value.push({ id: Date.now() + 1, role: 'assistant', content: '', loading: true, metadata: {} })
  isLoading.value = true
  await scrollToBottom()

  try {
    await runChatRequest(text, assistantMsgIndex, confirmWrite, skill, true)
    if (version !== viewVersion) return
    if (isWriteConfirmationMessage(messages.value[assistantMsgIndex].content)) {
      pendingWriteMessage.value = text
      pendingWriteSkill.value = skill
      showWriteConfirm.value = true
    }
    await loadScheduledTasks()
    await loadHistory()
  } catch (error: any) {
    if (version !== viewVersion) return
    messages.value[assistantMsgIndex].content = error.message || '网络错误，请重试'
  } finally {
    if (version === viewVersion) {
      messages.value[assistantMsgIndex].loading = false
      isLoading.value = false
      activeRunId = ''
      requestController = null
      await scrollToBottom()
    }
  }
}

const confirmPendingWrite = async () => {
  if (!pendingWriteMessage.value || isLoading.value) return
  showWriteConfirm.value = false
  pendingWriteTool.value = null
  const assistantMsgIndex = messages.value.length
  messages.value.push({ id: Date.now(), role: 'assistant', content: '', loading: true, metadata: {} })
  isLoading.value = true
  const version = viewVersion
  try {
    const resumeId = pendingResume.value?.session === sessionId.value ? pendingResume.value.id : ''
    pendingResume.value = null
    await runChatRequest(pendingWriteMessage.value, assistantMsgIndex, true, pendingWriteSkill.value, false, resumeId)
    await loadHistory()
  } catch (error: any) {
    if (version === viewVersion) messages.value[assistantMsgIndex].content = error.message || '请求失败'
  } finally {
    if (version === viewVersion) {
      messages.value[assistantMsgIndex].loading = false
      isLoading.value = false
      activeRunId = ''
      requestController = null
    }
  }
}

const resumeAfterAuthorization = async () => {
  const pending = pendingResume.value
  if (!pending || pending.session !== sessionId.value || isBusy.value) return
  pendingResume.value = null
  showLarkSetup.value = false
  const version = viewVersion
  const index = messages.value.length
  messages.value.push({ id: Date.now(), role: 'assistant', content: '', loading: true, metadata: {} })
  isLoading.value = true
  try {
    await runChatRequest(pending.query, index, false, 'lark_cli', false, pending.id)
    if (version === viewVersion && isWriteConfirmationMessage(messages.value[index].content)) {
      pendingWriteMessage.value = pending.query
      pendingWriteSkill.value = 'lark_cli'
      showWriteConfirm.value = true
    }
    await loadHistory()
  } catch (error: any) {
    if (version === viewVersion) messages.value[index].content = error.message || '继续任务失败'
  } finally {
    if (version === viewVersion) {
      messages.value[index].loading = false
      isLoading.value = false
      activeRunId = ''
      requestController = null
    }
  }
}

const quickQuestion = (question: string) => {
  inputText.value = question
  sendMessage()
}

const adjustTextareaHeight = () => {
  if (!textareaRef.value) return
  textareaRef.value.style.height = 'auto'
  textareaRef.value.style.height = Math.min(textareaRef.value.scrollHeight, 120) + 'px'
}

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    sendMessage()
  }
}

const toggleSidebar = () => {
  showSidebar.value = !showSidebar.value
}

const selectSkill = (skillId: string) => {
  currentSkill.value = skillId
  showModelPanel.value = false
  showScenarioPanel.value = false
  showSchedulePanel.value = false
}

const appendLarkSetupTerminal = (chunk: string) => {
  larkSetupTerminal.value += chunk
  larkSetupTerminal.value = larkSetupTerminal.value.slice(-20000)
}

const updateLarkSetupStep = (stepKey: string, status: SetupStep['status']) => {
  larkSetupSteps.value = larkSetupSteps.value.map((step) => (step.key === stepKey ? { ...step, status } : step))
}

const startLarkSetup = async () => {
  if (larkSetupRunning.value) return
  if (enterpriseAuthEnabled.value) {
    const version = viewVersion
    larkSetupRunning.value = true
    larkSetupMessage.value = '等待完成企业飞书授权'
    const pending = pendingResume.value
    if (pending) {
      try {
        sessionStorage.setItem(FEISHU_OAUTH_CONTINUATION_KEY, JSON.stringify({
          session: pending.session,
          resume: true
        }))
      } catch (error) {
        console.warn('无法保存授权后的任务续接信息:', error)
      }
    }
    try {
      await authorizeFeishu(larkSetupScopes.value)
      try { sessionStorage.removeItem(FEISHU_OAUTH_CONTINUATION_KEY) } catch (_error) { /* best effort cleanup */ }
      if (version === viewVersion) {
        showLarkSetup.value = false
        larkSetupForceAuth.value = false
        await resumeAfterAuthorization()
      }
    } catch (error: unknown) {
      try { sessionStorage.removeItem(FEISHU_OAUTH_CONTINUATION_KEY) } catch (_error) { /* best effort cleanup */ }
      if (version === viewVersion) larkSetupMessage.value = error instanceof Error ? error.message : '授权未完成'
    } finally {
      larkSetupRunning.value = false
    }
    return
  }
  larkSetupRunning.value = true
  const version = viewVersion
  const controller = new AbortController()
  setupController = controller
  let authorized = false
  larkSetupTerminal.value = ''
  larkSetupShowLog.value = false
  larkSetupAuthUrl.value = ''
  larkSetupUserCode.value = ''
  larkSetupExpiresIn.value = ''
  larkSetupMessage.value = '正在为当前账号准备飞书 CLI 初始化与授权。'
  larkSetupSteps.value = larkSetupSteps.value.map((step) => ({ ...step, status: 'pending' }))

  try {
    const response = await fetch('/api/v1/lark/setup/stream', {
      signal: controller.signal,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ user_id: currentUserId.value, scopes: larkSetupScopes.value, force_auth: larkSetupForceAuth.value })
    })
    if (!response.ok || !response.body) throw new Error(`HTTP error! status: ${response.status}`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (version !== viewVersion) { await reader.cancel(); return }
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        const line = block.split('\n').find((item) => item.startsWith('data:'))
        if (!line) continue
        const data = JSON.parse(line.slice(5).trim())
        if (data.type === 'status') {
          larkSetupSteps.value = (data.steps || []).map((step: any) => normalizeSetupStep({ ...step, status: 'pending' }))
        } else if (data.type === 'step_start') {
          updateLarkSetupStep(data.step?.key, 'running')
          appendLarkSetupTerminal(`\n> ${data.step?.display_command || ''}\n`)
        } else if (data.type === 'terminal') {
          appendLarkSetupTerminal(data.chunk || '')
        } else if (data.type === 'auth') {
          larkSetupAuthUrl.value = data.auth_url || larkSetupAuthUrl.value
          larkSetupUserCode.value = data.user_code ? String(data.user_code) : larkSetupUserCode.value
          larkSetupExpiresIn.value = data.expires_in ? String(data.expires_in) : larkSetupExpiresIn.value
        } else if (data.type === 'auth_wait') {
          larkSetupMessage.value = data.message || '正在等待当前账号完成飞书授权。'
        } else if (data.type === 'step_done') {
          updateLarkSetupStep(data.step_key, data.success ? 'success' : 'failed')
          if (!data.success) larkSetupShowLog.value = true
        } else if (data.type === 'done') {
          larkSetupMessage.value = data.message || '飞书授权流程已结束。'
          if (data.success) {
            authorized = true
            showLarkSetup.value = false
            larkSetupForceAuth.value = false
          }
        }
      }
    }
  } catch (error: any) {
    if (version !== viewVersion) return
    larkSetupShowLog.value = true
    larkSetupMessage.value = error.message || '飞书授权流程失败，请稍后重试。'
  } finally {
    if (setupController === controller) {
      setupController = null
      larkSetupRunning.value = false
    }
  }
  if (authorized && version === viewVersion) await resumeAfterAuthorization()
}

const refreshLarkSetup = async () => {
  const configResponse = await fetch('/api/v1/auth/feishu/config')
  if (configResponse.ok) enterpriseAuthEnabled.value = Boolean((await configResponse.json()).data?.enabled)
  const response = await fetch('/api/v1/lark/setup/status', { headers: authHeaders() })
  const payload = await parseApiJson(response)
  const data = payload.data || {}
  larkSetupForceAuth.value = false
  showLarkSetup.value = !data.ready
  larkSetupMessage.value = data.ready ? '当前账号的飞书 CLI 已就绪。' : '当前账号还需要连接飞书。'
  larkSetupSteps.value = (data.steps || []).map((step: any) => normalizeSetupStep({ ...step, status: 'pending' }))
}

const loadModelConfig = async () => {
  const response = await fetch('/api/v1/models/config', { headers: authHeaders() })
  const payload = await parseApiJson(response)
  modelPresets.value = payload.data?.presets || []
  currentModel.value = payload.data?.current || {}
  modelBaseUrl.value = currentModel.value.base_url || ''
  modelName.value = currentModel.value.model || ''
  modelPreset.value = modelPresets.value.find((preset) =>
    preset.base_url.replace(/\/$/, '') === modelBaseUrl.value.replace(/\/$/, '')
  )?.id || 'custom'
}

const applyPresetDefaults = () => {
  const preset = modelPresets.value.find((item) => item.id === modelPreset.value)
  if (!preset) return
  modelBaseUrl.value = preset.base_url
  modelName.value = preset.model
}

const saveModelConfig = async () => {
  modelSaving.value = true
  modelStatus.value = ''
  try {
    const response = await fetch('/api/v1/models/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        preset: modelPreset.value,
        api_key: modelApiKey.value,
        base_url: modelBaseUrl.value,
        model: modelName.value,
        provider: 'openai'
      })
    })
    const payload = await parseApiJson(response)
    if (!response.ok || payload.code !== 0) throw new Error('模型配置保存失败，请重试。')
    currentModel.value = payload.data?.current || {}
    modelStatus.value = '模型配置已保存'
    modelApiKey.value = ''
  } catch (error: unknown) {
    modelStatus.value = error instanceof Error ? error.message : '模型配置保存失败，请重试。'
  } finally {
    modelSaving.value = false
  }
}

const handleLogout = async () => {
  await fetch('/api/v1/auth/logout', { method: 'POST', headers: authHeaders() })
  clearAuthSession()
  currentAccount.value = null
  await router.replace('/login')
}

const closeFloatingPanels = (event: MouseEvent) => {
  const target = event.target as HTMLElement
  if (!target.closest('.skill-selector')) {
    showModelPanel.value = false
    showScenarioPanel.value = false
    showSchedulePanel.value = false
  }
}

const enterpriseAuthEnabled = ref(false)
const showAccountSettings = ref(false)

onMounted(async () => {
  document.addEventListener('click', closeFloatingPanels)
  await Promise.all([loadHistory(), refreshLarkSetup(), loadModelConfig(), loadScenarios(), loadScheduledTasks()])
  const urlParams = new URLSearchParams(window.location.search)
  const urlSessionId = urlParams.get('session')
  const resumeAfterOAuth = urlParams.get('resume') === '1'
  if (urlSessionId) await loadChat(urlSessionId, true)
  isInitializing.value = false
  if (resumeAfterOAuth && pendingResume.value) await resumeAfterAuthorization()
})

onUnmounted(() => {
  void stopActiveRequest()
  releaseAiPptObjectUrls()
  document.removeEventListener('click', closeFloatingPanels)
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
})
</script>

<template>
  <div class="app-container">
    <AccountSettings v-if="showAccountSettings" @close="showAccountSettings = false" />
    <div class="overlay" :class="{ show: showSidebar }" @click="toggleSidebar"></div>

    <aside class="sidebar" :class="{ show: showSidebar }">
      <div class="sidebar-top">
        <div class="sidebar-logo">
          <BrandMark />
          <span class="logo-text brand-wordmark">飞序 Flowing<small>AI WORKSPACE</small></span>
        </div>
        <button type="button" class="new-chat-btn" @click="newChat">
          <Plus :size="16" aria-hidden="true" />
          <span>新建对话</span>
        </button>
      </div>

      <div class="history-section">
        <div class="history-header">
          <span>聊天记录</span>
          <span v-if="historyList.length" class="history-count">{{ historyList.length }}</span>
        </div>
        <div class="history-list">
          <div v-if="historyList.length === 0" class="no-history">
            <p>暂无历史记录</p>
            <p class="no-history-tip">开始你的第一次飞书对话吧</p>
          </div>
          <div
            v-for="item in historyList"
            :key="item.id"
            class="history-item group"
            :class="{ active: item.id === sessionId }"
            role="button"
            tabindex="0"
            @click="loadChat(item.id)"
            @keydown.enter="loadChat(item.id)"
            @keydown.space.prevent="loadChat(item.id)"
          >
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <div class="history-copy">
              <div class="history-title">{{ item.title }}</div>
              <div class="history-time">{{ runningChats.has(item.id) ? '执行中' : item.time }}</div>
            </div>
            <button type="button" class="history-delete" @click="deleteHistory($event, item.id)" @keydown.stop title="删除对话" aria-label="删除对话">
              <Trash2 :size="14" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <div class="user-profile">
        <div class="user-avatar">{{ currentAccount?.name?.slice(0, 1) || '账' }}</div>
        <div class="user-info">
          <div class="user-name">{{ currentAccount?.name || '账号' }}</div>
          <div class="user-id">{{ currentAccount?.account || '' }}</div>
          <button type="button" class="logout-mini" @click="router.push('/templates')"><LayoutTemplate :size="14" /> 模板社区</button>
          <button type="button" class="logout-mini" @click="openLarkReauth"><RefreshCw :size="14" /> 重新授权</button>
          <button type="button" class="logout-mini" @click="showAccountSettings = true"><UserRound :size="14" /> 账号与权限</button>
          <button type="button" class="logout-mini" @click="handleLogout"><LogOut :size="14" /> 退出</button>
        </div>
      </div>
    </aside>

    <main class="main-content">
      <header class="document-header">
        <div class="document-breadcrumb">
          <button type="button" class="mobile-menu-btn" @click="toggleSidebar" aria-label="打开侧边栏" title="打开侧边栏">
            <PanelLeft :size="18" aria-hidden="true" />
          </button>
          <BrandMark :size="24" /><span>飞序 Flowing</span>
          <ChevronRight :size="14" aria-hidden="true" />
          <strong>{{ sessionId ? '当前对话' : '新建对话' }}</strong>
        </div>
        <div class="workspace-status"><span aria-hidden="true"></span> 账号已登录</div>
      </header>

      <div class="chat-container">
        <div ref="messagesContainer" class="chat-messages" @scroll.passive="handleMessagesScroll">
          <WelcomeView
            v-if="messages.length === 0 && !isInitializing"
            :current-skill="currentSkill"
            @quick-question="quickQuestion"
          />

          <div v-if="isInitializing" class="init-loading">
            <div class="typing-indicator"><span></span><span></span><span></span></div>
          </div>

          <template v-else>
            <div v-for="msg in messages" :key="msg.id" class="message group" :class="msg.role">
              <span class="block-handle message-handle" aria-hidden="true">⋮⋮</span>
              <div class="message-content">
                <template v-if="msg.loading">
                  <div class="typing-indicator"><span></span><span></span><span></span></div>
                </template>
                <template v-else>
                  <details v-if="getLarkTrace(msg).length" class="lark-progress">
                    <summary>
                      <span>{{ getLarkProgressSummary(msg) }}</span>
                      <span class="lark-progress-count">{{ getLarkTrace(msg).length }}</span>
                    </summary>
                    <div class="lark-progress-list">
                      <div v-for="(item, index) in getLarkTrace(msg)" :key="index" class="lark-progress-item">
                        <span class="lark-progress-index">{{ index + 1 }}</span>
                        <pre>{{ item }}</pre>
                      </div>
                    </div>
                  </details>
                  <div v-if="workflowMemoryFor(msg)" class="workflow-memory-card">
                    <Brain :size="16" aria-hidden="true" />
                    <div>
                      <strong>{{ workflowMemoryFor(msg)?.label }}</strong>
                      <span v-if="workflowMemoryFor(msg)?.status === 'active'">已记住 · 成功 {{ workflowMemoryFor(msg)?.success_count }} 次</span>
                      <span v-else>本次成功已形成候选经验，确认后可跨设备复用</span>
                      <small v-if="msg.metadata?.workflow_memory_error" role="alert">{{ msg.metadata.workflow_memory_error }}</small>
                    </div>
                    <button
                      v-if="workflowMemoryFor(msg)?.status === 'candidate'"
                      type="button"
                      :disabled="workflowMemoryBusy(msg)"
                      @click="activateWorkflowMemory(msg)"
                    >记住这个流程</button>
                    <button
                      v-else
                      type="button"
                      class="icon-button"
                      :disabled="workflowMemoryBusy(msg)"
                      aria-label="停用这条工作流记忆"
                      title="停用这条记忆"
                      @click="forgetWorkflowMemory(msg)"
                    ><Trash2 :size="15" /></button>
                  </div>
                  <div v-if="getAiPpt(msg)" class="ai-ppt-card">
                    <div class="ai-ppt-head">
                      <div>
                        <span class="ai-ppt-kicker">飞书演示文稿</span>
                        <strong>{{ getAiPpt(msg).title }}</strong>
                        <p>{{ getAiPpt(msg).style }} · v{{ String(getAiPpt(msg).version || 1).padStart(3, '0') }}</p>
                      </div>
                      <button type="button" class="ai-ppt-download" @click="downloadAiPpt(msg)"><Download :size="15" /> 下载 PPTX</button>
                    </div>
                    <div class="ai-ppt-actions">
                      <button type="button" @click="aiPptViewAll[msg.id] = !aiPptViewAll[msg.id]">
                        {{ aiPptViewAll[msg.id] ? '收起预览' : `完整预览${getAiPptSlideCount(msg) ? `（${getAiPptSlideCount(msg)}页）` : ''}` }}
                      </button>
                      <button type="button" @click="setAiPptAction(msg, 'upload')"><Upload :size="14" /> 上传云文档</button>
                      <button type="button" @click="setAiPptAction(msg, 'send_group')"><Users :size="14" /> 发到群</button>
                      <button type="button" @click="setAiPptAction(msg, 'send_person')"><UserRound :size="14" /> 发给同事</button>
                    </div>
                    <div v-if="getAiPptPreviewSlides(msg).length" class="ai-ppt-previews">
                      <button
                        v-for="slide in getAiPptPreviewSlides(msg)"
                        :key="slide.index"
                        type="button"
                        class="ai-ppt-preview"
                        :disabled="!slide.image_src"
                        @click="downloadAiPpt(msg)"
                      >
                        <img v-if="slide.image_src" :src="slide.image_src" :alt="`Slide ${slide.index}`" />
                        <span v-else>预览加载失败</span>
                        <span>{{ slide.index }}</span>
                      </button>
                    </div>
                    <div v-else class="ai-ppt-preview-loading">
                      {{ msg.metadata?.ai_ppt_preview_loading ? '正在生成预览...' : '预览生成后会显示在这里' }}
                    </div>
                    <div class="ai-ppt-slide-list">
                      <span v-for="slide in getAiPpt(msg).slides" :key="slide.index">{{ slide.index }}. {{ slide.title }}</span>
                    </div>
                    <div v-if="getAiPptAction(msg).action" class="ai-ppt-action-panel">
                      <template v-if="getAiPptAction(msg).action === 'upload'">
                        <strong>上传到飞书云文档</strong>
                        <label>
                          <span>文件夹 token（可选）</span>
                          <input v-model="getAiPptAction(msg).folderToken" placeholder="fldbc_xxx，不填则上传到云空间根目录" />
                        </label>
                        <label>
                          <span>Wiki 节点 token（可选）</span>
                          <input v-model="getAiPptAction(msg).wikiToken" placeholder="wikcn_xxx，和文件夹 token 二选一" />
                        </label>
                      </template>
                      <template v-else-if="getAiPptAction(msg).action === 'send_group'">
                        <strong>发送到飞书群</strong>
                        <label>
                          <span>群聊名称</span>
                          <input v-model="getAiPptAction(msg).target" placeholder="输入群聊名称，飞书 CLI 会搜索匹配" />
                        </label>
                        <label>
                          <span>附言（可选）</span>
                          <input v-model="getAiPptAction(msg).message" placeholder="随 PPT 一起发送的说明" />
                        </label>
                      </template>
                      <template v-else>
                        <strong>发送给飞书联系人</strong>
                        <label>
                          <span>联系人姓名</span>
                          <input v-model="getAiPptAction(msg).target" placeholder="输入同事姓名，飞书 CLI 会搜索联系人" />
                        </label>
                        <label>
                          <span>附言（可选）</span>
                          <input v-model="getAiPptAction(msg).message" placeholder="随 PPT 一起发送的说明" />
                        </label>
                      </template>
                      <div class="ai-ppt-action-row">
                        <button type="button" :disabled="getAiPptAction(msg).loading" @click="executeAiPptAction(msg)">
                          {{ getAiPptAction(msg).loading ? '执行中...' : '执行飞书操作' }}
                        </button>
                        <button type="button" class="secondary" :disabled="getAiPptAction(msg).loading" @click="getAiPptAction(msg).action = ''">取消</button>
                      </div>
                      <p v-if="getAiPptAction(msg).result" class="ai-ppt-action-result">{{ getAiPptAction(msg).result }}</p>
                      <p v-if="getAiPptAction(msg).error" class="ai-ppt-action-error">{{ getAiPptAction(msg).error }}</p>
                    </div>
                    <p v-if="msg.metadata?.ai_ppt_download_error" class="ai-ppt-action-error" role="alert">{{ msg.metadata.ai_ppt_download_error }}</p>
                    <p class="ai-ppt-tip">{{ getAiPpt(msg).feishu_tip }}</p>
                  </div>
                  <div v-html="formatContent(msg.content)"></div>
                </template>
              </div>
            </div>
          </template>

          <div v-if="showWriteConfirm" class="clarify-container">
            <div class="clarify-card write-confirm-card">
              <div class="clarify-header">{{ pendingWritePreview?.title || '确认飞书写操作' }}</div>
              <template v-if="pendingWritePreview">
                <p class="write-preview-summary">{{ pendingWritePreview.summary }}</p>
                <dl class="write-preview-fields">
                  <div v-for="field in pendingWritePreview.fields" :key="field.label" class="write-preview-field">
                    <dt>{{ field.label }}</dt>
                    <dd>{{ field.value }}</dd>
                  </div>
                </dl>
                <details class="write-preview-raw">
                  <summary>查看原始参数</summary>
                  <pre>{{ pendingWriteRaw(pendingWriteTool) }}</pre>
                </details>
              </template>
              <div v-else class="clarify-desc">{{ pendingWriteMessage }}</div>
              <div class="write-confirm-actions">
                <button type="button" class="write-confirm-btn" :disabled="isLoading" @click="confirmPendingWrite">确认执行</button>
              </div>
            </div>
          </div>

          <div v-if="planLoading || planPreview || planError" class="clarify-container">
            <div class="clarify-card plan-preview-card">
              <div class="clarify-header">执行计划预览</div>
              <div class="plan-request" aria-label="本次待处理指令">
                <span>本次指令</span>
                <p>{{ pendingPlanMessage }}</p>
              </div>
              <div v-if="planLoading" class="clarify-desc" role="status">
                {{ planProgressText }} · 已等待 {{ planElapsed }} 秒
              </div>
              <div v-else-if="planError" class="plan-error" role="alert">
                <p>{{ planError }}</p>
                <span>这条指令尚未执行，也没有发送任何飞书消息。</span>
              </div>
              <template v-else-if="planPreview">
                <div class="clarify-desc">{{ planPreview.summary || pendingPlanMessage }}</div>
                <div class="plan-meta">
                  <span v-if="planSourceLabel(planPreview.planning_source)">{{ planSourceLabel(planPreview.planning_source) }}</span>
                  <span v-if="typeof planPreview.planning_duration_ms === 'number'">{{ planPreview.planning_duration_ms }} ms</span>
                  <span v-for="skill in planPreview.relevant_skills" :key="skill">{{ skill }}</span>
                </div>
                <div v-if="planPreview.corrections?.length" class="plan-normalization">
                  已安全纠正操作词：{{ planPreview.corrections.join('、') }}；群名、人名和正文未修改。
                </div>
                <div v-if="planPreview.workflow_memory" class="plan-normalization">
                  正在复用 {{ planPreview.workflow_memory.label }}（已成功 {{ planPreview.workflow_memory.success_count }} 次）；动态 ID 和权限仍会重新检查。
                </div>
                <div v-if="planPreview.commands.length" class="plan-command-list">
                  <div v-for="(item, index) in planPreview.commands" :key="index" class="plan-command-item group">
                    <span class="block-handle" aria-hidden="true">⋮⋮</span>
                    <div class="plan-command-top">
                      <span>{{ index + 1 }}</span>
                      <strong>{{ item.write ? '写操作' : '读操作' }}</strong>
                    </div>
                    <code>{{ item.command }}</code>
                    <p>{{ item.reason }}</p>
                  </div>
                </div>
                <div v-if="planPreview.reason_for_confirmation" class="plan-warning">
                  {{ planPreview.reason_for_confirmation }}
                </div>
              </template>
              <div class="write-confirm-actions">
                <button v-if="planError" type="button" class="write-confirm-secondary" @click="previewPlan(pendingPlanMessage, pendingPlanSkill)">重试</button>
                <button v-if="planError" type="button" class="write-confirm-secondary" @click="editPendingPlan">修改指令</button>
                <button type="button" class="write-confirm-secondary" :disabled="isLoading || planLoading" @click="clearPlanPreview">
                  取消
                </button>
                <button v-if="!planPreview?.requires_input" type="button" class="write-confirm-btn" :disabled="isLoading || planLoading || !planPreview" @click="executePlannedMessage">
                  确认执行
                </button>
              </div>
            </div>
          </div>

          <div v-if="showLarkSetup" class="clarify-container">
            <div class="clarify-card lark-setup-card">
              <div class="clarify-header">{{ larkSetupTitle }}</div>
              <div class="lark-setup-message">{{ larkSetupMessage }}</div>
              <button v-if="pendingResume && !larkSetupRunning && !enterpriseAuthEnabled" type="button" class="write-confirm-secondary" :disabled="isBusy" @click="resumeAfterAuthorization">
                继续任务
              </button>
              <div v-if="!enterpriseAuthEnabled" class="lark-setup-hintbox">{{ larkSetupHint }}</div>
              <div v-if="!enterpriseAuthEnabled && larkSetupSteps.length" class="lark-setup-steps">
                <div v-for="(step, index) in larkSetupSteps" :key="step.key" class="lark-setup-step" :class="step.status">
                  <div class="lark-setup-step-title">
                    <span class="lark-setup-step-index">{{ index + 1 }}</span>
                    {{ step.title }}
                  </div>
                  <div v-if="step.description" class="lark-setup-step-desc">{{ step.description }}</div>
                  <div v-if="step.command" class="lark-setup-step-command">{{ step.command }}</div>
                </div>
              </div>
              <div class="write-confirm-actions">
                <button type="button" class="write-confirm-btn" :disabled="larkSetupRunning" @click="startLarkSetup">
                  {{ enterpriseAuthEnabled ? (larkSetupRunning ? '等待飞书授权' : pendingResume ? '授权并继续' : '连接飞书') : larkSetupActionText }}
                </button>
              </div>
              <div v-if="larkSetupAuthUrl" class="lark-setup-auth">
                <a class="lark-setup-auth-button" :href="larkSetupAuthUrl" target="_blank" rel="noopener noreferrer">打开授权链接</a>
                <div class="lark-setup-url">{{ larkSetupAuthUrl }}</div>
                <div v-if="larkSetupUserCode" class="lark-setup-code">授权码：<strong>{{ larkSetupUserCode }}</strong></div>
                <div v-if="larkSetupExpiresIn" class="lark-setup-hint">链接有效期：{{ larkSetupExpiresIn }} 秒</div>
              </div>
              <details v-if="larkSetupTerminal" class="lark-setup-log" :open="larkSetupShowLog">
                <summary>查看执行日志</summary>
                <pre class="lark-setup-pre">{{ larkSetupTerminal }}</pre>
              </details>
            </div>
          </div>
        </div>

        <div class="chat-input-container">
          <div class="input-box">
            <div class="skill-selector">
              <div class="skill-row">
                <button
                  type="button"
                  v-for="skill in skills"
                  :key="skill.id"
                  class="skill-btn"
                  :class="{ active: currentSkill === skill.id }"
                  :title="skill.description"
                  @click.stop="selectSkill(skill.id)"
                >
                  <CommandIcon :size="14" aria-hidden="true" />
                  <span>{{ skill.name }}</span>
                </button>
                <button type="button" class="skill-btn scenario-btn" @click.stop="showScenarioPanel = !showScenarioPanel; showSchedulePanel = false; showModelPanel = false"><Blocks :size="14" /> 场景模板</button>
                <button
                  type="button"
                  class="skill-btn schedule-btn"
                  :class="{ active: showSchedulePanel }"
                  @click.stop="toggleSchedulePanel"
                >
                  <CalendarClock :size="14" aria-hidden="true" />
                  定时任务
                  <span class="schedule-dot" :class="{ off: !scheduledConfig.enabled }"></span>
                </button>
                <button v-if="isAdmin" type="button" class="skill-btn model-btn" @click.stop="showModelPanel = !showModelPanel; showScenarioPanel = false; showSchedulePanel = false"><SlidersHorizontal :size="14" /> 模型配置</button>
              </div>

              <div v-if="showScenarioPanel" class="scenario-popover" @click.stop>
                <div v-if="!scenarioTemplates.length && scenarioError" role="alert">
                  <p class="danger">{{ scenarioError }}</p>
                  <button type="button" @click="loadScenarios">重新加载</button>
                </div>
                <p v-else-if="!scenarioTemplates.length" class="schedule-empty">暂无可用模板</p>
                <div class="scenario-list">
                  <button
                    type="button"
                    v-for="template in scenarioTemplates"
                    :key="template.id"
                    :class="{ active: selectedScenario?.id === template.id }"
                    @click="selectScenarioTemplate(template)"
                  >
                    <strong>{{ template.title }}</strong>
                    <span>{{ template.description }}</span>
                    <em v-if="template.requires_ai_content_generation">AI 生成内容</em>
                    <em v-if="template.enterprise_ready === false" class="enterprise-unavailable">企业未开放</em>
                  </button>
                </div>
                <div v-if="selectedScenario" class="scenario-form">
                  <p v-if="selectedScenario.enterprise_ready === false" class="danger" role="alert">
                    {{ selectedScenario.enterprise_reason }}
                  </p>
                  <label class="scenario-toggle">
                    <input v-model="scenarioAiContentGeneration" type="checkbox" :disabled="selectedScenario.enterprise_ready === false" />
                    <span>{{ selectedScenario.content_generation_label || '执行前 AI 扩写' }}</span>
                  </label>
                  <label v-for="field in selectedScenario.fields" :key="field.key">
                    <span>{{ field.label }}</span>
                    <input v-model="scenarioValues[field.key]" :placeholder="field.placeholder" :disabled="selectedScenario.enterprise_ready === false" />
                  </label>
                  <div class="model-actions">
                    <button type="button" :disabled="selectedScenario.enterprise_ready === false" @click="applyScenarioTemplate">填入输入框</button>
                  </div>
                  <p v-if="scenarioError" class="danger" role="alert">{{ scenarioError }}</p>
                </div>
              </div>

              <div v-if="showSchedulePanel" class="schedule-popover" @click.stop>
                <div class="schedule-panel-head">
                  <div>
                    <strong>定时任务</strong>
                    <p>当前账号：{{ currentAccount?.account || '-' }} · {{ scheduledConfig.enabled ? '全局调度已开启' : '全局调度已关闭' }} · 轮询间隔 {{ scheduledConfig.poll_seconds }} 秒</p>
                  </div>
                  <div class="schedule-head-actions">
                    <button type="button" class="schedule-mini-btn" :disabled="scheduleLoading" @click="loadScheduledTasks">刷新</button>
                    <button
                      v-if="isAdmin"
                      type="button"
                      class="schedule-switch"
                      :class="{ enabled: scheduledConfig.enabled }"
                      :disabled="scheduleSaving"
                      @click="setScheduledTasksEnabled(!scheduledConfig.enabled)"
                    >
                      {{ scheduledConfig.enabled ? '关闭' : '开启' }}
                    </button>
                  </div>
                </div>
                <div v-if="isAdmin" class="schedule-config-row">
                  <label>
                    <span>轮询间隔（秒）</span>
                    <input v-model.number="scheduledConfig.poll_seconds" type="number" min="5" max="3600" step="5" />
                  </label>
                  <button type="button" class="schedule-mini-btn" :disabled="scheduleSaving" @click="saveScheduledTaskConfig">保存配置</button>
                </div>
                <div v-if="scheduleStatus" class="schedule-status">{{ scheduleStatus }}</div>
                <div v-if="scheduleLoading" class="schedule-empty">正在加载定时任务...</div>
                <div v-else-if="!scheduledTasks.length" class="schedule-empty">当前账号暂无已添加的定时任务</div>
                <div v-else class="schedule-list">
                  <div v-for="task in scheduledTasks" :key="task.id" class="schedule-card group">
                    <span class="block-handle" aria-hidden="true">⋮⋮</span>
                    <div class="schedule-card-main">
                      <strong>{{ task.task_message }}</strong>
                      <p>{{ scheduleTypeText(task.schedule_type) }} · {{ task.time_of_day || '-' }} · {{ scheduleStatusText(task.status) }}</p>
                      <span>任务 ID：{{ task.id }}</span>
                      <span>下次执行：{{ formatDateTime(task.next_run_at) }}</span>
                      <span>上次执行：{{ formatDateTime(task.last_run_at) }} · 已执行 {{ task.run_count || 0 }} 次</span>
                      <span v-if="scheduledTaskResultText(task)" :class="{ 'schedule-result-unknown': scheduledTaskNeedsVerification(task) }">
                        最近结果：{{ scheduledTaskResultText(task) }}
                      </span>
                      <p v-if="scheduledTaskNeedsVerification(task)" class="schedule-verification-note">
                        结果待核实：请先在飞书中确认是否已经执行。只有确认“已核实，需要重新执行”后，才会恢复该任务。
                      </p>
                    </div>
                    <div class="schedule-card-actions">
                      <button
                        v-if="task.status === 'active' || task.status === 'paused'"
                        type="button"
                        class="schedule-mini-btn"
                        :class="{ enabled: task.status === 'active' }"
                        @click="toggleScheduledTask(task)"
                      >
                        {{ task.status === 'active' ? '关闭任务' : scheduledTaskNeedsVerification(task) ? '确认已核实，重新执行' : '开启任务' }}
                      </button>
                      <button
                        type="button"
                        class="schedule-mini-btn danger"
                        :disabled="!canDeleteScheduledTask(task)"
                        :title="canDeleteScheduledTask(task) ? '删除定时任务' : '请先关闭任务，再删除'"
                        @click="deleteScheduledTask(task)"
                      >
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="isAdmin && showModelPanel" class="model-popover" @click.stop>
                <label>
                  <span>服务商</span>
                  <select v-model="modelPreset" @change="applyPresetDefaults">
                    <option v-for="preset in modelPresets" :key="preset.id" :value="preset.id">{{ preset.label }}</option>
                  </select>
                </label>
                <label>
                  <span>API Key</span>
                  <input v-model="modelApiKey" type="password" placeholder="留空保留当前 Key" />
                </label>
                <label>
                  <span>Base URL</span>
                  <input v-model="modelBaseUrl" />
                </label>
                <label>
                  <span>模型名</span>
                  <input v-model="modelName" />
                </label>
                <div class="model-actions">
                  <button type="button" :disabled="modelSaving" @click="saveModelConfig">保存</button>
                </div>
                <p>当前：{{ currentModel.model || '-' }}</p>
                <p v-if="modelStatus">{{ modelStatus }}</p>
              </div>
            </div>

            <div class="chat-input-wrapper">
              <textarea
                ref="textareaRef"
                v-model="inputText"
                rows="1"
                aria-label="飞书需求"
                placeholder="输入飞书需求，按 Enter 发送..."
                @input="adjustTextareaHeight"
                @keydown="handleKeydown"
              ></textarea>
              <button v-if="isBusy" type="button" class="send-btn" title="停止执行" aria-label="停止执行" @click="stopActiveRequest">
                <Square :size="17" aria-hidden="true" />
              </button>
              <button type="button" class="send-btn" :disabled="!inputText.trim()" :title="isBusy ? '停止当前任务并发送' : '发送'" @click="sendMessage">
                <Send :size="17" aria-hidden="true" />
                <span class="sr-only">发送</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
