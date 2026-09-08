# 飞书能力与权限验证矩阵

更新时间：2026-09-08  
适用版本：本机 `lark-cli 1.0.93`、当前网站 `COMMANDS`/`CAPABILITIES` 注册表

这份矩阵用于回答“网站现在能做什么、谁能做、缺什么证据”，不是授权后台的
替代品。可执行策略的唯一来源仍是
`backend/app/core/feishu_permissions.py`；模型只能从
`backend/app/skills/lark_cli/tool_catalog.py` 返回的审核目录中选择操作。

当前审核目录包含 59 个操作、27 类能力；其中 35 个操作是明确登记的只读操作，
其余操作都必须经过网站确认后才会执行。

## 如何读状态

| 标记 | 含义 |
| --- | --- |
| `P` | 已进入网站注册表，有角色和 scope 映射，并有静态契约测试 |
| `A` | 真实执行时仍受飞书资源 ACL、群成员关系、文档/表格共享范围约束 |
| `L` | 已有过特定路径的真实验证记录，但不代表所有账号、资源和参数都验证过 |
| `N` | 当前没有足够的真实飞书端到端证据；不能对用户承诺已可用 |
| `—` | 没有加入网站审核目录，模型不能直接执行 |

`P` 只证明“网站的边界和参数契约已经登记”，不等于飞书后台已给当前用户
授权，也不等于当前用户能访问任意资源。写操作必须经过网站确认，仍由飞书
接口最终校验 ACL。

## 角色总览

| 网站角色 | 能力数 | onboarding user scopes | 允许的额外能力 | 明确边界 |
| --- | ---: | ---: | --- | --- |
| `employee` | 25 | 45（含 `offline_access`） | 无 | 只能使用自己在飞书中可见/可编辑的资源 |
| `lead` | 27 | 47（含 `offline_access`） | 创建项目群、创建/管理项目任务清单 | 不能因网站角色绕过飞书资源 ACL |
| `admin` | 27 | 47（含 `offline_access`） | 与 `lead` 相同；任务管理还受后端配置的任务清单范围约束 | 不能自动读取他人的私有文档、群或日程 |

当前 `lead` 与 `admin` 的能力集合相同；管理员的额外权力主要体现在网站管理
接口和已配置任务清单的任务策略，不应被模型解释成“可以修改所有飞书资源”。
未知角色拒绝授权。网站角色也不会根据姓名、头衔或“老板”称呼自动升级。

所有业务命令都以当前用户的 user token 执行。网站不允许模型传入
`--as bot`、`--profile`、`--config`、`--app-id` 或 `--app-secret`。

## 当前已审核能力

