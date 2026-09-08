<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  Globe2,
  History,
  LayoutTemplate,
  Plus,
  RotateCcw,
  Save,
  ScanSearch,
  Search,
  Sparkles,
  Trash2
} from 'lucide-vue-next'
import { buildAuthHeaders, getAuthAccount } from '../lib/auth'

interface TemplateField {
  key: string
  label: string
  placeholder: string
}

interface UserTemplate {
  id: string
  template_id: number
  template_key: string
  title: string
  category: string
  description: string
  visibility: 'private' | 'public'
  owner: { account: string; name: string }
  current_version: number
  updated_at: number
  published_at?: number
  prompt: string
  fields: TemplateField[]
  requires_ai_content_generation?: boolean
  content_generation_label?: string
}

interface TemplateVersion {
  id: number
  version: number
  prompt: string
  fields: TemplateField[]
  requires_ai_content_generation?: boolean
  content_generation_label?: string
  editor: { account: string; name: string }
  change_note: string
  created_at: number
  is_current: boolean
}

const router = useRouter()
const account = getAuthAccount()
const scope = ref<'accessible' | 'mine' | 'community'>('accessible')
const templates = ref<UserTemplate[]>([])
const selected = ref<UserTemplate | null>(null)
const versions = ref<TemplateVersion[]>([])
const loading = ref(false)
const saving = ref(false)
const generating = ref(false)
const status = ref('')
const aiRequirement = ref('')
const templateSearch = ref('')

const form = ref({
  title: '',
  category: '自定义模板',
  description: '',
  visibility: 'private' as 'private' | 'public',
  prompt: '',
  requires_ai_content_generation: false,
  content_generation_label: 'AI 扩写内容',
  change_note: '',
  fields: [] as TemplateField[]
})

const isOwner = computed(() => selected.value && selected.value.owner.account === account?.account)
const canEdit = computed(() => !selected.value || isOwner.value)
const filteredTemplates = computed(() => {
  const keyword = templateSearch.value.trim().toLowerCase()
  if (!keyword) return templates.value
  return templates.value.filter((template) => {
    return [template.title, template.category, template.description, template.owner.name, template.owner.account]
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  })
})

const apiJson = async (response: Response) => {
  if (!(response.headers.get('content-type') || '').includes('application/json')) {
    throw new Error(`服务暂时不可用（HTTP ${response.status}），请稍后重试。`)
  }
  const payload = await response.json()
  if (!response.ok || payload.code !== 0) {
    throw new Error(payload.detail || payload.message || '请求失败')
  }
  return payload.data
}

const formatTime = (timestamp?: number) => {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleString()
}

