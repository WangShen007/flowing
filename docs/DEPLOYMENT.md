# 部署与账号管理

本文说明飞序 Flowing 的运行方式、环境变量、飞书授权和运行数据维护。最短路径是 Docker Compose；本地部署适合开发或需要自行管理进程的环境。

## 运行模型

生产镜像分两阶段构建：Vite 先生成 `frontend/dist/`，FastAPI 再在同一个容器中提供 API 和网页。后端启动时还会运行本地定时任务和私人渠道轮询器，因此默认按单主机、单写入实例部署。

运行期数据默认位于 `.feishu_cli_data/`，包括网站账号、会话、执行记录、检查点、定时任务和飞书 CLI 的隔离目录。不要把这个目录或 `.env` 提交到仓库。

## 环境变量

从项目根目录复制配置样例：

```sh
cp .env.example .env
```

启动前至少配置模型：

| 变量 | 说明 |
| --- | --- |
| `LLM_PROVIDER` | `openai` 或 `anthropic`。默认使用 OpenAI 兼容接口。 |
| `OPENAI_API_KEY` | OpenAI 兼容服务的密钥。使用 Anthropic 时填写 `ANTHROPIC_API_KEY`。 |
| `OPENAI_BASE_URL` | 兼容服务地址，例如通义千问兼容接口。 |
| `LLM_MODEL` | 支持工具调用的模型名称。 |
| `LARK_AGENT_ENGINE` | 默认 `langgraph`；`legacy` 仅用于兼容旧流程。 |
| `LARK_CLI_COMMAND_TIMEOUT` | 单次 CLI 操作超时，默认 30 秒。 |
| `SCHEDULED_TASKS_ENABLED` | 新数据目录的定时任务默认开关。页面设置会保存到 SQLite。 |
| `SCHEDULED_TASK_POLL_SECONDS` | 调度轮询间隔，后端限制在 5–3600 秒。 |
| `FEISHU_CLI_DATA_DIR` | 自定义运行数据目录；容器部署通常使用 `/app/.feishu_cli_data`。 |
| `FRONTEND_DIST_DIR` | 已构建前端目录；单容器镜像使用 `/app/frontend/dist`。 |

同源部署请保持 `CORS_ALLOWED_ORIGINS` 为空。分域部署时填写逗号分隔的完整 `http(s)` origin，不要使用通配符。

## Docker Compose

在项目根目录执行：

```sh
docker compose up -d --build
docker compose logs -f flowing
```

访问 `http://localhost:8000`，并用下面的接口确认服务已就绪：

```sh
curl --fail http://127.0.0.1:8000/health
```

停止容器但保留数据：

```sh
docker compose down
```

`docker-compose.yml` 将数据挂载到 `/app/.feishu_cli_data`。`docker compose down -v` 会删除 Compose 管理的数据卷及其中的数据，只有在确认不再需要时才执行。

只使用 Dockerfile 时：

```sh
docker build -t flowing:latest .
docker run -d --name flowing \
  --env-file .env \
  -p 8000:8000 \
  -v flowing_data:/app/.feishu_cli_data \
  flowing:latest
```

## 本地运行

以下命令从项目根目录执行。Python 3.10 以上受支持，建议使用 Python 3.12；Node.js 20 与 npm 用于构建前端和安装官方 CLI：

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
npm install -g @larksuite/cli
npx skills add larksuite/cli -y -g
```

启动后端并提供已构建网页：

```sh
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

本地开发可让后端和 Vite 分开运行。终端一：

```sh
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

终端二（项目根目录）：

```sh
npm --prefix frontend run dev
```

Vite 默认把 `/api` 代理到 `http://127.0.0.1:8000`。后端在其他主机时，设置 `frontend/.env` 中的 `VITE_API_TARGET`，例如：

```dotenv
VITE_API_TARGET=http://192.168.1.10:8000
```

开发网页地址为 `http://localhost:3000`；单容器或本地生产构建地址为 `http://127.0.0.1:8000`。

## 初始管理员

项目不创建通用默认密码。新空库可以在受保护的环境中设置：

```dotenv
AUTH_BOOTSTRAP_ACCOUNT=admin
AUTH_BOOTSTRAP_PASSWORD=自行生成的至少12位密码
```

首次认证请求会原子地创建一名管理员；已有账号时这两个变量不会覆盖现有数据。成功登录后从运行环境移除 `AUTH_BOOTSTRAP_PASSWORD` 并重启服务。留空则不自动创建密码账号，可使用账号管理脚本预置账号。

批量维护账号：