| 能力 | 已注册操作（模型可见） | employee | lead/admin | user scopes（核心） | ACL/资源限制 | 证据与缺口 |
| --- | --- | --- | --- | --- | --- | --- |
| 文档读取 | `docs +fetch`, `docs +search` | P/A | P/A | `docx:document:readonly`, `search:docs:read` | 文档必须对当前用户可见 | P；读取曾有特定文档验证，其他文档为 N |
| 文档创建/更新 | `docs +create`, `docs +update` | P/A | P/A | `docx:document:create`, `docx:document:write_only`；部分资源另需条件 scope | 当前用户必须有目标文档/知识库 ACL | P；创建/读取有历史验证，更新各种内容形态为 N |
| 文件/多维表格导入 | `drive +import` | P/A | P/A | `docs:document.media:upload`, `docs:document:import`；部分知识库目标另需 `wiki:node:retrieve` | 本地文件、目标文件夹/知识库 ACL | P；各导入类型和目标 ACL 未完整 L |
| 群列表/搜索/群详情 | `im +chat-list`, `im +chat-search`, `im chats get` | P/A | P/A | `im:chat:read` | 当前用户必须是成员或对群可见 | P；群列表有真实记录，跨企业 ACL 为 N |
| 群成员读取 | `im +chat-members-list`, `im chat.members get` | P/A | P/A | `im:chat.members:read` | 群可见性、群安全配置；原生查询强制 `check_security_conf=true` | P；静态安全检查，真实不同群 ACL 为 N |
| 消息读取 | `im +chat-messages-list`, `im +messages-search` | P/A | P/A | `search:message`, `im:message.reactions:read`, `im:message.group_msg:get_as_user`, `im:message.p2p_msg:get_as_user` | 用户必须能看到对应会话和消息 | P；真实查询只覆盖特定账号/群 |
| 以本人身份发消息 | `im +messages-send` | P/A | P/A | `im:message.send_as_user`, `im:message` | 当前用户必须能向目标群/用户发消息；消息内容和 @ 用户必须有效 | P；此前遇到 scope 缺失并已补映射；当前账号重新授权及真实发送仍为 N |
| 联系人查找 | `contact +search-user` | P/A | P/A | `contact:user:search` | 企业目录可见范围、姓名同名歧义 | P；真实跨角色/外部联系人为 N |
| 日程查看 | `calendar +agenda`, `+get`, `+search-event`, `+list-attendees`, `+meeting`, `calendar events list/get` | P/A | P/A | `calendar:calendar.event:read` | 当前身份可访问的日历和日程 | P；具体日历 ACL 和重复日程为 N |
| 空闲时间/会议室建议 | `calendar +freebusy`, `+room-find`, `+suggestion` | P/A | P/A | `calendar:calendar.free_busy:read` | 参与人可见性、会议室城市/楼宇/容量和预订规则 | P；有过 freebusy 路径记录，room/suggestion 全参数为 N |
| 创建/更新日程 | `calendar +create`, `+update`, `calendar events create`, `calendar event.attendees create` | P/A | P/A | shortcut：`calendar:calendar.event:create/update`；native create：`calendar:calendar.event:create` | 当前用户必须有目标日历编辑权限；邀请人可能受企业策略约束；native `data.vchat` 可携带 `vc_type` | P；native schema 明确支持 user，模拟创建/审批/回读已覆盖；真实租户邀请与会议 URL 仍为 N |
| 回复日程邀请 | `calendar +rsvp` | P/A | P/A | `calendar:calendar.event:reply` | 只能代表当前用户回复可见事件；飞书仍校验事件 ACL | P；真实 RSVP 为 N |
| 任务读取 | `task +get-my-tasks`, `+search`, `task tasks get` | P/A | P/A | `task:task:read` | 任务详情仍由飞书返回范围决定 | P；真实任务读取有限 |
| 任务创建/修改/完成/分配/删除 | `task +update/+complete/+assign`, `task tasks create/delete` | P/A | P/A | `task:task:write`；原生删除还由 CLI 高风险确认保护 | 修改/分配/删除：创建者；执行人仅完成；管理员仅配置清单内例外 | P；任务所有权有单测，真实多账号 ACL 为 N |
| 项目任务清单 | `task +tasklist-create` | — | P/A | `task:tasklist:write`, `task:task:write` | 仅 lead/admin；实际清单权限仍由飞书校验 | P；没有真实创建清单 L |
| 视频会议查询 | `vc +search`, `vc +detail` | P/A | P/A | `vc:meeting.search:read`; 详情另需 `vc:meeting.meetingevent:read`, `vc:record:readonly` | 会议记录和会议产物可见性 | P；真实查询为 N |
| 妙记查询/详情 | `minutes +search`, `minutes +detail` | P/A | P/A | `minutes:minutes.search:read`, `minutes:minutes.basic:read`；总结/待办另需 artifacts | 内容访问、会议产物和租户策略 | P；空结果和授权续接有验证，非空内容为 N |
| 多维表格读取 | `base +table-list`, `+table-get`, `+field-list`, `+field-get`, `+record-list`, `+record-search`, `+record-get` | P/A | P/A | `base:table:read`, `base:field:read`, `base:record:read`, `base:view:read` | Base/table/字段必须对当前用户可见 | P；真实 Base 读取覆盖有限，其他资源为 N |
| 创建/调整 Base 表结构 | `base +base-create`, `+table-create`, `+table-update`, `+field-create` | P/A | P/A | Base 创建及 `base:table:update`, `base:field:create` 等对应 scopes | 所属空间、文件夹和企业 Base 策略；字段 schema 仍需用户确认 | P；Base 创建曾有特定验证，表改名/字段创建为 N |
| 多维表格记录新增/更新 | `base +record-batch-create`, `+record-batch-update`, `+record-upsert` | P/A | P/A | `base:record:create/update` | 目标表和字段 ACL、字段类型/公式约束；网站角色不能绕过资源拒绝 | P；已用真实 CLI help/schema 形状模拟 `field-list → singleSelect Status:["Done"]` 的确认、更新、回读；三种网站角色的资源拒绝分支均不得报告成功；真实租户写入仍为 N |
| 演示文稿创建 | `slides +create` | P/A | P/A | `slides:presentation:create`, `slides:presentation:write_only`, `docs:document.media:upload` | 目标文件夹和文档权限 | P；真实生成/读取为 N |