const loadTemplates = async () => {
  loading.value = true
  status.value = ''
  try {
    const response = await fetch(`/api/v1/templates?scope=${scope.value}`, { headers: buildAuthHeaders() })
    templates.value = await apiJson(response)
    if (selected.value) {
      const current = templates.value.find((item) => item.template_id === selected.value?.template_id)
      if (current) selectTemplate(current)
    }
  } catch (error: any) {
    status.value = error.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const resetForm = () => {
  selected.value = null
  versions.value = []
  form.value = {
    title: '',
    category: '自定义模板',
    description: '',
    visibility: 'private',
    prompt: '',
    requires_ai_content_generation: false,
    content_generation_label: 'AI 扩写内容',
    change_note: '',
    fields: [{ key: 'input', label: '输入', placeholder: '请输入内容' }]
  }
}

const selectTemplate = async (template: UserTemplate) => {
  selected.value = template
  form.value = {
    title: template.title,
    category: template.category,
    description: template.description,
    visibility: template.visibility,
    prompt: template.prompt,
    requires_ai_content_generation: Boolean(template.requires_ai_content_generation),
    content_generation_label: template.content_generation_label || 'AI 扩写内容',
    change_note: '',
    fields: template.fields.map((item) => ({ ...item }))
  }
  await loadVersions(template.template_id)
}

const loadVersions = async (templateId: number) => {
  try {
    const response = await fetch(`/api/v1/templates/${templateId}/versions`, { headers: buildAuthHeaders() })
    versions.value = await apiJson(response)
  } catch (error: any) {
    status.value = error.message || '版本加载失败'
  }
}

const addField = () => {
  form.value.fields.push({ key: '', label: '', placeholder: '' })
}

const removeField = (index: number) => {
  form.value.fields.splice(index, 1)
}

const saveTemplate = async (visibility?: 'private' | 'public') => {
  if (!canEdit.value) return
  saving.value = true
  status.value = ''
  try {
    const body = {
      ...form.value,
      visibility: visibility || form.value.visibility,
      fields: form.value.fields.filter((item) => item.key.trim() && item.label.trim())
    }
    const response = await fetch(selected.value ? `/api/v1/templates/${selected.value.template_id}` : '/api/v1/templates', {
      method: selected.value ? 'PUT' : 'POST',
      headers: { ...buildAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    const saved = await apiJson(response)
    status.value = selected.value ? `已保存为版本 ${saved.current_version}` : '模板已创建'
    selected.value = saved
    await loadTemplates()
    await loadVersions(saved.template_id)
  } catch (error: any) {
    status.value = error.message || '保存失败'
  } finally {
    saving.value = false
  }
}

const saveAndPublish = async () => {
  await saveTemplate('public')
  if (selected.value && selected.value.visibility !== 'public') {
    await publishTemplate()
  }
}

const syncFieldsFromPrompt = () => {
  const keys = Array.from(new Set(Array.from(form.value.prompt.matchAll(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g)).map((match) => match[1])))
  if (!keys.length) {
    status.value = 'Prompt 里还没有 {{field_key}} 变量。'
    return
  }
  const existing = new Map(form.value.fields.map((item) => [item.key, item]))
  form.value.fields = keys.map((key) => existing.get(key) || {
    key,
    label: key.replace(/_/g, ' '),
    placeholder: `请输入${key.replace(/_/g, ' ')}`
  })
  status.value = `已识别 ${keys.length} 个字段。`
}

const generateTemplateDraft = async () => {
  if (!aiRequirement.value.trim()) {
    status.value = '先输入你想固定下来的流程，例如“读妙记并按负责人创建任务”。'
    return
  }
  generating.value = true
  status.value = ''
  try {
    const response = await fetch('/api/v1/templates/generate', {
      method: 'POST',
      headers: { ...buildAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirement: aiRequirement.value })
    })
    const draft = await apiJson(response)
    selected.value = null
    versions.value = []
    form.value = {
      title: draft.title || '',
      category: draft.category || '自定义模板',
      description: draft.description || '',
      visibility: draft.visibility || 'private',
      prompt: draft.prompt || '',
      requires_ai_content_generation: Boolean(draft.requires_ai_content_generation),
      content_generation_label: draft.content_generation_label || 'AI 扩写内容',
      change_note: draft.change_note || 'AI 生成初稿',
      fields: Array.isArray(draft.fields) ? draft.fields : []
    }
    status.value = 'AI 已生成模板草稿，可以直接保存为私有模板，或调整后再保存发布。'
  } catch (error: any) {
    status.value = error.message || 'AI 生成失败'
  } finally {
    generating.value = false
  }
}

const publishTemplate = async () => {
  if (!selected.value || !isOwner.value) return
  saving.value = true
  try {
    const response = await fetch(`/api/v1/templates/${selected.value.template_id}/publish`, {
      method: 'POST',
      headers: buildAuthHeaders()
    })
    selected.value = await apiJson(response)
    form.value.visibility = 'public'
    status.value = '已发布到模板社区，所有人都可以使用'
    await loadTemplates()
  } catch (error: any) {
    status.value = error.message || '发布失败'
  } finally {
    saving.value = false
  }
}

const rollbackVersion = async (version: TemplateVersion) => {
  if (!selected.value || !isOwner.value || version.is_current) return
  saving.value = true
  try {
    const response = await fetch(`/api/v1/templates/${selected.value.template_id}/versions/${version.version}/rollback`, {
      method: 'POST',
      headers: buildAuthHeaders()
    })
    const rolledBack = await apiJson(response)
    status.value = `已基于版本 ${version.version} 创建新版本 ${rolledBack.current_version}`
    await selectTemplate(rolledBack)
    await loadTemplates()
  } catch (error: any) {
    status.value = error.message || '回滚失败'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  resetForm()
  await loadTemplates()
})
</script>

<template>
  <div class="template-page">
    <header class="template-header">
      <div class="template-heading">
        <button type="button" class="icon-button" @click="router.push('/')" aria-label="返回对话" title="返回对话">
          <ArrowLeft :size="18" aria-hidden="true" />
        </button>
        <div class="template-mark"><LayoutTemplate :size="18" :stroke-width="1.8" /></div>
        <div>
          <div class="template-breadcrumb"><span>飞书 CLI</span><ChevronRight :size="13" /><strong>模板社区</strong></div>
          <h1>流程模板</h1>
        </div>
      </div>
      <div class="header-actions">
        <span class="account-label">{{ account?.name || account?.account || '当前账号' }}</span>
        <button type="button" class="primary" @click="resetForm"><Plus :size="15" /> 新建模板</button>
      </div>
    </header>

    <main class="template-shell">
      <aside class="template-list-panel">
        <div class="tabs">
          <button type="button" :class="{ active: scope === 'accessible' }" @click="scope = 'accessible'; loadTemplates()">可用</button>
          <button type="button" :class="{ active: scope === 'mine' }" @click="scope = 'mine'; loadTemplates()">我的</button>
          <button type="button" :class="{ active: scope === 'community' }" @click="scope = 'community'; loadTemplates()">社区</button>
        </div>
        <label class="template-search-wrap">
          <Search :size="15" aria-hidden="true" />
          <input v-model="templateSearch" class="template-search" aria-label="搜索模板" placeholder="搜索模板、分类或创建者" />
        </label>
        <div v-if="loading" class="empty">加载中...</div>
        <div v-else-if="!filteredTemplates.length" class="empty">暂无匹配模板</div>
        <button
          v-for="template in filteredTemplates"
          v-else
          :key="template.id"
          type="button"
          class="template-card group"
          :class="{ active: selected?.template_id === template.template_id }"
          @click="selectTemplate(template)"
        >
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <span class="template-card-meta">
            <span class="badge" :class="template.visibility">{{ template.visibility === 'public' ? '公开' : '私有' }}</span>
            <span v-if="template.requires_ai_content_generation" class="badge ai">AI 扩写</span>
          </span>
          <strong>{{ template.title }}</strong>
          <small>{{ template.category }} · v{{ template.current_version }}</small>
          <p>{{ template.description || '暂无描述' }}</p>
          <small>创建者：{{ template.owner.name }}（{{ template.owner.account }}）</small>
        </button>
      </aside>

      <section class="editor-panel">
        <div class="ai-draft-panel">
          <div class="ai-draft-copy">
            <span class="section-icon"><Sparkles :size="17" /></span>
            <div><h2>AI 一键生成模板</h2>
            <p>描述你想固定的流程，AI 会先生成可保存、可发布的模板草稿。</p>
            </div>
          </div>
          <textarea
            v-model="aiRequirement"
            aria-label="模板需求"
            rows="3"
            placeholder="例如：读一下妙记链接，提取所有 action items，按负责人创建任务，并在项目群里通知每个人"
          ></textarea>
          <button type="button" class="primary" :disabled="generating" @click="generateTemplateDraft">
            <Sparkles :size="15" /> {{ generating ? '生成中...' : '生成草稿' }}
          </button>
        </div>

        <div class="editor-head">
          <div>
            <h2>{{ selected ? '编辑模板' : '新建模板' }}</h2>
            <p v-if="selected">当前版本 v{{ selected.current_version }} · 更新于 {{ formatTime(selected.updated_at) }}</p>
            <p v-else>保存后会立即进入你的可用模板列表。</p>
          </div>
          <div class="editor-actions">
            <button type="button" :disabled="!selected || !isOwner || selected.visibility === 'public' || saving" @click="publishTemplate">
              <Globe2 :size="15" /> 发布
            </button>
            <button type="button" :disabled="!canEdit || saving" @click="saveAndPublish">
              <Globe2 :size="15" /> 保存并发布
            </button>
            <button type="button" class="primary" :disabled="!canEdit || saving" @click="saveTemplate()">
              <Save :size="15" /> {{ saving ? '保存中...' : '保存版本' }}
            </button>
          </div>
        </div>

        <div v-if="selected && !isOwner" class="notice">这是社区模板，你可以使用它；只有创建者可以修改和发布。</div>
        <div v-if="status" class="notice">{{ status }}</div>

        <div class="form-grid">
          <label>
            <span>模板名称</span>
            <input v-model="form.title" :disabled="!canEdit" />
          </label>
          <label>
            <span>分类</span>
            <input v-model="form.category" :disabled="!canEdit" />
          </label>
          <label>
            <span>可见性</span>
            <select v-model="form.visibility" :disabled="!canEdit">
              <option value="private">私有</option>
              <option value="public">公开</option>
            </select>
          </label>
          <label>
            <span>版本说明</span>
            <input v-model="form.change_note" :disabled="!canEdit" placeholder="例如：补充会议室偏好字段" />
          </label>
        </div>

        <div class="content-generation-box">
          <label class="toggle-row">
            <input v-model="form.requires_ai_content_generation" type="checkbox" :disabled="!canEdit" />
            <span>
              <strong>执行前 AI 扩写</strong>
              <small>适合文档、纪要、总结、报告、邮件、Slides 等内容型模板；开启后用户使用模板时也可以再次切换。</small>
            </span>
          </label>
          <label>
            <span>按钮文案</span>
            <input v-model="form.content_generation_label" :disabled="!canEdit || !form.requires_ai_content_generation" placeholder="例如：AI 扩写纪要 / AI 生成正文" />
          </label>
        </div>

        <label class="full">
          <span>描述</span>
          <input v-model="form.description" :disabled="!canEdit" />
        </label>

        <label class="full">
          <span>Prompt 模板</span>
          <textarea v-model="form.prompt" :disabled="!canEdit" rows="8" placeholder="使用 {{field_key}} 插入字段值"></textarea>
        </label>

        <div class="field-section">
          <div class="section-title">
            <h3><FileText :size="17" /> 字段配置</h3>
            <div class="field-actions">
              <button type="button" :disabled="!canEdit" @click="syncFieldsFromPrompt"><ScanSearch :size="15" /> 识别字段</button>
              <button type="button" :disabled="!canEdit" @click="addField"><Plus :size="15" /> 添加字段</button>
            </div>
          </div>
          <div v-if="!form.fields.length" class="empty">这个模板没有可填字段。</div>
          <div v-for="(field, index) in form.fields" :key="index" class="field-row group">
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <input v-model="field.key" :disabled="!canEdit" aria-label="字段 key" placeholder="字段 key，例如 group_name" />
            <input v-model="field.label" :disabled="!canEdit" aria-label="字段名称" placeholder="字段名称，例如 群名称" />
            <input v-model="field.placeholder" :disabled="!canEdit" aria-label="占位提示" placeholder="占位提示" />
            <button type="button" class="icon-button danger" :disabled="!canEdit" @click="removeField(index)" aria-label="删除字段" title="删除字段"><Trash2 :size="15" /></button>
          </div>
        </div>
      </section>

      <aside class="version-panel">
        <h2><History :size="17" /> 版本历史</h2>
        <div v-if="!selected" class="empty">选择一个模板后查看版本。</div>
        <div v-else-if="!versions.length" class="empty">暂无版本。</div>
        <div v-for="version in versions" :key="version.id" class="version-card group">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div>
            <strong>v{{ version.version }}</strong>
            <span v-if="version.is_current">当前</span>
          </div>
          <p>{{ version.change_note || '无版本说明' }}</p>
          <small v-if="version.requires_ai_content_generation">AI 扩写：{{ version.content_generation_label || '已开启' }}</small>
          <small>{{ version.editor.name }}（{{ version.editor.account }}）</small>
          <small>{{ formatTime(version.created_at) }}</small>
          <button type="button" :disabled="!isOwner || version.is_current || saving" @click="rollbackVersion(version)">
            <RotateCcw :size="14" /> 回滚为新版本
          </button>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped src="./template-community.css"></style>
