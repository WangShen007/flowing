# Feishu CLI Web
![alt text](doc/架构图1.png)

把飞书/Lark CLI 变成一个可私有化部署的 Web 智能工作台。

Feishu CLI Web 基于官方 `lark-cli`，提供自然语言交互、执行计划预览、写操作确认、多用户隔离和 SQLite 本地存储。它适合团队把飞书自动化能力快速接入自己的 Agent、内部工具或运维平台。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5%2B-42b883.svg)](https://vuejs.org/)

## 它解决什么问题

飞书官方 CLI 能力很完整，但团队成员通常不想记命令、配置参数、处理授权和调试输出。Feishu CLI Web 在 CLI 之上加了一层更适合团队使用的 Web 工作流：


- 用自然语言描述飞书任务
- 执行前预览计划和命令
- 写操作需要确认，降低误操作风险
- 每个用户独立授权，互不影响
- 聊天记录、执行记录、账号信息统一保存到 SQLite
- 内置常用场景模板，便于沉淀团队流程
- 内置 AI PPT 工作流，支持生成、修改、完整预览，并通过按钮上传云文档或发送到飞书
- 一次部署，多人使用：每个用户有独立的账号，互不影响。
- 专注于飞书CLI能力的Agent：响应速度超快。

## 典型场景

```text
帮我和同事小王在明天找一个 1 小时的空闲时间，创建主题为「项目复盘」的会议，并把会议链接发给他
```

系统会先生成计划：

```text
1. 搜索联系人小王
2. 查询明天工作时间内双方共同空闲时间
3. 创建「项目复盘」会议
4. 邀请参会人
5. 发送会议链接
```

确认后才会真正执行飞书写操作。
## 功能亮点

- **自然语言飞书操作**：发消息、建会议、查日程、创建文档、导入多维表格等。
- **计划预览**：先看清楚系统准备做什么，再决定是否执行。
- **场景模板**：群通知、会议安排、文档创建、多维表格导入、会议纪要总结等。
- **AI PPT 融入飞书 CLI**：在聊天里生成或多轮修改 PPT，卡片内可完整预览，并通过明确按钮上传云文档、发到群聊或发给同事。
- **多用户隔离**：每个 Web 账号有独立的 `lark-cli` HOME、授权状态和会话数据。
- **SQLite 存储**：账号、登录态、会话、消息、执行记录集中在一个数据库文件里。
- **OpenAI 兼容模型**：支持 Qwen、OpenAI、GLM、Doubao 等兼容接口。
- **开源友好**：Skill 文档、场景模板、计划预览、存储层已拆分，方便贡献。
- **一次部署，多人使用**：每个用户有独立的账号，互不影响。
- **专注于飞书CLI能力的Agent**：响应速度超快。
- **支持Docker部署**：docker compose一键部署到Docker服务。

## 支持的飞书能力

| 模块 | 能力 |
| --- | --- |
| IM | 用户/群搜索、发消息、群消息读取 |
| Calendar | 日程查询、忙闲推荐、会议创建、参会人邀请 |
| Contact | 联系人搜索、用户信息查询 |
| Doc / Wiki | 文档创建、检索、更新 |
| Drive | 文件上传、导入、下载、评论 |
| AI PPT / Slides | 生成 PPT、修改 PPT、逐页预览、下载 PPTX、上传云文档、发送到群聊或联系人 |
| Base | 多维表格、字段、记录、视图、仪表盘、工作流 |
| Sheets | 电子表格读写、样式、过滤视图、导出 |
| Task | 任务、任务清单、提醒、评论 |
| More | Mail、Minutes、VC、Whiteboard、Approval、Attendance、Event 等 |

## 技术栈（依赖包）

- Backend：FastAPI、Pydantic、SQLite、OpenAI SDK、Anthropic SDK、PyYAML、python-pptx、Pillow
- Frontend：Vue 3、TypeScript、Vite、SSE
- Runtime：官方 `@larksuite/cli`

## 快速开始

### 1. 准备环境

需要：

- Python 3.10+
- Node.js 16+
- npm / npx
- 一个 OpenAI 兼容模型 API Key

建议先确认版本：

```bash
python --version
node --version
npm --version
```

Windows 如果 `python` 不可用，可以把后续命令里的 `python` 换成 `py -3`。

可选：提前安装官方飞书 CLI。Linux/macOS 如果全局安装遇到权限问题，可以使用 Node 版本管理器，或按终端提示加 `sudo`。

```bash
npm install -g @larksuite/cli
npx skills add larksuite/cli -y -g
```

如果没有提前安装，页面首次授权时也会提示安装。

### 2. 进入项目目录

```bash
cd feishu-cli-web
```

### 3. 配置后端

推荐使用虚拟环境，避免污染系统 Python。

Linux/macOS：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

复制环境变量文件：

Linux/macOS：

```bash
cd ..
cp .env.example .env
```

Windows PowerShell：

```powershell
cd ..
Copy-Item .env.example .env
```

编辑 `.env`：

```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
OPENAI_API_KEY=your_api_key_here
APP_NAME=Feishu CLI Web
API_PREFIX=/api/v1
LARK_CLI_COMMAND_TIMEOUT=120

VITE_API_TARGET=http://后端地址:8000
```
#### 如果某个人的后端不在本机，比如跑在服务器、Docker、WSL 或另一台机器上，就让他自己在 frontend/.env 里配置：

VITE_API_TARGET=http://后端地址:8000

### 4. 启动后端

确保当前目录是 `backend`，并且虚拟环境已激活。

```bash
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. 启动前端

```bash
cd frontend
npm ci
npm run dev
```

访问：

- Web：http://localhost:3000
- API Docs：http://localhost:8000/docs

开发模式下，Vite 会把 `/api` 自动代理到后端。默认目标是：

```text
http://127.0.0.1:8000
```

如果后端不在本机，例如跑在另一台服务器或局域网机器上，可以临时覆盖：

Linux/macOS：

```bash
VITE_API_TARGET=http://192.168.1.10:8000 npm run dev
```

Windows PowerShell：

```powershell
$env:VITE_API_TARGET="http://192.168.1.10:8000"
npm run dev
```

如果你没有提交 `package-lock.json`，可以把 `npm ci` 换成 `npm install`。当前仓库已经包含 lock 文件，优先用 `npm ci` 可以保证不同机器安装结果一致。

启动后可以先做一次最小检查：

```bash
curl http://127.0.0.1:8000/health
```

返回 `{"status":"ok"}` 说明后端已启动。然后打开 Web，使用默认账号登录，再按页面提示连接飞书。

### 6. 生产构建

```bash
cd frontend
npm ci
npm run build
```

把 `frontend/dist/` 交给 Nginx、Caddy 或其它静态文件服务。生产环境需要把 `/api` 反向代理到后端，例如 `http://127.0.0.1:8000`。

## Docker 部署

仓库内置单容器部署方式：构建阶段会打包前端，运行阶段由 FastAPI 同时提供 API 和前端页面。容器内会安装 Node.js、npm、Git、官方 `@larksuite/cli` 和飞书 CLI skills。

### 1. 准备 `.env`

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填入模型配置：

```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
OPENAI_API_KEY=your_api_key_here
API_PREFIX=/api/v1
LARK_CLI_COMMAND_TIMEOUT=120
```

### 2. 使用 Docker Compose 启动

```bash
docker compose up -d --build
```

访问：

- Web：http://localhost:8000
- API Docs：http://localhost:8000/docs
- Health：http://localhost:8000/health

查看日志：

```bash
docker compose logs -f
```

停止服务：

```bash
docker compose down
```

运行期数据会保存在 Docker volume `feishu-cli-web_feishu_cli_data` 中，对应容器内目录：

```text
/app/.feishu_cli_data
```

如果要同时删除数据卷：

```bash
docker compose down -v
```

### 3. 只用 Dockerfile

```bash
docker build -t feishu-cli-web .
docker run -d \
  --name feishu-cli-web \
  --env-file .env \
  -p 8000:8000 \
  -v feishu_cli_data:/app/.feishu_cli_data \
  feishu-cli-web
```

Windows PowerShell：

```powershell
docker build -t feishu-cli-web .
docker run -d `
  --name feishu-cli-web `
  --env-file .env `
  -p 8000:8000 `
  -v feishu_cli_data:/app/.feishu_cli_data `
  feishu-cli-web
```

### 4. Docker 常见问题

- 构建时下载依赖失败：确认服务器可以访问 npm registry、PyPI、GitHub 和飞书 CLI 相关包；公司网络下通常需要配置 Docker 代理。
- `Failed to clone repository` 或 `spawn git ENOENT`：说明镜像里缺少 Git。请确认使用的是最新 Dockerfile，里面会安装 `git`。
- `docker compose` 不存在：新版 Docker Desktop 自带 `docker compose`；旧版本可能是 `docker-compose`。
- 授权后重启丢失：确认已经挂载 `/app/.feishu_cli_data` 数据卷。
- 前端可以打开但 API 失败：Docker 单容器模式下前端和 API 都在 `8000` 端口，通常不需要额外反向代理；如果放到 Nginx 后面，请把 `/api`、`/docs`、`/openapi.json` 转发到容器。
- 需要备份数据：备份 Docker volume 中的 `.feishu_cli_data`，重点是 `feishu_cli_web.sqlite3` 和 `lark_cli_users/`。

## 初始化管理员

不再自动创建固定密码的演示账号。新空库可以在受保护的环境配置中设置
`AUTH_BOOTSTRAP_ACCOUNT` 和自行生成的 `AUTH_BOOTSTRAP_PASSWORD`（至少 12 位），
首次认证请求时原子地初始化一名管理员；初始化后应移除该密码配置。
留空则不自动创建密码账号，可使用账号管理脚本预置管理员。

既有账号和飞书绑定不会被删除。旧 SHA-256 密码在成功登录后升级为随机盐 PBKDF2；
这不等于弱密码已经变强，已有演示账号仍须由管理员更换密码。
密码登录按账号和来源地址限流，触发时返回 429 和 Retry-After。

## SQLite 与账号管理

项目默认使用 SQLite 保存运行期数据，不需要额外安装数据库服务。数据库文件会在首次启动后自动创建：

```text
.feishu_cli_data/feishu_cli_web.sqlite3
```

主要数据表：

- `accounts`：Web 登录账号和密码哈希
- `auth_sessions`：Web 登录 token
- `chat_sessions` / `chat_messages`：聊天会话和消息
- `execution_records`：飞书 CLI 执行记录
- `profile_states`：每个 Web 账号对应的飞书授权状态

本地开发和团队部署都应自行设置账号密码，可用脚本批量维护账号，不要手改数据库。

### 批量新增或更新用户

编辑：

```text
backend/data/users_upsert.json
```

示例：

```json
{
  "users": [
    {
      "account": "demo",
      "name": "Demo User",
      "password": "replace-with-a-unique-password"
    }
  ]
}
```

执行：

```bash
cd backend
python data/manage_users.py --add-file data/users_upsert.json
```

密码必须为 12–1024 个字符，请自行生成。相同 `account` 已存在时，脚本会更新昵称和密码并撤销旧的网站登录会话。

### 批量删除用户

编辑：

```text
backend/data/users_delete.json
```

示例：

```json
{
  "accounts": [
    "demo"
  ]
}
```

执行：

```bash
cd backend
python data/manage_users.py --delete-file data/users_delete.json
```

删除用户时会同步清理该账号的 Web 登录态、聊天记录、执行记录和飞书授权状态。默认不会删除隔离的官方 `lark-cli` HOME 目录；如果确认不再需要该用户的本地飞书授权缓存，可以加参数：

```bash
python data/manage_users.py --delete-file data/users_delete.json --purge-cli-data
```

### 常用维护命令

```bash
# 查看当前账号
python data/manage_users.py --list

# 预览新增/更新，不写入数据库
python data/manage_users.py --add-file data/users_upsert.json --dry-run

# 预览删除，不写入数据库
python data/manage_users.py --delete-file data/users_delete.json --dry-run

# 指定其它 SQLite 文件
python data/manage_users.py --db ../.feishu_cli_data/feishu_cli_web.sqlite3 --list
```

这些命令也可以从项目根目录运行：

```bash
python backend/data/manage_users.py --add-file backend/data/users_upsert.json
python backend/data/manage_users.py --delete-file backend/data/users_delete.json
```

也可以使用 DB Browser for SQLite、DBeaver、DataGrip 等 SQLite 工具查看数据，但不建议直接改 `password_hash`、`auth_sessions` 或飞书授权相关字段。新增、改密、删除账号优先使用脚本，避免状态不一致。

## 飞书授权

系统支持企业共享应用和传统 CLI 两种授权模式。团队部署推荐企业共享应用：
公司只维护一个飞书应用，每位员工仍使用自己的飞书身份和个人授权。

### 企业共享应用模式

服务器配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_TENANT_KEY`、
`FEISHU_REDIRECT_URI` 和稳定的 `FEISHU_TOKEN_ENCRYPTION_KEY` 后，登录页会显示
「使用企业飞书登录」。飞书返回的租户和 `open_id` 会绑定到唯一网站账号；手机、
电脑等每台设备获得独立的网站会话，但复用服务器端该账号的加密个人授权。

网站会话默认保留 30 天，可用 `AUTH_SESSION_DAYS` 调整。飞书访问令牌由服务器自动
刷新；新设备、撤销授权、刷新凭据失效或飞书要求重新确认时，仍可能出现一次登录或
授权页面。因此它是跨设备持续复用，不承诺浏览器永不过期，也不会把个人令牌复制到
设备。手机访问必须使用同一个可达的 HTTPS 域名，`127.0.0.1` 只适合本机测试。

企业模式会按员工、负责人、管理员角色检查命令和权限。当前 50 个内置模板中，
管理员/负责人开放 18 个已审核模板，员工开放 17 个；其余模板会标记为
「企业未开放」，在生成提示词和执行命令前拒绝。详细部署边界见
`design/enterprise-deployment.md`，场景映射见
`design/enterprise-scenario-policy.md`。

### 传统 CLI 模式

未配置企业应用时，登录后如果当前账号还没有完成飞书 CLI 初始化或授权，页面会显示
「连接飞书账号」卡片。

授权流程会做这些事：

1. 检查 `lark-cli` 是否可用
2. 为当前 Web 用户准备独立 CLI 环境
3. 生成飞书授权链接
4. 等待用户在浏览器中完成授权
5. 保存该 Web 账号的独立授权状态

侧边栏提供「重新授权飞书」入口。重新授权会分两步执行：

1. 执行官方 `lark-cli auth logout`，并清除当前 Web 用户隔离环境中的旧授权缓存
2. 按首次连接飞书账号的同一流程生成新的授权链接，并等待用户完成登录

它不会强制重装 CLI，也不会重建已有应用配置。

## AI PPT 与飞书协作

AI PPT 是飞书 CLI 工作台中的一个内置能力，不是独立入口。用户仍然从聊天输入开始，例如：

```text
帮我做一份项目周报 PPT，突出进度、风险和下周计划
把刚才的 PPT 改得更适合发给管理层，减少技术细节，增加结论页
```

系统会生成可下载的 PPTX，并在回复卡片中展示 PPT 信息、页面列表和逐页预览。用户可以多轮修改，每一轮都会保留新的版本，并且可以点击「完整预览」查看整份 PPT。

PPT 完成后不会自动猜测用户要发给谁，而是在 PPT 卡片里提供明确按钮：

- 「上传云文档」：上传 PPTX 到飞书云文档，可填写文件夹 token 或 Wiki 节点 token。
- 「发到群」：填写群聊名称，系统通过飞书 CLI 搜索群聊并发送 PPTX。
- 「发给同事」：填写联系人姓名，系统通过飞书 CLI 搜索用户并发送 PPTX。

这样可以把制作、修改、查看和分发都放在同一张 PPT 卡片里，用户每次点击按钮才会执行对应飞书操作。发送或上传失败时，卡片会显示错误；如果是缺少飞书权限，会出现补充授权卡片，完成授权后回到原 PPT 卡片重新点击即可。

### PPT 相关权限

如果要使用「上传云文档」「发到群」「发给同事」，飞书开放平台中的应用需要配置相应用户权限，并让用户重新授权。常见权限包括：

```text
im:resource:upload
im:resource
```

如果发送群文件时报错 `缺少权限 im:resource:upload, im:resource`，说明当前飞书应用还没有这些权限，或当前用户尚未重新授权。请先在飞书开放平台补充权限，再通过页面里的补充授权卡片完成授权。

## 自然语言理解与工作流记忆

默认使用 `LARK_AGENT_ENGINE=langgraph`，保留 `legacy` 作为旧执行器的回退配置。

1. 普通请求直接进入模型工具循环，不再由关键词选择业务流程，也不预先生成完整命令计划。
2. 模型发现统一审核目录中的能力，按需读取本机 CLI 参数定义，用结构化参数调用并根据真实结果决定下一步。
3. 读取操作直接执行；写操作展示具体参数并暂停。确认后从持久化步骤继续，重新检查用户权限，不重新规划已确认的操作。
4. LangGraph SQLite 检查点保存任务状态；账号、会话与运行 ID 共同限定恢复范围。写操作执行凭据避免在恢复时重复发送，结果不确定时不会自动重放。
5. 日期和周期由模型解析为结构化定时工具参数；当前支持一次、每天、工作日。创建计划需要确认。
6. 模型服务异常、参数错误、权限不足与用户信息不完整分别处理。模型服务异常可输入「继续」恢复。

成功结果会形成当前网站账号专属的工作流记忆：

- 第一次成功只创建「候选经验」；用户可在回复卡片或「账号与权限」中手动记住。
- 同类流程第二次独立成功后自动变成「已记住」。
- 新执行器向模型提供当前账号最多 20 条启用的经验，由模型判断适用性，不按措辞相似度阻断发现，也不直接复用旧命令。
- 记忆保存操作方法、命令形状和成功计数。动态事实重新查询，经验不能赋予权限或替代写操作确认。
- 旧执行器保留单条经验的失败停用逻辑；新执行器提供多条经验参考，目前不把一次失败自动归因于某一条经验。
- 记忆保存在服务器 SQLite 中，因此同一个网站账号在手机和电脑登录后看到的是同一组经验；不同账号完全隔离。

当前账号可通过 API 管理自己的记忆：

```bash
curl -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/workflow-memories
curl -X POST -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/workflow-memories/1/activate
curl -X DELETE -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/workflow-memories/1
```

## 定时任务

当用户提出明显的定时需求时，系统会把它识别为后台定时任务，而不是立即执行飞书写操作。

支持的常见表达：

```text
每天上午9点帮我给飞书CLI测试群发一条消息：请大家填写日报
每日 18:00 帮我总结【项目群】今天的消息
每个工作日 18:00 查看【项目群】未回应的阻塞事项并提醒我
明天上午10点帮我给张三发消息：记得参加评审会
2026年4月25日 9点帮我创建项目复盘文档
```

定时任务闭环：

1. 用户输入定时需求
2. 系统生成执行计划预览，标记为“创建定时任务”
3. 用户点击“确认执行”
4. 系统把任务写入 SQLite 的 `scheduled_tasks` 表
5. 后端调度器到点自动执行飞书任务
6. 执行结果写回当前会话和执行记录
7. 一次性任务执行后变为 `completed`；每日或工作日任务会自动计算下一次执行时间

为了避免误判，系统只把带有明确时刻的请求识别为定时任务，例如 `9点`、`09:30`、`下午三点`。像“明天找一个 1 小时空闲时间”这类日程规划请求不会被当成后台定时任务。

输入框上方提供「定时任务」面板：

- 全局开关：关闭后不会创建新的定时任务，后台调度器也不会执行已有任务；再次开启后，处于 `active` 状态且到期的任务会继续执行。
- 调度配置：可以调整后台轮询间隔，配置会写入 SQLite 并在下一轮调度生效。
- 任务列表：只显示当前登录账号已添加的定时任务，避免不同账号之间互相看到任务内容。
- 单任务开关：每个任务可以单独暂停或恢复，适合临时停用某个重复任务。
- 删除保护：已暂停、已完成或已失败的任务，前端二次确认后可以删除；待执行和正在执行的任务禁止直接删除。

`.env` 支持配置定时任务默认值：

```env
SCHEDULED_TASKS_ENABLED=true
SCHEDULED_TASK_POLL_SECONDS=30
```

说明：

- `SCHEDULED_TASKS_ENABLED` 只决定首次启动或 SQLite 中还没有运行时配置时的默认开关。
- 页面上的全局开关会写入 SQLite 的 `system_settings` 表，重启服务后仍然生效。
- `SCHEDULED_TASK_POLL_SECONDS` 是后台调度轮询间隔，建议保持 `30` 秒；系统会限制在 `5` 到 `3600` 秒之间。

可以通过 API 查看和管理当前账号的定时任务：

```bash
curl -H "X-Auth-Token: <login_token>" "http://127.0.0.1:8000/api/v1/scheduled-tasks"
curl -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/scheduled-tasks/config
curl -X POST -H "Content-Type: application/json" -H "X-Auth-Token: <login_token>" \
  -d '{"enabled":false,"poll_seconds":30}' http://127.0.0.1:8000/api/v1/scheduled-tasks/config
curl -X POST -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/scheduled-tasks/1/pause
curl -X POST -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/scheduled-tasks/1/resume
curl -X DELETE -H "X-Auth-Token: <login_token>" http://127.0.0.1:8000/api/v1/scheduled-tasks/1
```

## 数据目录

运行期数据默认集中在：

```text
.feishu_cli_data/
  feishu_cli_web.sqlite3
  lark_cli_profiles/
  lark_cli_users/
```

说明：

- `feishu_cli_web.sqlite3`：账号、登录 token、聊天会话、消息、执行记录、profile 状态
- `feishu_grants` / `feishu_identities`：企业模式下的加密个人授权和已验证身份绑定
- `scheduled_tasks` 表：后台定时任务、下次执行时间、执行状态和最近执行结果
- `workflow_memories` 表：按网站账号隔离的候选/已记住工作流结构与验证计数
- `lark_cli_profiles/`：Web 侧 profile 状态
- `lark_cli_users/`：每个 Web 用户独立的官方 `lark-cli` HOME
- `enterprise.env`：可选的本地企业应用配置；包含机密，只能保存在服务器

不要提交或分享这些敏感数据：

```text
.env
.feishu_cli_data/
.lark_cli_profiles/
.lark_cli_users/
.auth_accounts.json
.auth_sessions.json
frontend/node_modules/
frontend/dist/
```

旧版本的 `.auth_accounts.json`、`.auth_sessions.json`、`.feishu_cli_data/sessions/*.json` 已被 SQLite 替代。

## API 概览

除 `/health` 和登录接口外，业务 API 需要携带登录 token。前端会自动处理；如果你直接调用 API，需要在请求头里加：

```text
X-Auth-Token: <login_token>
```

| API | 说明 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /api/v1/auth/login` | 登录 |
| `GET /api/v1/auth/me` | 当前账号 |
| `POST /api/v1/auth/feishu/start` | 发起企业飞书登录、连接或增量授权 |
| `GET /api/v1/auth/feishu/callback` | 校验 PKCE/state 并完成企业身份绑定或授权 |
| `GET /api/v1/admin/members` | 管理员查看成员、角色与飞书连接状态 |
| `PATCH /api/v1/admin/members/{member}` | 管理员修改角色或启停成员 |
| `DELETE /api/v1/admin/members/{member}/feishu` | 管理员断开成员的企业飞书授权 |
| `POST /api/v1/chat/plan` | 生成执行计划，不执行命令 |
| `POST /api/v1/chat` | 执行聊天请求，支持 SSE |
| `GET /api/v1/workflow-memories` | 当前账号的候选/已记住工作流 |
| `POST /api/v1/workflow-memories/{memory_id}/activate` | 手动激活当前账号的候选经验 |
| `DELETE /api/v1/workflow-memories/{memory_id}` | 停用并移除当前账号的工作流记忆 |
| `GET /api/v1/sessions` | 会话列表 |
| `GET /api/v1/scenarios` | 场景模板列表 |
| `POST /api/v1/scenarios/render` | 渲染场景模板 |
| `GET /api/v1/scheduled-tasks` | 当前账号定时任务列表 |
| `GET /api/v1/scheduled-tasks/config` | 定时任务全局配置 |
| `POST /api/v1/scheduled-tasks/config` | 开启或关闭全局定时任务 |
| `POST /api/v1/scheduled-tasks/{task_id}/pause` | 暂停定时任务 |
| `POST /api/v1/scheduled-tasks/{task_id}/resume` | 恢复定时任务 |
| `DELETE /api/v1/scheduled-tasks/{task_id}` | 删除已暂停的定时任务 |
| `GET /api/v1/models/config` | 模型配置 |
| `POST /api/v1/models/config` | 保存模型配置 |
| `GET /api/v1/lark/setup/status` | 飞书 CLI 状态 |
| `POST /api/v1/lark/setup/stream` | 初始化或重新授权飞书 CLI |
| `GET /api/v1/ai-ppt/templates` | AI PPT 模板列表 |
| `POST /api/v1/ai-ppt/source` | 上传用于改写或参考的 PPTX |
| `POST /api/v1/ai-ppt/templates` | 上传 AI PPT 模板 |
| `GET /api/v1/ai-ppt/files/{filename}` | 下载生成的 PPTX |
| `GET /api/v1/ai-ppt/files/{filename}/preview` | 获取 PPT 逐页预览 |
| `POST /api/v1/ai-ppt/actions` | 执行 PPT 相关飞书操作，例如上传云文档、发群、发给联系人 |

## 部署检查清单

上线前建议逐项确认：

- 后端使用 Python 3.10+，并通过 `python -m pip install -r backend/requirements.txt` 安装依赖。
- 前端使用 Node.js 16+，并通过 `npm ci` 安装依赖。
- `.env` 已配置模型 API Key、模型名称和 API 地址。
- 如需启用定时任务，确认 `.env` 中 `SCHEDULED_TASKS_ENABLED=true`，并在页面「定时任务」面板中保持全局开关开启。
- `.feishu_cli_data/` 所在目录对后端进程可写。
- 服务器能执行 `npm`、`npx` 和 `lark-cli`。如果没有预装 `lark-cli`，首次授权页面会引导安装。
- 如需使用 PPT 分发能力，确认飞书开放平台应用已配置 `im:resource:upload`、`im:resource` 等所需权限，并完成重新授权。
- 生产环境已经把前端 `/api` 反向代理到后端。
- 默认账号密码已经修改，或已通过 `backend/data/manage_users.py` 新建正式账号并删除演示账号。
- 企业模式使用同一个可达 HTTPS 域名，飞书后台已精确登记
  `FEISHU_REDIRECT_URI`，所有实例共享数据库、加密密钥和持久化数据目录。
- 用 `curl http://127.0.0.1:8000/health` 检查后端健康状态。
- 用浏览器打开前端并完成一次登录，确认 `/api/v1/auth/me` 不再返回 401。

常见问题：

- `python: command not found`：Windows 使用 `py -3`，Linux/macOS 确认已安装 Python 3.10+。
- `npm ci` 失败：先确认 Node.js 版本；如果 lock 文件被删除，改用 `npm install`。
- `lark-cli command was not found`：运行 `npm install -g @larksuite/cli`，然后重启后端进程。
- Linux/macOS 全局安装 npm 包权限不足：使用 Node 版本管理器，或按系统策略使用 `sudo npm install -g @larksuite/cli`。
- SQLite 报只读或 I/O 错误：确认 `.feishu_cli_data/` 目录存在且后端进程有写权限。
- 前端能打开但接口 404 或跨域失败：开发环境确认 `frontend/vite.config.ts` 的 `VITE_API_TARGET` 指向后端；生产环境确认 Nginx/Caddy 已转发 `/api`。
- PPT 能生成但不能发到群或发给同事：优先检查飞书应用权限和用户授权；补充权限后回到原 PPT 卡片重新点击发送按钮。

## 项目结构

```text
Feishu-CLI-Web/
  backend/
    data/                     # SQLite account maintenance scripts and JSON examples
    app/
      api/routes/              # FastAPI routes
        ai_ppt.py              # AI PPT generation, preview and Feishu action APIs
      core/                    # SQLite, sessions, templates, records
      assets/ai_ppt_templates/ # Built-in PPT templates
      skills/ai_ppt/           # PPT generation and modification skill
      skills/lark_cli/
        skills/                # Skill markdown docs and references
        plan_preview.py        # dry-run plan preview
        skill_runtime.py       # Lark CLI runtime orchestrator
    requirements.txt
  frontend/
    src/components/            # Vue components
    src/lib/                   # frontend helpers
    package.json
  doc/                         # screenshots, videos, sharing docs
  .env.example
  README.md
```

## 如何扩展

### 增加场景模板

编辑：

```text
backend/app/core/scenario_templates.py
```

适合沉淀团队常用流程，例如：

- 创建项目周会
- 发送发布通知
- 导入销售数据到多维表格
- 从会议纪要生成任务

### 增加飞书 Skill

在下面目录新增 Skill：

```text
backend/app/skills/lark_cli/skills/
```

每个 Skill 使用 `SKILL.md` 描述能力、命令、约束和示例。复杂能力可以在 `references/` 中补充更多文档。

### 运行时模块

- `backend/app/core/storage.py`
- `backend/app/core/local_sessions.py`
- `backend/app/core/scenario_templates.py`
- `backend/app/core/execution_records.py`
- `backend/app/skills/lark_cli/plan_preview.py`


## 贡献

欢迎 Issue 和 PR

## License

[MIT](LICENSE)