```sh
# 从项目根目录运行，先编辑 backend/data/users_upsert.json
backend/.venv/bin/python backend/data/manage_users.py \
  --add-file backend/data/users_upsert.json

# 预览删除而不写入
backend/.venv/bin/python backend/data/manage_users.py \
  --delete-file backend/data/users_delete.json --dry-run
```

账号密码长度必须为 12–1024 个字符。删除账号会清理网站会话、聊天、执行记录、定时任务和授权绑定；需要同时删除该账号隔离的 CLI 缓存时，显式增加 `--purge-cli-data`。

## 企业登录

未配置企业应用时，用户登录后在账号设置中连接自己的官方飞书 CLI。每个网站账号拥有独立的 CLI 环境和授权状态。

团队部署可以配置企业共享应用，让员工使用各自的飞书身份：

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=服务器密钥
FEISHU_TENANT_KEY=租户标识
FEISHU_REDIRECT_URI=https://flowing.example.com/api/v1/auth/feishu/callback
FEISHU_TOKEN_ENCRYPTION_KEY=32字节密钥的URL-safe Base64
```

可用下面的命令生成加密密钥：

```sh
python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

飞书开放平台的回调地址必须与 `FEISHU_REDIRECT_URI` 完全一致；生产环境使用 HTTPS，本机测试可使用 `http://127.0.0.1` 或 `http://localhost`。令牌由服务端加密保存，设备只得到网站会话。管理员角色仍受企业场景清单和个人授权范围限制，不会自动获得组织内全部数据。

## 数据持久化与备份

主要文件：

```text
.feishu_cli_data/
├── feishu_cli_web.sqlite3       # 账号、会话、任务与执行记录
├── agent_checkpoints.sqlite3    # LangGraph 恢复检查点
├── lark_cli_users/              # 每个网站账号的 CLI HOME
├── lark_cli_profiles/           # CLI profile 状态
├── token_locks/                 # 刷新令牌时的进程锁
└── enterprise.env               # 可选的服务端企业配置
```

PPT 产物位于 `backend/app/data/ai_ppt/`，同样需要纳入受控备份。SQLite 快照脚本只创建新文件，不覆盖已有目标：

```sh
mkdir -p backups
backend/.venv/bin/python backend/data/sqlite_snapshot.py \
  --source .feishu_cli_data/feishu_cli_web.sqlite3 \
  --destination backups/feishu_cli_web.sqlite3
```

备份前应暂停写入，或至少为每个数据库分别使用脚本并校验输出。备份 `.feishu_cli_data/` 时同时保护企业加密密钥和 `.env`；它们与数据库具有相同的敏感级别。

## 反向代理与扩容

生产访问建议在 Nginx、Caddy 或云负载均衡器后使用 HTTPS。单容器模式下 `/api`、`/docs`、`/openapi.json` 和网页都由 8000 端口提供；反向代理应保持这些路径和 SSE 长连接，不要缓存认证接口或事件流。

SQLite、定时任务和私人渠道使用本地文件锁与租约选择写入实例。不要让多个主机或 NFS 目录共享同一数据目录，也不要在没有集中式数据库和队列前直接水平扩容。需要多实例时，应先设计租约、任务去重、检查点和令牌刷新的一致性方案。

## 运维检查

部署后按顺序检查：

1. `curl --fail http://127.0.0.1:8000/health` 返回 `{"status":"ok"}`。
2. 浏览器可以打开登录页，且 `/api/v1/auth/me` 在未登录时返回 401。
3. 使用初始化账号或脚本账号登录，完成 CLI 或企业飞书授权。
4. 先发送读取请求，再用一个明确的写请求确认页面会展示操作卡片。
5. 如启用定时任务，创建一次短期测试任务并在面板中确认其状态。
6. 如启用私人渠道，检查 `账号与权限` 中的后台心跳和绑定状态；详细协议见[私人消息入口](PRIVATE-BOT-CHANNELS.md)。

常见故障：

- **页面能打开但 API 失败**：开发模式检查 `VITE_API_TARGET`；生产模式检查反向代理是否转发 `/api` 和 SSE。
- **无法登录**：确认账号存在、密码长度符合要求，并检查是否触发登录限流；初始化变量只对空库生效。
- **CLI 未找到**：重新执行 `npm install -g @larksuite/cli` 与 `npx skills add larksuite/cli -y -g`，再重启后端。
- **授权或任务在重启后消失**：确认 `.feishu_cli_data` 已挂载且进程对目录有读写权限。
- **PPT 无法上传或发送**：检查飞书应用的 `im:resource:upload`、`im:resource` 等权限，并在页面完成补充授权。
