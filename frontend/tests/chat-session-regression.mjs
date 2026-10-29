// Execute Chat.vue's compiled setup with synthetic I/O, without a browser or service.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import ts from 'typescript'

const sourcePath = process.env.FLOWING_CHAT_SFC || new URL('../src/components/Chat.vue', import.meta.url)
const source = await readFile(sourcePath, 'utf8')
const parsed = parse(source, { filename: 'Chat.vue' })
assert.deepEqual(parsed.errors, [])
const script = compileScript(parsed.descriptor, { id: 'chat-session-regression' })
const javascript = ts.transpileModule(script.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true }
}).outputText

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function jsonResponse(data) {
  return new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } })
}

function harness(fetchHandler) {
  const urls = []
  const requests = []
  const router = { replace: async () => {}, push: async () => {} }
  const modules = {
    vue: { ...vue, onMounted: () => {}, onUnmounted: () => {} },
    'vue-router': { useRouter: () => router },
    'lucide-vue-next': {},
    '../lib/feishuAuth': { authorizeFeishu: async () => {}, FEISHU_OAUTH_CONTINUATION_KEY: 'test' },
    '../lib/markdown': { renderMarkdown: (text) => text },
    '../lib/auth': {
      AUTH_TOKEN_KEY: 'test-token-key',
      buildAuthHeaders: () => ({ 'X-Auth-Token': 'test-token' }),
      clearAuthSession: () => {},
      getAuthAccount: () => ({ account: 'test', name: 'Test' })
    }
  }
  const module = { exports: {} }
  const context = {
    module,
    exports: module.exports,
    require: (name) => {
      if (name.endsWith('.vue')) return { __esModule: true, default: {} }
      if (name in modules) return modules[name]
      throw new Error(`Unexpected import: ${name}`)
    },
    fetch: (url, options) => {
      requests.push({ url, options })
      return fetchHandler(url, options)
    },
    window: {
      location: { pathname: '/' },
      history: {
        replaceState: (_state, _title, url) => urls.push({ method: 'replace', url }),
        pushState: (_state, _title, url) => urls.push({ method: 'push', url })
      }
    },
    crypto: { randomUUID: () => 'test-run-id' },
    AbortController,
    TextDecoder,
    requestAnimationFrame: (callback) => { callback(); return 1 },
    cancelAnimationFrame: () => {},
    console
  }
  vm.runInNewContext(javascript, context, { filename: 'Chat.vue' })
  const state = module.exports.default.setup({}, { expose: () => {} })
  return { state, urls, requests }
}

test('late history cannot replace the newer session', async () => {
  const alpha = deferred()
  const beta = deferred()
  const { state, urls } = harness((url) => {
    if (url.includes('/alpha/')) return alpha.promise
    if (url.includes('/beta/')) return beta.promise
    throw new Error(`Unexpected request: ${url}`)
  })
  state.sessionId.value = 'previous'
  state.inputText.value = 'previous draft'
  const first = state.loadChat('alpha')
  const second = state.loadChat('beta')
  beta.resolve(jsonResponse({ data: { messages: [{ role: 'assistant', content: 'beta response' }] } }))
  await second
  alpha.resolve(jsonResponse({ data: { messages: [{ role: 'assistant', content: 'late alpha response' }] } }))
  await first
  assert.equal(state.sessionId.value, 'beta')
  assert.equal(state.messages.value[0].content, 'beta response')
  assert.equal(state.drafts.get('previous'), 'previous draft')
  assert.equal(urls.at(-1).url, '?session=beta')
})

