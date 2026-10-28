# caishu 单机部署

入口为 `https://flowing.caishu.site/`。FastAPI 同时提供前端和 API，容器仅监听服务器的 `127.0.0.1:8000`，由现有 Cloudflare Tunnel 提供 HTTPS。

## 目录与启动

服务器使用下面的目录。将本目录的 `compose.yml` 放到 `/opt/flowing/compose.yml`，在该目录执行 Compose 命令。

```text
/opt/flowing/
├── compose.yml
├── .env                 # 仅含 FLOWING_IMAGE=<本次镜像标签>
├── config/.env          # 应用与模型配置，0600，挂载到 /app/.env
├── current -> releases/<发布版本>
└── releases/<发布版本>/ # 源码与 Dockerfile
```

```bash
cd /opt/flowing
docker compose -f compose.yml build flowing
docker compose -f compose.yml up -d flowing
curl --fail http://127.0.0.1:8000/health
docker compose -f compose.yml ps
```

`config/.env` 由应用直接读取，避免将模型配置固定在容器环境变量里；管理页面修改后可持久化。新空库需临时设置 `AUTH_BOOTSTRAP_ACCOUNT` 和至少 12 位的随机 `AUTH_BOOTSTRAP_PASSWORD`。管理员建立后清空初始密码变量并重建容器；原账号保留在数据卷中。

当前 Dockerfile 内置 Node 22、官方 CLI 及技能，CLI／技能安装器版本可通过构建参数 `LARK_CLI_VERSION`／`SKILLS_CLI_VERSION` 更新。

## 数据与恢复

- `flowing_caishu_data`：账号、会话、检查点、任务、CLI 配置和飞书授权。
- `flowing_caishu_ppt`：上传、模板、预览和生成的 PPT。
- `/opt/flowing/config/.env`：模型配置与加密密钥。

备份时将这三部分作为同一组保护；需要一致的数据库集合时先暂停本应用写入。不要使用 `docker compose down -v`，不要覆盖 Nook 或其他应用目录。

更新前保留旧镜像标签、源码目录与数据备份。应用回退只需将 `/opt/flowing/.env` 的 `FLOWING_IMAGE` 改回旧标签，再执行 `docker compose -f compose.yml up -d --no-build flowing`。Tunnel 故障时，通过 SSH 恢复该次发布保存的 `/etc/cloudflared/config.yml` 备份；若同时更新了 `/root/.cloudflared/config.yml` 副本，也恢复它的对应备份，再重新启动 `cloudflared`。该恢复不依赖网站本身可访问。

首次登录后，点击左下角「重新授权」，按向导连接个人飞书；「账号与权限」管理私人机器人与工作流记忆。部署不迁移开发机的私人账号、会话或 OAuth 授权，也不会代替用户批准飞书写操作。
