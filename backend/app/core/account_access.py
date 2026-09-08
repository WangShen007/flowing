from __future__ import annotations

from app.core.storage import store


def account_execution_error(account: str) -> str:
    from app.core.bot_channels import channel_execution, channel_store

    binding_id = channel_execution.get()
    if binding_id and not channel_store.active(binding_id, account):
        return "机器人已解绑或账号已停用，不能继续执行。"
    row = store.query_one(
        """SELECT COALESCE(m.enabled, 1) AS enabled FROM accounts a
           LEFT JOIN account_memberships m ON m.account = a.account
           WHERE a.account = ?""",
        (account,),
    )
    if not row or not row["enabled"]:
        return "账号已停用或不存在，请联系管理员。"
    return ""