test('newChat saves the draft and resets the view without aborting a background run', async () => {
  const { state, urls, requests } = harness(() => { throw new Error('Unexpected request') })
  const plan = new AbortController()
  const setup = new AbortController()
  const background = new AbortController()
  const oldMessages = [{ role: 'assistant', content: 'running', loading: true }]
  state.sessionId.value = 'alpha'
  state.inputText.value = ''
  state.pendingPlanMessage.value = 'alpha draft'
  state.drafts.set('', 'new-chat draft')
  state.messages.value = oldMessages
  state.runningChats.set('alpha', { controller: background, runId: 'alpha-run', messages: oldMessages })
  state.requestController = background
  state.planController = plan
  state.setupController = setup
  state.activeRunId = 'alpha-run'
  state.isLoading.value = true
  state.planLoading.value = true
  state.planPreview.value = { summary: 'pending plan' }
  state.planError.value = 'old error'
  state.planElapsed.value = 3
  state.showLarkSetup.value = true
  state.larkSetupRunning.value = true
  state.showWriteConfirm.value = true
  state.pendingWriteTool.value = { operation: 'test', arguments: {} }
  state.pendingResume.value = { id: 'resume', query: 'query', session: 'alpha' }
  state.shouldStickToBottom.value = false
  state.showSidebar.value = true
  const result = state.newChat()
  assert.equal(typeof result.then, 'function')
  await result
  assert.equal(state.drafts.get('alpha'), 'alpha draft')
  assert.equal(state.inputText.value, 'new-chat draft')
  assert.equal(state.sessionId.value, '')
  assert.equal(state.messages.value.length, 0)
  assert.equal(state.pendingResume.value, null)
  assert.equal(state.pendingPlanMessage.value, '')
  assert.equal(state.planPreview.value, null)
  assert.equal(state.planError.value, '')
  assert.equal(state.planElapsed.value, 0)
  assert.equal(state.showLarkSetup.value, false)
  assert.equal(state.larkSetupRunning.value, false)
  assert.equal(state.showWriteConfirm.value, false)
  assert.equal(state.pendingWriteTool.value, null)
  assert.equal(state.shouldStickToBottom.value, true)
  assert.equal(state.showSidebar.value, false)
  assert.equal(state.isLoading.value, false)
  assert.equal(state.planLoading.value, false)
  assert.equal(state.requestController, null)
  assert.equal(state.activeRunId, '')
  assert.equal(plan.signal.aborted, true)
  assert.equal(setup.signal.aborted, true)
  assert.equal(background.signal.aborted, false)
  assert.equal(state.runningChats.get('alpha').runId, 'alpha-run')
  assert.equal(urls.at(-1).url, '/')
  assert.equal(requests.length, 0)
})

test('loadChat restores an existing background run without fetching history', async () => {
  const { state, requests } = harness(() => { throw new Error('Unexpected request') })
  const controller = new AbortController()
  const oldMessages = [{ role: 'assistant', content: 'background', loading: true }]
  state.sessionId.value = 'beta'
  state.inputText.value = 'beta draft'
  state.drafts.set('alpha', 'alpha draft')
  state.runningChats.set('alpha', { controller, runId: 'alpha-run', messages: oldMessages })
  await state.loadChat('alpha')
  assert.equal(state.sessionId.value, 'alpha')
  assert.equal(state.inputText.value, 'alpha draft')
  assert.equal(vue.toRaw(state.messages.value), oldMessages)
  assert.equal(state.requestController, controller)
  assert.equal(state.activeRunId, 'alpha-run')
  assert.equal(state.isLoading.value, true)
  assert.equal(controller.signal.aborted, false)
  assert.equal(requests.length, 0)
})