## 高价值但目前没有开放的操作

下面这些命令在本机 `lark-cli 1.0.93` 中存在，但不在网站 `COMMANDS` 中。它们
不是“忘记开权限”，而是为了避免未经审核的写操作被模型调用而暂时拒绝。
要加入必须同时补充命令参数 schema、角色策略、scope、确认/幂等策略、资源
ACL 测试和至少一条模拟飞书端到端测试。

### 仍待审核：日历与会议生命周期

| CLI 路径 | 官方 CLI scope（v1.0.93） | 风险 | 当前建议 |
| --- | --- | --- | --- |
| `calendar +delete` | `calendar:calendar.event:read`, `calendar:calendar.event:delete` | high-risk-write | 先增加单个事件确认、重复日程 `--apply-to` 强制选择、删除后回读 |
| `calendar +join-event` | `calendar:calendar.event:join` | write | 需明确 share token 来源和一次性使用 |
| `calendar +transfer` | `calendar:calendar.event:transfer`，条件 `calendar:calendar.event:read` | high-risk-write | 暂不建议开放；会改变组织者归属 |
| `vc +recording` | `vc:record:readonly` | read | 与妙记/录制 ACL 单独核验 |

`calendar +create` 和原生 `calendar events create` 都可以在当前用户可编辑的日历上
创建带飞书视频会议信息的日程：shortcut 默认发送 `vchat: {"vc_type":"vc"}`，
原生 schema 的 `data.vchat` 也支持 `vc_type`，且 CLI 元数据允许 `user` 身份。网站
会先展示写操作供确认，再按返回的 `event_id` 回读日程；只有回读结果确实包含
`vchat.meeting_url` 时才能向用户报告会议链接。CLI 的 `--meeting-owner-id` 只是
额外设置会议 owner，官方实现明确仅 bot 在 app calendar 场景生效；不能把它当成
普通用户创建 VC 的前置条件，也不能据此替换当前用户身份或声称支持任意会议 owner。

### 第二优先：多维表格完整 CRUD

当前 Base 只覆盖“创建 Base/表、查看结构、批量写记录”的安全子集，还不是完整
CRUD。建议按以下顺序增加：

| CLI 路径 | 典型 scope | 风险/边界 |
| --- | --- | --- |
| `base +table-delete` | `base:table:delete` | high-risk-write；必须二次确认并回读 |
| `base +record-delete` | `base:record:delete` | high-risk-write；必须单表/单记录确认并回读 |
| `base +field-update`, `+field-delete` | `base:field:update`, `base:field:delete` | 公式、关联、主字段变更可能影响整张表，建议分级确认 |
| `base +view-list`, `+view-create`, `+view-rename` 等 | `base:view:read`, `base:view:write_only` | 先开放读取，再开放非破坏性更新 |
| `base +record-history-list` | `base:history:read` | read；用于写入后核验 |

不要因为 `+base-create` 的官方依赖包含 `base:table:delete` 就把删除表操作
自动开放；scope 是应用能力，不是网站授权结论。

