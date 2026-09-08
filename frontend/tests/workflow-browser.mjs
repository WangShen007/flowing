// Uses Node's built-in CDP WebSocket client and an isolated mock API. No Feishu writes.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve, extname } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const cdpUrl = process.argv[2]
if (!cdpUrl) throw new Error('Usage: node tests/workflow-browser.mjs <Chrome CDP websocket URL>')
const requests = []
const evilRequests = []
const aiPptImage = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
let evilBase = ''
let checkpointWaiting = false
let approvalWaiting = false
let enterpriseMode = false
let memberRole = 'employee'
let websiteRole = 'admin'
let botBindings = []
let mockScheduledTasks = [{
  id: 91, user_id: 'test', session_id: 'alpha', original_request: '每天提醒项目群', task_message: '每天提醒项目群',
  schedule_type: 'daily', time_of_day: '09:00', timezone: 'Asia/Shanghai', next_run_at: 4102444800,
  last_run_at: 4102440000, status: 'paused', run_count: 1,
  last_result: { status: 'unknown', success: false, requires_confirmation: true, message: '服务重启时结果不明确' }
}]
let workflowMemories = [{
  id: 7, intent_key: 'group_message', label: '群消息通知', status: 'candidate',
  success_count: 1, failure_count: 0, created_at: 1, updated_at: 1
}]
const timers = new Set()
const json = (res, data) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ code: 0, data })) }
const sse = (res, value) => res.write(`data: ${JSON.stringify(value)}\n\n`)
const aiPptMetadata = (unsafeDownload = false) => ({
  title: '隔离演示稿', style: '商务', version: 1, filename: 'owned.pptx',
  download_url: unsafeDownload ? `${evilBase}/steal.pptx` : '/api/v1/ai-ppt/files/owned.pptx',
  preview_url: '/api/v1/ai-ppt/files/owned.pptx/preview',
  slides: [{ index: 1, title: '摘要', layout: 'cover' }],
  feishu_tip: '使用认证下载'
})
const evilServer = createServer((req, res) => {
  evilRequests.push({ path: new URL(req.url, 'http://localhost').pathname, headers: { ...req.headers } })
  res.setHeader('Content-Type', 'image/png')
  res.end(aiPptImage)
})
await new Promise(resolve => evilServer.listen(0, '127.0.0.1', resolve))
evilBase = `http://127.0.0.1:${evilServer.address().port}`
const server = createServer(async (req, res) => {
  try {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : {}
    const path = new URL(req.url, 'http://localhost').pathname
    requests.push({ path, body, headers: { ...req.headers } })
    if (path === '/api/v1/bot-channels') return json(res, { enabled: true, worker_running: true, telegram_configured: false, bindings: botBindings })
    if (path === '/api/v1/bot-channels/weixin/pair') return json(res, { id: 'pair-owned', image: `data:image/png;base64,${aiPptImage.toString('base64')}`, expires_at: 4102444800 })
    if (path === '/api/v1/bot-channels/pairings/pair-owned/poll') return json(res, {
      status: body.verify_code === '1234' ? 'scanned' : 'need_verifycode', peer_id: body.verify_code === '1234' ? 'my-weixin-peer' : ''
    })
    if (path === '/api/v1/bot-channels/pairings/pair-owned/confirm') {
      botBindings = [{ id: 'owned-binding', channel: 'weixin', peer_id: 'my-weixin-peer', session_id: 'private-bot', state: 'ready', uncertain_count: 0 }]
      return json(res, { id: 'owned-binding' })
    }
    if (path === '/api/v1/bot-channels/bindings/owned-binding' && req.method === 'DELETE') {
      botBindings = []; return json(res, {})
    }
    if (path === '/api/v1/auth/feishu/config') return json(res, { enabled: enterpriseMode })
    if (path === '/api/v1/auth/feishu/status') return json(res, {
      enabled: enterpriseMode, role: websiteRole, connected: true, missing_scopes: [],
      capabilities: [{ key: 'documents_read', title: '读取和搜索文档' }, { key: 'groups_read', title: '查看本人所在群组' }]
    })
    if (path === '/api/v1/admin/members') return json(res, [
      { account: 'test', name: 'Test', role: 'admin', enabled: true },
      { account: 'employee', name: '员工甲', role: memberRole, enabled: true }
    ])
    if (path === '/api/v1/admin/members/employee' && req.method === 'PATCH') {
      memberRole = body.role
      return json(res, { account: 'employee', role: memberRole, enabled: true })
    }
    if (path === '/api/v1/auth/feishu/start') return json(res, {
      url: `http://${req.headers.host}/login?code=mock-code&state=mock-state`,
      state: 'mock-state', browser_secret: 'mock-browser-secret'
    })
    if (path === '/api/v1/auth/feishu/complete') return json(res, {
      token: 'oauth-session', account: { account: 'test', name: 'Test', role: 'employee' }
    })
    if (path === '/api/v1/auth/me') return json(res, { account: 'test', name: 'Test', role: websiteRole })
    if (path === '/api/v1/workflow-memories' && req.method === 'GET') return json(res, workflowMemories)
    if (path === '/api/v1/workflow-memories/7/activate' && req.method === 'POST') {
      workflowMemories = workflowMemories.map(item => item.id === 7 ? { ...item, status: 'active' } : item)
      return json(res, workflowMemories.find(item => item.id === 7))
    }
    if (path === '/api/v1/workflow-memories/7' && req.method === 'DELETE') {
      workflowMemories = workflowMemories.filter(item => item.id !== 7)
      return json(res, { disabled: true })
    }
    if (path === '/api/v1/sessions') return json(res, [
      { session_id: 'alpha', title: '历史甲', updated_at: 1 },
      { session_id: 'beta', title: '历史乙', updated_at: 2 }
    ])
    if (path === '/api/v1/sessions/ai-ppt/messages' || path === '/api/v1/sessions/ai-ppt-unsafe/messages') {
      return json(res, { messages: [{ role: 'assistant', content: '演示文稿已生成', metadata: {
        ai_ppt: aiPptMetadata(path.endsWith('ai-ppt-unsafe/messages'))
      } }] })
    }
    if (path === '/api/v1/sessions/unsafe/messages') return json(res, { messages: [
      { role: 'assistant', content: '安全文本 [可信链接](https://example.com) <img src="x" onerror="alert(1)"> <script>alert(2)</script> [危险链接](javascript:alert(3))' }
    ] })
    if (path === '/api/v1/sessions/structured-calendar/messages') return json(res, { messages: [
      { role: 'assistant', content: '请确认日历安排', metadata: {
        approval_required: true, resume_id: 'calendar-approval', resume_query: '创建项目评审会议',
        pending_tool: { kind: 'approval', operation: 'calendar events create', arguments: {
          params: { calendar_id: 'cal_unknown' },
          data: { summary: '项目评审', start_time: { timestamp: '1789010400', timezone: 'Asia/Shanghai' },
            end_time: { timestamp: '1789014000', timezone: 'Asia/Shanghai' }, attendees: ['ou_unknown'] }
        }, command: 'lark-cli calendar events create --params …' }
      } }
    ] })
    if (path === '/api/v1/sessions/structured-bitable/messages') return json(res, { messages: [
      { role: 'assistant', content: '请确认多维表格变更', metadata: {
        approval_required: true, resume_id: 'bitable-approval', resume_query: '把项目记录标记完成',
        pending_tool: { kind: 'approval', operation: 'base +record-batch-update', arguments: {
          params: { base_token: 'basc_unknown', table_id: 'tbl_unknown' },
          json: JSON.stringify({ update_records: { rec_unknown: { 状态: ['完成'] } } })
        }, command: 'lark-cli base +record-batch-update --params …' }
      } }
    ] })
    if (path.includes('/messages')) return json(res, { messages: [
      { role: 'assistant', content: path.includes('beta') ? '乙的历史正文' : '甲的历史正文' },
      ...(approvalWaiting && path.includes('alpha') ? [{ role: 'assistant', content: '请确认执行具体发送操作', metadata: {
        approval_required: true, resume_id: 'approval-checkpoint', resume_query: 'needs-approval',
        pending_tool: { kind: 'approval', operation: 'im +messages-send', arguments: {
          'chat-id': 'oc_unknown', text: '<at user_id="ou_unknown">darling</at> 该吃饭了'
        }, command: 'lark-cli im +messages-send --chat-id oc_unknown --text …' }
      } }] : []),
      ...(checkpointWaiting && path.includes('alpha') ? [{ role: 'assistant', content: '请补充权限', metadata: {
        setup_required: true, setup_scopes: ['test:read'], resume_id: 'checkpoint-test', resume_query: 'needs-auth'
      } }] : [])
    ] })
    if (path === '/api/v1/ai-ppt/files/owned.pptx/preview') return json(res, {
      filename: 'owned.pptx', slide_count: 2, slides: [
        { index: 1, title: '摘要', image_url: '/api/v1/ai-ppt/previews/owned-preview/slide_001.png', texts: ['摘要'] },
        { index: 2, title: '跨源地址应被拒绝', image_url: `${evilBase}/steal.png`, texts: ['不应外发凭据'] }
      ]
    })
    if (path === '/api/v1/ai-ppt/previews/owned-preview/slide_001.png') {
      res.setHeader('Content-Type', 'image/png')
      return res.end(aiPptImage)
    }
    if (path === '/api/v1/ai-ppt/files/owned.pptx') {
      res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.presentationml.presentation')
      res.setHeader('Content-Disposition', 'attachment; filename="owned.pptx"')
      return res.end(Buffer.from('mock-pptx'))
    }
    if (path === '/api/v1/lark/setup/status') return json(res, { ready: true, steps: [] })
    if (path === '/api/v1/models/config') return json(res, { current: {}, presets: [] })
    if (path === '/api/v1/scenarios') return json(res, enterpriseMode ? [
      {
        id: 'enterprise-ready', title: '企业已覆盖模板', category: 'Doc', description: '已审计命令与权限',
        fields: [], enterprise_ready: true, enterprise_reason: ''
      },
      {
        id: 'enterprise-unreviewed', title: '企业未审核模板', category: 'Advanced', description: '包含未审计操作',
        fields: [], enterprise_ready: false,
        enterprise_reason: '该模板包含尚未纳入企业安全清单的飞书操作，暂不可在企业模式执行。'
      }
    ] : [])
    if (path === '/api/v1/scheduled-tasks' && req.method === 'GET') return json(res, mockScheduledTasks)
    if (path === '/api/v1/scheduled-tasks/91/resume' && req.method === 'POST') {
      assert.equal(body.confirm_unknown, true)
      mockScheduledTasks = mockScheduledTasks.map(task => task.id === 91
        ? { ...task, status: 'active', last_result: {} }
        : task)
      return json(res, { message: 'active' })
    }
    if (path === '/api/v1/scheduled-tasks/config') return json(res, { enabled: true, poll_seconds: 30 })
    if (path === '/api/v1/chat/plan') {
      if (body.message === 'slow-plan') await delay(2000)
      if (body.message === 'timeout-plan') {
        res.statusCode = 504
        res.setHeader('Content-Type', 'application/json')
        return res.end(JSON.stringify({ detail: '生成计划超过20秒，请重试。' }))
      }
      return json(res, { session_id: body.session_id, plan: {
        summary: body.message.includes('所有人') && !body.message.includes('补充信息') ? '请提供参会人名单' : `计划 ${body.message}`,
        requires_input: body.message.includes('所有人') && !body.message.includes('补充信息'), need_confirmation: false,
        commands: [], relevant_skills: [], references: []
      } })
    }
    if (path.endsWith('/cancel')) return json(res, { cancelled: true })
    if (path === '/api/v1/lark/setup/stream') {
      res.setHeader('Content-Type', 'text/event-stream')
      sse(res, { type: 'done', success: true, message: '授权完成' })
      return res.end()
    }
    if (path === '/api/v1/chat') {
      res.setHeader('Content-Type', 'text/event-stream')
      res.flushHeaders()
      if (body.message === 'late-session') await delay(900)
      sse(res, { type: 'session', session_id: body.session_id })
      sse(res, { type: 'progress', content: '正在查询测试数据' })
      if (body.message === 'needs-approval') {
        approvalWaiting = !body.resume_id
        if (approvalWaiting) {
          sse(res, { type: 'content', content: '请确认执行具体发送操作' })
          sse(res, { type: 'metadata', metadata: { approval_required: true,
            resume_id: 'approval-checkpoint', resume_query: body.message,
            pending_tool: { kind: 'approval', operation: 'im +messages-send', arguments: {
              'chat-id': 'oc_unknown', text: '<at user_id="ou_unknown">darling</at> 该吃饭了'
            }, command: 'lark-cli im +messages-send --chat-id oc_unknown --text …' } } })
        } else {
          assert.equal(body.confirm_write, true)
          assert.equal(body.resume_id, 'approval-checkpoint')
          sse(res, { type: 'content', content: '已从原步骤完成发送' })
        }
      } else if (body.message === 'needs-auth' && !body.resume_id) {
        checkpointWaiting = true
        sse(res, { type: 'content', content: '请补充权限' })
        sse(res, { type: 'metadata', metadata: {
          setup_required: true, setup_scopes: ['test:read'], resume_id: 'checkpoint-test',
          resume_query: body.message, setup_steps: [{ key: 'auth_login', title: '补充授权' }]
        } })
      } else {
        if (body.resume_id) checkpointWaiting = false
        if (body.message === 'slow-stream') await delay(2500)
        if (body.message === 'memory-result') sse(res, { type: 'metadata', metadata: {
          workflow_memory: workflowMemories.find(item => item.id === 7)
        } })
        sse(res, { type: 'content', content: body.resume_id ? '授权后已接着完成任务' : `回复 ${body.message}` })
      }
      sse(res, { type: 'done', session_id: body.session_id })
      return res.end()
    }
    const asset = resolve('dist', path === '/' || path === '/login' ? 'index.html' : `.${path}`)
    if (!asset.startsWith(resolve('dist') + '/')) { res.statusCode = 404; return res.end() }
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[extname(asset)] || 'application/octet-stream'
    res.setHeader('Content-Type', type)
    res.end(await readFile(asset))
  } catch {
    if (!res.writableEnded) { res.statusCode = 500; res.end() }
  }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const base = `http://127.0.0.1:${server.address().port}`
const socket = new WebSocket(cdpUrl)
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
let nextId = 0
let sessionId
let targetId
const pending = new Map()
socket.onmessage = event => {
  const message = JSON.parse(event.data)
  if (pending.has(message.id)) {
    const { resolve, reject, timer } = pending.get(message.id)
    clearTimeout(timer)
    timers.delete(timer)
    pending.delete(message.id)
    message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result)
  }
}
const call = (method, params = {}, session = sessionId) => new Promise((resolve, reject) => {
  const id = ++nextId
  const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 15000)
  timers.add(timer)
  pending.set(id, { resolve, reject, timer })
  socket.send(JSON.stringify({ id, method, params, ...(session ? { sessionId: session } : {}) }))
})
const evaluate = async expression => {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}
const waitFor = async expression => {
  for (let i = 0; i < 80; i++) {
    if (await evaluate(expression)) return
    await delay(50)
  }
  throw new Error(`Condition failed: ${expression}\n${await evaluate('document.body.innerText')}`)
}
const waitForMockRequest = async predicate => {
  for (let i = 0; i < 80; i++) {
    if (requests.some(predicate)) return
    await delay(50)
  }
  throw new Error(`Mock request not observed: ${JSON.stringify(requests.slice(-8))}`)
}
const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`)
const fill = value => evaluate(`(() => { const e = document.querySelector('textarea'); e.value = ${JSON.stringify(value)}; e.dispatchEvent(new Event('input', { bubbles: true })); })()`)
const send = async value => { await fill(value); await click('.send-btn:last-child') }
const open = async () => {
  await call('Page.navigate', { url: `${base}/?session=alpha` })
  await waitFor(`document.body.innerText.includes('甲的历史正文') && !!document.querySelector('textarea')`)
}
const openSession = async id => {
  await call('Page.navigate', { url: `${base}/?session=${encodeURIComponent(id)}` })
  await waitFor(`!!document.querySelector('textarea')`)
}
try {
  targetId = (await call('Target.createTarget', { url: `${base}/` }, null)).targetId
  sessionId = (await call('Target.attachToTarget', { targetId, flatten: true }, null)).sessionId
  await waitFor(`location.origin === ${JSON.stringify(base)}`)
  await evaluate(`localStorage.setItem('feishu-cli.auth.token','test-only'); localStorage.setItem('feishu-cli.auth.account', JSON.stringify({account:'test',name:'Test'}));`)
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
  await open()
  await send('slow-plan')
  await waitFor(`!!document.querySelector('[aria-label="停止执行"]')`)
  await waitFor(`document.body.innerText.includes('已等待 1 秒')`)
  await fill('正在规划也能输入')
  assert.equal(await evaluate('document.querySelector("textarea").disabled'), false)
  await click('[aria-label="停止执行"]')
  await delay(2100)
  assert.equal(await evaluate('document.querySelector("textarea").value'), '正在规划也能输入')
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.message === 'slow-plan').length, 0)
  console.log('PASS stop planning and retain typed draft')

  await send('timeout-plan')
  await waitFor(`document.body.innerText.includes('生成计划超过20秒')`)
  assert.equal(await evaluate('document.querySelector("textarea").disabled'), false)
  assert.equal(await evaluate('document.body.innerText.includes("timeout-plan")'), true)
  assert.equal(await evaluate('document.body.innerText.includes("尚未执行，也没有发送任何飞书消息")'), true)
  assert.equal(await evaluate('Array.from(document.querySelectorAll(".plan-preview-card button")).some(e => e.textContent.includes("重试"))'), true)
  await evaluate(`Array.from(document.querySelectorAll('.plan-preview-card button')).find(e => e.textContent.includes('修改指令')).click()`)
  assert.equal(await evaluate('document.querySelector("textarea").value'), 'timeout-plan')
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.message === 'timeout-plan').length, 0)
  console.log('PASS plan timeout shows pending request, no-side-effect status, retry, and editable recovery')

  await send('slow-stream')
  await waitFor(`document.body.innerText.includes('执行过程') || document.body.innerText.includes('处理中')`)
  await fill('甲的未发送草稿')
  const cancelsBeforeSwitch = requests.filter(r => r.path.endsWith('/cancel')).length
  await click('.history-item:nth-child(2)')
  await waitFor(`document.body.innerText.includes('乙的历史正文')`)
  assert.equal(await evaluate('new URL(location.href).searchParams.get("session")'), 'beta')
  assert.equal(await evaluate('document.body.innerText.includes("回复 slow-stream")'), false)
  assert.equal(requests.filter(r => r.path.endsWith('/cancel')).length, cancelsBeforeSwitch)
  await send('beta-parallel')
  await waitFor(`document.body.innerText.includes('回复 beta-parallel')`)
  await click('.history-item:first-child')
  await waitFor(`document.body.innerText.includes('甲的历史正文')`)
  assert.equal(await evaluate('document.querySelector("textarea").value'), '甲的未发送草稿')
  await waitFor(`document.body.innerText.includes('回复 slow-stream')`)
  await waitFor(`!document.querySelector('[aria-label="停止执行"]')`)
  assert.equal(await evaluate('document.body.innerText.includes("回复 beta-parallel")'), false)
  console.log('PASS switch sessions keeps background stream alive; no cancellation or cross-session pollution')

  await send('slow-stream')
  await waitFor(`document.body.innerText.includes('处理中')`)
  await send('followup')
  await waitFor(`document.body.innerText.includes('回复 followup')`)
  console.log('PASS send new request while a previous request is running')

  await send('late-session')
  await waitFor(`!!document.querySelector('[aria-label="停止执行"]')`)
  await click('.new-chat-btn')
  await delay(1300)
  assert.equal(await evaluate('new URL(location.href).searchParams.get("session")'), null)
  assert.equal(await evaluate('document.body.innerText.includes("回复 late-session")'), false)
  console.log('PASS new chat ignores delayed session event')

  await open()
  await send('needs-auth')
  await waitFor(`!!document.querySelector('.lark-setup-card')`)
  await open()
  await waitFor(`!!document.querySelector('.lark-setup-card')`)
  await click('.lark-setup-card .write-confirm-btn')
  await waitFor(`document.body.innerText.includes('授权后已接着完成任务')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.resume_id === 'checkpoint-test').length, 1)
  console.log('PASS authorization checkpoint survives reload and automatically resumes once')

  await send('needs-approval')
  await waitFor(`document.body.innerText.includes('请确认执行具体发送操作')`)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('发送飞书消息')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('@darling（ou_unknown）')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('oc_unknown')`), true)
  assert.equal(await evaluate(`!!document.querySelector('.write-preview-raw')`), true)
  await open()
  await waitFor(`document.body.innerText.includes('请确认执行具体发送操作')`)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('该吃饭了')`), true)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent.trim() === '确认执行').click()`)
  await waitFor(`document.body.innerText.includes('已从原步骤完成发送')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.resume_id === 'approval-checkpoint').length, 1)
  console.log('PASS concrete tool approval survives reload and resumes the exact checkpoint once')

  await openSession('structured-calendar')
  await waitFor(`!!document.querySelector('.write-confirm-card')`)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('创建日历/会议安排')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('项目评审')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('1789010400')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('Asia/Shanghai')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('ou_unknown')`), true)
  console.log('PASS calendar approval card renders a readable summary and preserves unresolved IDs')

  await openSession('structured-bitable')
  await waitFor(`document.querySelector('.write-confirm-card')?.innerText.includes('修改多维表格')`)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('修改多维表格')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('basc_unknown')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('rec_unknown')`), true)
  assert.equal(await evaluate(`document.querySelector('.write-confirm-card').innerText.includes('状态')`), true)
  console.log('PASS Bitable approval card renders target and field changes')

  await send('今天23点开会，所有人参加')
  await waitFor(`document.body.innerText.includes('请提供参会人名单')`)
  assert.equal(await evaluate('!!document.querySelector(".plan-preview-card .write-confirm-btn")'), false)
  await send('张三、李四，1小时')
  await waitFor(`document.body.innerText.includes('回复 今天23点开会')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.message === '今天23点开会，所有人参加').length, 0)
  console.log('PASS clarify before execution, then retain original request with added attendees')

  await send('memory-result')
  await waitFor(`!!document.querySelector('.workflow-memory-card')`)
  assert.equal(await evaluate(`document.querySelector('.workflow-memory-card').innerText.includes('候选经验')`), true)
  await click('.workflow-memory-card button')
  await waitFor(`document.querySelector('.workflow-memory-card').innerText.includes('已记住')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/workflow-memories/7/activate').length, 1)
  console.log('PASS verified workflow memory is visible and can be activated from the conversation')

  await openSession('unsafe')
  await waitFor(`!!document.querySelector('.message-content a[href="https://example.com"]')`)
  const renderedMessage = await evaluate(`document.querySelector('.message.assistant .message-content')?.innerHTML || ''`)
  assert.equal(renderedMessage.includes('<script'), false)
  assert.equal(renderedMessage.includes('onerror'), false)
  assert.equal(renderedMessage.includes('javascript:'), false)
  assert.equal(await evaluate('document.querySelectorAll(".message-content script").length'), 0)
  assert.equal(await evaluate('!!document.querySelector(".message-content a[href=\\"https://example.com\\"]")'), true)
  console.log('PASS untrusted Markdown is sanitized while safe links remain usable')

  await openSession('ai-ppt')
  await waitFor(`document.body.innerText.includes('隔离演示稿')`)
  await waitFor('Array.from(document.querySelectorAll(".ai-ppt-preview img")).some(e => e.src.startsWith("blob:"))')
  await waitForMockRequest(request => request.path === '/api/v1/ai-ppt/files/owned.pptx/preview')
  await waitForMockRequest(request => request.path === '/api/v1/ai-ppt/previews/owned-preview/slide_001.png')
  const previewApiRequest = requests.find(request => request.path === '/api/v1/ai-ppt/files/owned.pptx/preview')
  const previewImageRequest = requests.find(request => request.path === '/api/v1/ai-ppt/previews/owned-preview/slide_001.png')
  assert.equal(previewApiRequest.headers['x-auth-token'], 'test-only')
  assert.equal(previewImageRequest.headers['x-auth-token'], 'test-only')
  assert.equal(await evaluate('document.querySelectorAll(".ai-ppt-preview img").length'), 1)
  assert.equal(await evaluate('document.body.innerText.includes("预览加载失败")'), true)
  assert.equal(evilRequests.length, 0)
  const previewBlobUrl = await evaluate('document.querySelector(".ai-ppt-preview img")?.src || ""')
  assert.equal(previewBlobUrl.startsWith('blob:'), true)
  assert.equal(await evaluate(`fetch(${JSON.stringify(previewBlobUrl)}).then(() => true).catch(() => false)`), true)
  await click('.ai-ppt-download')
  await waitForMockRequest(request => request.path === '/api/v1/ai-ppt/files/owned.pptx')
  const downloadRequest = requests.find(request => request.path === '/api/v1/ai-ppt/files/owned.pptx')
  assert.equal(downloadRequest.headers['x-auth-token'], 'test-only')
  await click('.history-item:nth-child(2)')
  await waitFor(`document.body.innerText.includes('乙的历史正文')`)
  assert.equal(await evaluate(`fetch(${JSON.stringify(previewBlobUrl)}).then(() => true).catch(() => false)`), false)
  await openSession('ai-ppt-unsafe')
  await waitFor(`document.body.innerText.includes('隔离演示稿')`)
  await waitFor(`document.body.innerText.includes('预览加载失败')`)
  await click('.ai-ppt-download')
  await waitFor(`document.body.innerText.includes('AI PPT 资源地址无效')`)
  assert.equal(evilRequests.length, 0)
  console.log('PASS AI PPT preview/download use same-origin auth, reject cross-origin URLs, and revoke Blob URLs on session switch')

  for (const [name, width, height] of [['desktop', 1280, 900], ['mobile', 390, 844]]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: name === 'mobile' })
    await delay(100)
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
    const screenshot = await call('Page.captureScreenshot', { format: 'png' })
    await writeFile(`/tmp/feishu-workflow-${name}.png`, Buffer.from(screenshot.data, 'base64'))
  }
  console.log('PASS desktop/mobile width checks and screenshots')

  enterpriseMode = true
  await evaluate('localStorage.clear()')
  await call('Page.navigate', { url: `${base}/login` })
  await waitFor(`document.body.innerText.includes('使用企业飞书登录')`)
  for (const [name, width, height] of [['desktop', 1280, 900], ['mobile', 390, 844]]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: name === 'mobile' })
    await delay(100)
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
    const screenshot = await call('Page.captureScreenshot', { format: 'png' })
    await writeFile(`/tmp/feishu-enterprise-login-${name}.png`, Buffer.from(screenshot.data, 'base64'))
  }
  await click('button.login-button')
  await waitFor(`localStorage.getItem('feishu-cli.auth.token') === 'oauth-session' && location.pathname === '/'`)
  const callback = requests.filter(r => r.path === '/api/v1/auth/feishu/complete')
  assert.equal(callback.length, 1)
  assert.deepEqual(callback[0].body, { state: 'mock-state', browser_secret: 'mock-browser-secret', code: 'mock-code' })
  assert.equal(await evaluate(`sessionStorage.getItem('feishu-cli.oauth.pending')`), null)
  assert.equal(await evaluate(`location.search.includes('code=')`), false)
  console.log('PASS enterprise login redirect, browser-bound callback, cleanup, desktop/mobile layout (mock provider)')

  await open()
  await send('needs-auth')
  await waitFor(`!!document.querySelector('.lark-setup-card')`)
  assert.equal(await evaluate('!!document.querySelector(".lark-setup-steps, .lark-setup-card .write-confirm-secondary")'), false)
  assert.equal(await evaluate('document.querySelector(".lark-setup-card .write-confirm-btn").textContent.trim()'), '授权并继续')
  await click('.lark-setup-card .write-confirm-btn')
  await waitFor(`document.body.innerText.includes('授权后已接着完成任务')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/auth/feishu/complete').length, 2)
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.resume_id === 'checkpoint-test').length, 2)
  console.log('PASS enterprise authorization popup resumes checkpoint without navigating main conversation')

  await open()
  await send('needs-auth')
  await waitFor(`!!document.querySelector('.lark-setup-card')`)
  await evaluate('window.open = () => null')
  await click('.lark-setup-card .write-confirm-btn')
  await waitFor(`location.pathname === '/' && document.body.innerText.includes('授权后已接着完成任务')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/auth/feishu/complete').length, 3)
  assert.equal(requests.filter(r => r.path === '/api/v1/chat' && r.body.resume_id === 'checkpoint-test').length, 3)
  assert.equal(await evaluate('sessionStorage.getItem("feishu-cli.oauth.continuation")'), null)
  console.log('PASS blocked authorization popup falls back to same-tab flow and resumes checkpoint')

  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent.includes('账号与权限')).click()`)
  await waitFor(`!!document.querySelector('[aria-label="员工甲的角色"]')`)
  assert.equal(await evaluate(`document.body.innerText.includes('AI 工作流记忆') && document.body.innerText.includes('群消息通知')`), true)
  await waitFor(`document.querySelector('.bot-channels')?.innerText.includes('待管理员配置 Bot Token')`)
  assert.equal(await evaluate(`document.querySelectorAll('.channel-row button')[1].disabled`), true)
  await click('.channel-row button')
  await waitFor(`!!document.querySelector('.pairing input')`)
  assert.equal(requests.filter(r => r.path.endsWith('/pair-owned/confirm')).length, 0)
  await evaluate(`const input = document.querySelector('.pairing input'); input.value = '1234'; input.dispatchEvent(new Event('input', { bubbles: true }))`)
  await evaluate(`Array.from(document.querySelectorAll('.pairing button')).find(b => b.textContent.includes('提交验证码')).click()`)
  await waitFor(`document.querySelector('.pairing')?.innerText.includes('my-weixin-peer')`)
  assert.equal(requests.filter(r => r.path.endsWith('/pair-owned/confirm')).length, 0)
  await evaluate(`Array.from(document.querySelectorAll('.pairing button')).find(b => b.textContent.includes('是我本人')).click()`)
  await waitFor(`document.querySelector('.bot-channels')?.innerText.includes('连接正常')`)
  assert.equal(requests.filter(r => r.path.endsWith('/pair-owned/confirm')).length, 1)
  await click('.channel-row button')
  await waitFor(`!document.querySelector('.bot-channels')?.innerText.includes('连接正常')`)
  console.log('PASS private bot QR, verification code, explicit website confirmation, unbinding and Telegram configuration state')
  await evaluate(`(() => { const e = document.querySelector('[aria-label="员工甲的角色"]'); e.value='lead'; e.dispatchEvent(new Event('change', { bubbles:true })); })()`)
  await waitFor(`document.body.innerText.includes('成员权限已更新')`)
  assert.equal(memberRole, 'lead')
  for (const [name, width, height] of [['desktop', 1280, 900], ['mobile', 390, 844]]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: name === 'mobile' })
    await delay(100)
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
    const screenshot = await call('Page.captureScreenshot', { format: 'png' })
    await writeFile(`/tmp/feishu-members-${name}.png`, Buffer.from(screenshot.data, 'base64'))
  }
  await click('[aria-label="关闭账号设置"]')
  console.log('PASS member role editing and desktop/mobile account dialog')

  await open()
  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent.includes('场景模板')).click()`)
  await waitFor(`document.body.innerText.includes('企业未开放')`)
  await evaluate(`Array.from(document.querySelectorAll('.scenario-list button')).find(e => e.textContent.includes('企业未审核模板')).click()`)
  await waitFor(`document.body.innerText.includes('尚未纳入企业安全清单')`)
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('.scenario-form button')).find(e => e.textContent.includes('填入输入框')).disabled`), true)
  console.log('PASS enterprise scenario catalog blocks unreviewed workflows before execution')

  await open()
  assert.equal(await evaluate('!!document.querySelector(".model-btn")'), true)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent.includes('定时任务')).click()`)
  await waitFor(`!!document.querySelector('.schedule-config-row')`)
  assert.equal(await evaluate('!!document.querySelector(".schedule-switch")'), true)
  assert.equal(await evaluate(`document.querySelector('.schedule-card').innerText.includes('结果待核实')`), true)
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('.schedule-card-actions button')).some(e => e.textContent.includes('确认已核实，重新执行'))`), true)
  await evaluate('window.confirm = () => false')
  await evaluate(`Array.from(document.querySelectorAll('.schedule-card-actions button')).find(e => e.textContent.includes('确认已核实')).click()`)
  await delay(100)
  assert.equal(requests.filter(r => r.path === '/api/v1/scheduled-tasks/91/resume').length, 0)
  await evaluate('window.confirm = () => true')
  await evaluate(`Array.from(document.querySelectorAll('.schedule-card-actions button')).find(e => e.textContent.includes('确认已核实')).click()`)
  await waitFor(`document.querySelector('.schedule-card').innerText.includes('待执行')`)
  assert.equal(requests.filter(r => r.path === '/api/v1/scheduled-tasks/91/resume').at(-1).body.confirm_unknown, true)
  console.log('PASS unknown scheduled result requires explicit verification before re-execution')
  websiteRole = 'employee'
  await open()
  assert.equal(await evaluate('!!document.querySelector(".model-btn")'), false)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent.includes('定时任务')).click()`)
  await waitFor(`!!document.querySelector('.schedule-popover')`)
  assert.equal(await evaluate('!!document.querySelector(".schedule-config-row, .schedule-switch")'), false)
  assert.equal(await evaluate('!!document.querySelector(".schedule-head-actions button")'), true)
  console.log('PASS employee keeps personal schedule access without global configuration controls')
} finally {
  if (targetId) await call('Target.closeTarget', { targetId }, null)
  socket.close()
  for (const timer of timers) clearTimeout(timer)
  server.closeAllConnections()
  server.close()
  evilServer.closeAllConnections()
  evilServer.close()
}