test('actual streaming progress branch retains the latest 80 entries across UTF-8 chunks', async () => {
  const events = [
    { type: 'session', session_id: 'alpha' },
    ...Array.from({ length: 82 }, (_, index) => ({ type: 'progress', content: `进度-${index}` })),
    { type: 'content', content: '完成' },
    { type: 'done', session_id: 'alpha' }
  ]
  const bytes = new TextEncoder().encode(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''))
  const split = bytes.indexOf(0xe8) + 1
  assert.ok(split > 1)
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.subarray(0, split))
      controller.enqueue(bytes.subarray(split))
      controller.close()
    }
  })
  const { state, requests } = harness((url) => {
    if (url === '/api/v1/chat') return Promise.resolve(new Response(stream))
    if (url.startsWith('/api/v1/sessions?')) return Promise.resolve(jsonResponse({ data: [] }))
    throw new Error(`Unexpected request: ${url}`)
  })
  state.sessionId.value = 'alpha'
  state.messages.value = [{ role: 'assistant', content: '', loading: true, metadata: {} }]
  state.isLoading.value = true
  await state.runChatRequest('synthetic', 0)
  const assistant = state.messages.value[0]
  assert.equal(assistant.content, '完成')
  assert.equal(assistant.loading, false)
  assert.equal(assistant.metadata.lark_progress.length, 80)
  assert.equal(assistant.metadata.lark_progress[0], '进度-2')
  assert.equal(assistant.metadata.lark_progress.at(-1), '进度-81')
  assert.equal(state.getLarkTrace(assistant).length, 80)
  assert.equal(state.runningChats.has('alpha'), false)
  const sent = JSON.parse(requests.find((request) => request.url === '/api/v1/chat').options.body)
  assert.equal(sent.session_id, 'alpha')
  assert.equal(sent.run_id, 'test-run-id')
})

test('late background stream updates its owner, not a newly opened conversation', async () => {
  const gate = deferred()
  const chunk = new TextEncoder().encode([
    { type: 'session', session_id: 'alpha' },
    { type: 'progress', content: '后台进度' },
    { type: 'content', content: '后台完成' },
    { type: 'done', session_id: 'alpha' }
  ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''))
  let read = false
  const response = { ok: true, status: 200, body: { getReader: () => ({
    async read() {
      if (read) return { done: true }
      read = true
      await gate.promise
      return { done: false, value: chunk }
    }
  }) } }
  const { state, urls } = harness((url) => {
    if (url === '/api/v1/chat') return Promise.resolve(response)
    if (url.startsWith('/api/v1/sessions?')) return Promise.resolve(jsonResponse({ data: [] }))
    throw new Error(`Unexpected request: ${url}`)
  })
  const ownerMessages = [{ role: 'assistant', content: '', loading: true, metadata: {} }]
  state.sessionId.value = 'alpha'
  state.messages.value = ownerMessages
  state.isLoading.value = true
  const run = state.runChatRequest('synthetic', 0)
  await state.newChat()
  assert.equal(state.runningChats.has('alpha'), true)
  gate.resolve()
  await run
  assert.equal(state.sessionId.value, '')
  assert.equal(state.messages.value.length, 0)
  assert.equal(ownerMessages[0].content, '后台完成')
  assert.equal(ownerMessages[0].metadata.lark_progress[0], '后台进度')
  assert.equal(state.runningChats.has('alpha'), false)
  assert.equal(urls.at(-1).url, '/')
})

for (const [name, response, expected] of [
  ['401', new Response('{"detail":"请登录"}', { status: 401, headers: { 'content-type': 'application/json' } }), '请登录'],
  ['409', new Response('{"detail":"会话正在执行"}', { status: 409, headers: { 'content-type': 'application/json' } }), '会话正在执行'],
  ['500', new Response('{"message":"服务器错误"}', { status: 500, headers: { 'content-type': 'application/json' } }), '服务器错误'],
  ['non-json 500', new Response('proxy error', { status: 500 }), '删除会话失败'],
  ['non-json 200', new Response('proxy error', { status: 200 }), '后端没有返回 JSON'],
  ['business error', new Response('{"code":1,"message":"未删除"}', { headers: { 'content-type': 'application/json' } }), '未删除']
]) {
  test(`failed delete (${name}) keeps current session, messages, and draft`, async () => {
    const { state, requests, urls } = harness(() => Promise.resolve(response))
    state.sessionId.value = 'alpha'
    state.inputText.value = 'unsent draft'
    state.messages.value = [{ role: 'assistant', content: 'keep this' }]
    state.historyList.value = [{ id: 'alpha', title: 'Current', time: 'now' }]
    const background = new AbortController()
    state.runningChats.set('beta', { controller: background, runId: 'beta-run', messages: [] })
    let stopped = false
    await state.deleteHistory({ stopPropagation: () => { stopped = true } }, 'alpha')
    assert.equal(stopped, true)
    assert.equal(state.sessionId.value, 'alpha')
    assert.equal(state.inputText.value, 'unsent draft')
    assert.equal(state.messages.value[0].content, 'keep this')
    assert.equal(state.historyList.value[0].id, 'alpha')
    assert.equal(state.runningChats.get('beta').runId, 'beta-run')
    assert.equal(background.signal.aborted, false)
    assert.match(state.historyError.value, new RegExp(expected))
    assert.equal(requests.length, 1)
    assert.equal(requests[0].options.method, 'DELETE')
    assert.equal(urls.length, 0)
  })
}

