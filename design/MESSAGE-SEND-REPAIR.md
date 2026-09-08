# 消息发送修复

真实失败：执行记录 64/65，CLI identity=user，错误 99991679。现有 grant 包含 im:message.send_as_user，但不包含 im:message。官方 CLI v1.0.93 的 UserScopes 声明二者，实际调用 POST /open-apis/im/v1/messages。网站现在按此映射提前检查，而不是等用户确认发送后才发现依赖缺失。不得将错误中的候选权限全部申请，更不能自动切换 bot。

修复内容：

- 发送能力增加 im:message 用户身份依赖，已有 OAuth 角色过滤/增量授权处理它。
- LangGraph 输出明确的 waiting_approval / waiting_authorization / completed 等状态。
- SSE 只有图明确 completed 才记成功；授权、确认、失败或服务异常中的成功查询不等于整体成功。
- 记忆入库独立检查图终态，不能把部分成功查询学习为已完成发送。
- 文本 @ 标签在请求确认前校验，JSON 解码后引号残留反斜杠、标签缺失均要求模型修正，不发送坏格式。
- 同一次图执行中，同一失败写命令不再弹出第二次确认并盲目重试。修复后用户重新发起，正常成功回执去重机制不变。

数据纠正：执行 63 的准备步骤曾误记成功，已改为 success=0 并在 plan 留 audit_correction。候选记忆 6（last_execution_record_id=63）已停用，移除对应历史消息的错误记忆卡片 metadata；未删除原始聊天文本或命令日志。此状态可追溯，未重发通知。

自动化验证覆盖成功查询但等待确认/失败/异常、不合法 @、重复失败写入、成功只读任务仍可完成。真实外发测试需用户另行确认目标和正文；本轮不重发“该吃饭了”。

验证结果：216 项后端测试通过，git diff --check 通过；本地服务已重载，/health 返回 ok。后台已新增且核实 `im:message` 为“用户身份 / 已开通 / 与用户权限范围一致”。个人 grant 尚需网站增量授权；未进行真实消息外发测试。