### 第三优先：消息、文档和电子表格

| 领域 | 当前缺口 | 建议 |
| --- | --- | --- |
| 消息 | `im +messages-reply`, `+messages-edit`, `+messages-read-status`, `+message-read-users`, reactions 等 | 先加 reply/read-status；编辑、撤回、催办需要单独高风险确认。部分原生 API 要 bot scope，不能混入用户身份能力 |
| 文档 | history、media、resource、whiteboard 操作 | 先开放历史读取/媒体读取；媒体写入必须限制本地文件范围和目标文档 ACL |
| 电子表格 | 当前 `sheets` 没有任何审核操作 | 先开放 `+workbook-info`、`+cells-get` 等只读；再设计单元格写入前的范围预览和回读 |
| Drive/Wiki | 当前只有 `drive +import` 与异步结果轮询 | 不要直接开放共享权限、移动、删除；先做资源解析与只读预览 |

## 测试证据与真实验证边界

当前新增 `backend/tests/test_capability_contract.py`，验证：

- 59 个已审核操作都能反向解析到能力、角色和 scope 契约；
- employee ⊆ lead ⊆ admin，lead/admin 的额外能力明确为项目群和任务清单；
- 用户消息发送包含 `im:message.send_as_user` 和 `im:message`，不把 bot 发送 scope 混入用户包；
- 日历、任务、Base 关键操作使用精确 scope；
- 原生 `calendar events create` 的 `data.vchat` 用户身份路径能够通过 schema、写操作审批、
  模拟创建和 `calendar events get` 回读 `vchat.meeting_url`；该测试不调用真实飞书；
- 所有注册操作在本机安装的 `lark-cli 1.0.93` 中仍存在，发现 CLI 升级删除/改名时测试失败。
- `backend/tests/test_bitable_workflow.py` 使用本机 `lark-cli 1.0.93` 的字段/记录命令 schema，模拟官方 Base 字段和记录矩阵响应，验证批量更新必须确认、按 `singleSelect` 的数组 CellValue 写入并回读；资源写入被拒绝时，`employee`、`lead`、`admin` 都保留失败证据，不能宣称完成。

这组测试不会伪造真实飞书成功。当前仍缺：

1. 普通员工、负责人、管理员至少各一个真实账号的资源 ACL 对照；
2. 日历创建、添加参与人、会议详情、重复日程删除的真实读写回读；
3. Base 每种常用字段类型的新增/更新/查询/失败回滚；
4. 消息发送的当前用户增量授权完成，以及发送后通过消息查询回读；
5. 授权过期、撤销、成员降级和目标资源无权限时的真实错误映射。

因此网站目前可以准确地说“已审核并按角色限制这些操作”，不能说“飞书几乎
所有权限都已开通且所有账号都能正常使用”。企业管理员仍需在飞书开发者后台
发布对应用户身份权限，用户本人完成增量授权，随后再按资源 ACL 做分项验收。

## 参考来源

- 本机 `lark-cli --help`、`lark-cli schema <service.resource.method> --format json`。
- [Lark CLI v1.0.93 日程创建源码](https://github.com/larksuite/cli/blob/v1.0.93/shortcuts/calendar/calendar_create.go)
- [Lark CLI v1.0.93 日程删除源码](https://github.com/larksuite/cli/blob/v1.0.93/shortcuts/calendar/calendar_delete.go)
- [Lark CLI v1.0.93 Base 创建源码](https://github.com/larksuite/cli/blob/v1.0.93/shortcuts/base/base_create.go)
- [Lark CLI v1.0.93 Base 记录写入源码](https://github.com/larksuite/cli/tree/v1.0.93/shortcuts/base)
- [Lark CLI v1.0.93 消息发送源码](https://github.com/larksuite/cli/blob/v1.0.93/shortcuts/im/im_messages_send.go)
- [Lark CLI v1.0.93 任务快捷命令源码](https://github.com/larksuite/cli/blob/v1.0.93/shortcuts/task/shortcuts.go)