test('successful delete resets only the deleted current view and refreshes history', async () => {
  const { state, requests } = harness((url) => {
    if (url === '/api/v1/sessions/alpha') return Promise.resolve(jsonResponse({ code: 0, message: 'Session deleted' }))
    if (url.startsWith('/api/v1/sessions?')) return Promise.resolve(jsonResponse({ code: 0, data: [] }))
    throw new Error(`Unexpected request: ${url}`)
  })
  state.sessionId.value = 'alpha'
  state.inputText.value = 'alpha draft'
  state.messages.value = [{ role: 'assistant', content: 'deleted' }]
  state.historyError.value = 'old failure'
  await state.deleteHistory({ stopPropagation: () => {} }, 'alpha')
  assert.equal(state.sessionId.value, '')
  assert.equal(state.messages.value.length, 0)
  assert.equal(state.drafts.get('alpha'), 'alpha draft')
  assert.equal(state.historyError.value, '')
  assert.equal(requests.length, 2)
})

test('deleting a running session opens it rather than cancelling or sending DELETE', async () => {
  const { state, requests } = harness(() => { throw new Error('Unexpected request') })
  const controller = new AbortController()
  const messages = [{ role: 'assistant', content: 'still running', loading: true }]
  state.runningChats.set('alpha', { controller, runId: 'alpha-run', messages })
  state.sessionId.value = 'other'
  await state.deleteHistory({ stopPropagation: () => {} }, 'alpha')
  assert.equal(state.sessionId.value, 'alpha')
  assert.equal(state.activeRunId, 'alpha-run')
  assert.equal(controller.signal.aborted, false)
  assert.equal(requests.length, 0)
})

test('late successful delete cannot reset a different conversation', async () => {
  const gate = deferred()
  const { state } = harness((url) => {
    if (url === '/api/v1/sessions/alpha') return gate.promise
    if (url.startsWith('/api/v1/sessions?')) return Promise.resolve(jsonResponse({ code: 0, data: [] }))
    throw new Error(`Unexpected request: ${url}`)
  })
  state.sessionId.value = 'alpha'
  const pending = state.deleteHistory({ stopPropagation: () => {} }, 'alpha')
  state.sessionId.value = 'beta'
  state.inputText.value = 'beta draft'
  state.messages.value = [{ role: 'assistant', content: 'beta' }]
  gate.resolve(jsonResponse({ code: 0 }))
  await pending
  assert.equal(state.sessionId.value, 'beta')
  assert.equal(state.inputText.value, 'beta draft')
  assert.equal(state.messages.value[0].content, 'beta')
})

test('network failure deleting a session retains the current view', async () => {
  const { state, requests } = harness(() => Promise.reject(new Error('offline')))
  state.sessionId.value = 'alpha'
  state.inputText.value = 'draft'
  state.messages.value = [{ role: 'assistant', content: 'keep this' }]
  await state.deleteHistory({ stopPropagation: () => {} }, 'alpha')
  assert.equal(state.sessionId.value, 'alpha')
  assert.equal(state.inputText.value, 'draft')
  assert.equal(state.messages.value[0].content, 'keep this')
  assert.match(state.historyError.value, /删除会话失败|offline/)
  assert.equal(requests.length, 1)
})
