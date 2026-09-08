"""Account-owned resource aliases, separate from reusable workflow memory."""
from __future__ import annotations

import json
import re
from contextlib import closing
from typing import Any

from app.core.storage import SQLiteStore


def resource_table(db: SQLiteStore) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS business_resources (
        account TEXT NOT NULL REFERENCES accounts(account) ON DELETE CASCADE,
        alias TEXT NOT NULL, config TEXT NOT NULL,
        PRIMARY KEY(account, alias))""")


def list_resources(db: SQLiteStore, account: str) -> list[dict[str, Any]]:
    resource_table(db)
    return [{"alias": row["alias"], **json.loads(row["config"])} for row in
            db.query_all("SELECT alias,config FROM business_resources WHERE account=? ORDER BY alias", (account,))]


def validate_resource(args: dict[str, Any]) -> dict[str, Any]:
    alias = args.get("alias")
    if not isinstance(alias, str) or not alias.strip() or len(alias) > 80:
        raise ValueError("资源别名必须为 1–80 个字符")
    for key, pattern in (("base_token", r"[A-Za-z0-9]{10,100}"), ("table_id", r"tbl[A-Za-z0-9]{5,100}")):
        if not isinstance(args.get(key), str) or not re.fullmatch(pattern, args[key]):
            raise ValueError(f"无效的 {key}，请从实际查询结果取得")
    fields = args.get("fields", {})
    if not isinstance(fields, dict) or len(fields) > 30 or any(
        not isinstance(k, str) or not k.strip() or len(k) > 80
        or not isinstance(v, str) or not re.fullmatch(r"fld[A-Za-z0-9]{3,100}", v)
        for k, v in fields.items()
    ):
        raise ValueError("字段映射必须为业务名称到真实 fld ID，最多 30 项")
    return {"alias": alias.strip(), "base_token": args["base_token"], "table_id": args["table_id"], "fields": fields}


def save_resource(db: SQLiteStore, account: str, config: dict[str, Any]) -> None:
    config = validate_resource(config)
    resource_table(db)
    alias = config.pop("alias")
    # One transaction also bounds concurrent creation, without evicting old aliases.
    with closing(db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM business_resources WHERE account=? AND alias=?", (account, alias)).fetchone()
        if not exists and conn.execute("SELECT count(*) FROM business_resources WHERE account=?", (account,)).fetchone()[0] >= 30:
            raise ValueError("最多保存 30 个资源别名，请先删除不用的配置")
        conn.execute("INSERT INTO business_resources VALUES (?,?,?) ON CONFLICT(account,alias) DO UPDATE SET config=excluded.config",
                     (account, alias, json.dumps(config, ensure_ascii=False)))


def delete_resource(db: SQLiteStore, account: str, alias: str) -> None:
    resource_table(db)
    db.execute("DELETE FROM business_resources WHERE account=? AND alias=?", (account, alias))
