# 开发与接口

本文面向需要修改飞序 Flowing、补充飞书能力或运行回归检查的开发者。产品入口是 Vue 3 + Vite，后端是 FastAPI；聊天执行使用 LangGraph，SQLite 保存业务状态和恢复检查点。

## 开发环境

建议使用 Python 3.12、Node.js 20 和 npm。项目根目录执行：

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
backend/.venv/bin/python -m pip install pytest ruff
npm --prefix frontend ci
```

复制 `.env.example` 为 `.env`，模型密钥可以先留空以运行不需要模型的接口测试。启动开发服务：

```sh
# 终端一：API 和 SSE
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 终端二：Vite（在项目根目录执行）
npm --prefix frontend run dev
```

Vite 的 `/api` 代理默认指向 `http://127.0.0.1:8000`；跨主机调试时在 `frontend/.env` 设置 `VITE_API_TARGET`。

## 代码地图

```text
backend/app/
├── api/routes/                 # HTTP、SSE、认证和资源接口
├── core/                       # SQLite、会话、权限、调度、私人渠道
├── skills/lark_cli/
│   ├── agent_graph.py          # 默认 LangGraph 工具循环与中断恢复
│   ├── tool_catalog.py         # 能力目录、参数定义和写操作判定
│   ├── skill_runtime.py        # CLI 执行、旧流程兼容和结果整理
│   └── skills/                 # 飞书能力说明与参数参考
└── skills/ai_ppt/              # PPT 生成、修改和预览
frontend/src/
├── App.vue                     # 应用状态与页面布局
├── components/                 # 聊天、确认、账号、模板和渠道组件
└── lib/                        # API、认证、Markdown 和 PPT 资源客户端
```

一次聊天请求的大致路径是：认证账号 → 创建或恢复会话 → 选择执行技能 → 读取能力定义 → 执行读取或暂停等待确认 → 记录结果 → 返回 SSE。LangGraph 的检查点、账号和会话 ID 一起限定恢复范围；写操作在恢复前会再次检查权限，结果不明确时不会自动重放。

## 执行器边界

`LARK_AGENT_ENGINE=langgraph` 是默认值。它使用模型原生工具调用，根据真实返回结果决定下一步，并把授权和写入确认作为可恢复中断。`legacy` 保留给兼容旧行为的部署和测试；两种执行器的计划格式与定时任务识别路径不同，新增功能应优先接入 LangGraph 工具目录，并为兼容路径补充测试。

工具目录中的操作必须先经过 `describe_tool` 取得当前 CLI 参数定义，再由 `invoke_tool` 以结构化参数执行。目录、参数、飞书返回和文档内容都属于不可信输入，不能覆盖系统规则。新增写操作时同时确认：

- 参数摘要能在前端确认卡片中读懂，且不泄露不必要的密钥。
- 失败、授权中断、超时和送达不确定都能被明确区分。
- 恢复请求只属于原账号和会话，并且不会重复提交已送达的写操作。
- 结果引用真实返回值；规划、搜索或接口受理不能被写成“已完成”。

## 测试与检查

后端全量回归：

```sh
backend/.venv/bin/python -m pytest -q backend/tests
```

静态检查：

```sh
backend/.venv/bin/ruff check backend
```

前端类型检查和生产构建由同一命令完成：

```sh
npm --prefix frontend run build
```

浏览器回归脚本使用 Node 内置 CDP WebSocket 和模拟 API，不会向飞书发送真实消息。先启动一个允许远程调试的 Chrome/Chromium，取得 `webSocketDebuggerUrl`，再执行：

```sh
node frontend/tests/workflow-browser.mjs '<CDP WebSocket URL>'
```

脚本覆盖会话切换、并行请求、计划超时、授权恢复、写操作确认、工作流记忆、PPT 预览、企业登录、私人渠道以及桌面/手机宽度检查。需要真实飞书或真实模型验证时，另行使用 `backend/evals/` 和受控测试账号，不要把凭证放进 fixture。

## HTTP 接口

除健康检查和登录入口外，接口需要 `X-Auth-Token` 或 `Authorization: Bearer …`。主要路由如下：

| 路由组 | 用途 |
| --- | --- |
| `/api/v1/auth/*` | 密码登录、企业飞书 OAuth、当前账号和成员管理 |
| `/api/v1/chat` | SSE 聊天执行、计划和运行取消 |
| `/api/v1/sessions` | 会话列表、消息和执行记录 |
| `/api/v1/scenarios`、`/api/v1/templates` | 内置场景、团队模板及版本 |
| `/api/v1/scheduled-tasks` | 定时任务列表、暂停、恢复和全局配置 |
| `/api/v1/workflow-memories` | 当前账号的候选/已记住工作流 |
| `/api/v1/lark/setup`、`/api/v1/models` | CLI 授权与模型配置 |
| `/api/v1/ai-ppt/*` | PPT 上传、生成文件、逐页预览和飞书分发 |
| `/api/v1/bot-channels/*` | 微信/Telegram 绑定、轮询状态和解绑 |

完整参数以运行中的 `/docs` 和 `/openapi.json` 为准。新增接口时给路由加认证依赖，明确账号过滤条件，并为未授权、跨账号访问和输入边界增加测试。

## 扩展方式

### 增加飞书能力

1. 在 `backend/app/skills/lark_cli/skills/` 增加能力说明和参数参考。
2. 在工具目录审核操作属于读取还是写入，并声明必要权限。
3. 让模型工具 schema 与 CLI 的真实参数保持一致；不要猜测字段名或 ID。
4. 在 `backend/tests/` 覆盖参数校验、权限、确认、恢复和结果整理。
5. 更新 README 的能力概览或对应开发文档。

### 增加场景模板

内置模板位于 `backend/app/core/scenario_templates.py`；团队模板由 `template_store` 写入 SQLite。模板只负责组织用户输入，不应携带隐含授权、固定用户 ID 或绕过确认的命令。

### 增加前端状态

优先复用 `frontend/src/lib/` 的认证、SSE 和资源下载 helper。涉及写操作时保持确认卡片、加载中、失败、重试和窄屏布局完整；外部图片和 Markdown 继续经过现有安全处理。

## 数据与调试

测试默认使用临时 `FEISHU_CLI_DATA_DIR`，避免污染本地账号。调试真实实例时可查看：

```sh
curl --fail http://127.0.0.1:8000/health
curl -H "X-Auth-Token: <token>" http://127.0.0.1:8000/api/v1/auth/me
```

不要直接修改 `password_hash`、`auth_sessions`、令牌或检查点表。账号使用 `backend/data/manage_users.py`，数据库快照使用 `backend/data/sqlite_snapshot.py`。日志、`.env`、`.feishu_cli_data/`、PPT 产物和 CLI profile 都属于敏感运行数据。

## 提交前清单

- 修改路径有对应的后端测试或前端构建检查。
- 写操作仍显示具体目标、参数和确认按钮。
- 账号、会话、任务、记忆和文件资源按所有者隔离。
- SSE 结束、取消、超时和断线恢复不会遗留活动状态。
- 文档中的命令从标注的目录执行，链接和示例文件真实存在。
- `git diff --check`、`ruff check`、pytest 和前端构建均通过。
