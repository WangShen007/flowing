"""One audited operation registry shared by model discovery and execution policy."""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator
from app.core.calendar_time import calendar_input_schema, normalize_calendar_arguments

from app.core.feishu_permissions import (
    CAPABILITIES,
    COMMANDS,
    capabilities_for_role,
    member_role,
    required_command_scopes,
)
from app.core.storage import store

FORBIDDEN_FLAGS = {
    "as", "profile", "config", "app-id", "app-secret", "output", "jq", "format",
    "json", "help", "dry-run", "yes", "idempotency-key",
}

# These shortcuts use ``--json`` as a required input payload.  ``+table-update``
# also exposes a flag named ``--json``, but there it is an output-format alias
# and must stay hidden from model-supplied arguments.  Keep this allow-list
# explicit so a newly added shortcut cannot accidentally accept arbitrary JSON.
JSON_INPUT_OPERATIONS = frozenset({
    ("base", "+field-create"),
    ("base", "+record-batch-create"),
    ("base", "+record-batch-update"),
    ("base", "+record-upsert"),
})

# Keep this explicit instead of inferring risk from command spelling. Some
# read-only shortcuts (for example ``+meeting`` and ``+list-attendees``) do not
# carry a conventional ``-get``/``-list`` suffix, while native methods can use
# the same final verb for either a read or a write operation.
READ_OPERATIONS = frozenset({
    ("docs", "+fetch"), ("docs", "+search"),
    ("drive", "+task_result"),
    ("im", "+chat-list"), ("im", "+chat-search"), ("im", "chats", "get"),
    ("im", "+chat-members-list"), ("im", "chat.members", "get"),
    ("im", "+chat-messages-list"), ("im", "+messages-search"),
    ("contact", "+search-user"),
    ("calendar", "+agenda"), ("calendar", "+get"), ("calendar", "+search-event"),
    ("calendar", "+list-attendees"), ("calendar", "+meeting"),
    ("calendar", "+freebusy"), ("calendar", "+room-find"), ("calendar", "+suggestion"),
    ("calendar", "events", "list"), ("calendar", "events", "get"),
    ("task", "+get-my-tasks"), ("task", "+search"), ("task", "tasks", "get"),
    ("vc", "+search"), ("vc", "+detail"),
    ("minutes", "+search"), ("minutes", "+detail"),
    ("base", "+table-list"), ("base", "+table-get"),
    ("base", "+field-list"), ("base", "+field-get"),
    ("base", "+record-list"), ("base", "+record-search"), ("base", "+record-get"),
})

# Keep mention handling aligned with the official ``im +messages-send``
# shortcut.  The CLI accepts id/open_id/user_id spellings and normalizes them
# to the user_id form before calling the API; the web agent performs the same
# normalization before the command is executed so the fake executor and the
# real CLI see the same payload.
_MENTION_NORMALIZE_RE = re.compile(
    r'<at\s+(?:id|open_id|user_id)=("?)([^"\s/>]+)"?\s*/?>'
)
_MENTION_OPEN_RE = re.compile(r"<at\b[^>]*>")
_MENTION_TOKEN_RE = re.compile(r"<at\b")
_MENTION_CLOSE_RE = re.compile(r"</at\s*>")
_MENTION_ALLOWED_RE = re.compile(r'<at\s+user_id="(?:ou_[A-Za-z0-9]+|all)">')
_POST_LOCALE_RE = re.compile(r"^[a-z]{2}(?:_[a-z]{2})?$")


def _normalize_message_strings(value: Any) -> Any:
    """Normalize mention tags in a JSON-compatible message payload."""

    if isinstance(value, str):
        return _MENTION_NORMALIZE_RE.sub(
            lambda match: f'<at user_id="{match.group(2)}">', value
        )
    if isinstance(value, list):
        return [_normalize_message_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_message_strings(item) for key, item in value.items()}
    return value


def _validate_message_mentions(value: Any) -> None:
    """Reject malformed or non-user mention tags before a send is approved."""

    strings: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)

    collect(value)
    for text in strings:
        if "<at" not in text and "</at>" not in text:
            continue
        tokens = list(_MENTION_TOKEN_RE.finditer(text))
        openings = list(_MENTION_OPEN_RE.finditer(text))
        closings = list(_MENTION_CLOSE_RE.finditer(text))
        if len(tokens) != len(openings) or len(closings) > len(openings):
            raise ValueError(
                '提及格式错误。JSON 解码后的内容应包含 <at user_id="ou_真实ID">姓名</at>，'
                "引号前不能残留反斜杠。请修正后再请求发送。"
            )
        if any(not _MENTION_ALLOWED_RE.fullmatch(match.group(0)) for match in openings):
            raise ValueError(
                '提及格式错误。JSON 解码后的内容应包含 <at user_id="ou_真实ID">姓名</at>，'
                "引号前不能残留反斜杠。请修正后再请求发送。"
            )


def _decode_message_content(raw: Any) -> Any:
    if not isinstance(raw, str):
        raise ValueError("content 必须是合法 JSON 字符串")
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("content 必须是合法 JSON 字符串") from exc


def _normalize_text_message_content(arguments: dict[str, Any]) -> None:
    raw = _decode_message_content(arguments["content"])
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        raise ValueError("文本消息 content 必须包含字符串 text")
    normalized = _normalize_message_strings(raw)
    _validate_message_mentions(normalized)
    arguments["content"] = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _normalize_post_message_content(arguments: dict[str, Any]) -> None:
    raw = _decode_message_content(arguments["content"])
    if not isinstance(raw, dict):
        raise ValueError("post 消息 content 必须是 JSON 对象")
    if "post" in raw:
        if len(raw) != 1 or not isinstance(raw["post"], dict):
            raise ValueError("post 消息 content 只能包含 post 对象")
        raw = raw["post"]

    locales = [
        (locale, body)
        for locale, body in raw.items()
        if isinstance(locale, str) and _POST_LOCALE_RE.fullmatch(locale)
    ]
    if not locales or len(locales) != len(raw):
        raise ValueError("post 消息 content 必须包含 zh_cn 等语言对象")
    for _, body in locales:
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise ValueError("post 消息语言对象必须包含二维数组 content")
        if "title" in body and not isinstance(body["title"], str):
            raise ValueError("post 消息 title 必须是字符串")
        if any(
            not isinstance(row, list) or any(not isinstance(node, dict) for node in row)
            for row in body["content"]
        ):
            raise ValueError("post 消息 content 必须是由消息节点组成的二维数组")

    normalized = _normalize_message_strings(raw)
    _validate_message_mentions(normalized)
    # The Feishu API expects the locale object directly.  It does not accept
    # the convenient model-facing {"post": {"zh_cn": ...}} wrapper.
    arguments["content"] = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def operation_key(path: tuple[str, ...]) -> str:
    return " ".join(path)


def is_write(path: tuple[str, ...]) -> bool:
    return path not in READ_OPERATIONS


def catalog(user_id: str) -> list[dict[str, Any]]:
    allowed = set(capabilities_for_role(member_role(user_id)))
    grant = store.query_one("SELECT scopes FROM feishu_grants WHERE account=? AND revoked=0", (user_id,))
    scopes = set(grant["scopes"].split()) if grant else set()
    return [
        {"operation": operation_key(path), "description": "、".join(CAPABILITIES[key].title for key in caps),
         "write": is_write(path), "allowed": all(key in allowed for key in caps),
         "missing_scopes": sorted(required_command_scopes(["lark-cli", *path], caps) - scopes)}
        for path, caps in COMMANDS.items()
    ]


def resolve_operation(operation: str) -> tuple[str, ...]:
    for path in COMMANDS:
        if operation_key(path) == operation:
            return path
    raise ValueError("操作不在已审核工具目录中；请使用 discover_tools 查看可用能力。")


async def _inspect_cli(args: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        "lark-cli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError(f"已安装 CLI 不支持此操作：{stderr.decode()[:500]}")
    return stdout.decode()


@lru_cache(maxsize=128)
def _help_schema(help_text: str, *, allow_json_input: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for match in re.finditer(r"^\s+(?:-\w,\s+)?--([a-z][a-z0-9-]*)(?:\s+(string|strings|int|float|bool))?\s{2,}(.+)$", help_text, re.M):
        key, kind, description = match.groups()
        if key in FORBIDDEN_FLAGS and not (allow_json_input and key == "json"):
            continue
        field: dict[str, Any] = {"type": {"int": "integer", "float": "number", "bool": "boolean",
                                             "strings": "array", "string": "string", None: "boolean"}[kind],
                                 "description": description}
        if kind == "strings":
            field["items"] = {"type": "string"}
        if key in {"params", "data"}:
            field = {"type": "object", "description": "JSON 对象，不接受文件引用或 JSON 字符串"}
        properties[key] = field
    return {"type": "object", "properties": properties, "additionalProperties": False}


def model_tool_description(description: dict[str, Any]) -> dict[str, Any]:
    """Keep validation locally; avoid duplicate output schemas/help in model context."""
    def concise(value: Any) -> Any:
        if isinstance(value, list):
            return [concise(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {key: (item[:300] if key == "description" and isinstance(item, str) else concise(item))
                for key, item in value.items() if key not in {"example", "examples", "flag", "carrier", "enumDescriptions"}}
    result = {key: description[key] for key in ("operation", "write") if key in description}
    result["input_schema"] = concise(description["input_schema"])
    if any(part.startswith("+") for part in description["operation"].split()):
        result["help"] = description.get("help", "")
    return result


async def describe_operation(operation: str) -> dict[str, Any]:
    path = resolve_operation(operation)
    help_text = await _inspect_cli([*path, "--help"])
    schema = dict(_help_schema(help_text, allow_json_input=path in JSON_INPUT_OPERATIONS))
    native_schema = None
    if not path[-1].startswith("+"):
        native_schema = json.loads(await _inspect_cli(["schema", ".".join(path), "--format", "json"]))
        # Native APIs accept structured params/data, with the installed CLI's
        # exact nested validation instead of model-invented parameter names.
        schema = native_schema["inputSchema"]
        schema = {**schema, "additionalProperties": False}
        if path == ("task", "tasks", "delete"):
            # CLI confirmation is supplied by the backend after human approval,
            # never by model arguments.
            schema["properties"].pop("yes", None)
        if path == ("im", "chat.members", "get"):
            params = schema["properties"]["params"]
            params["properties"]["check_security_conf"] = {
                "type": "boolean", "const": True,
                "description": "必须为 true，遵守飞书群安全配置。",
            }
            params["required"] = list(dict.fromkeys([*params.get("required", []), "check_security_conf"]))
    if operation == "calendar events create":
        schema = calendar_input_schema(schema)
        help_text = "网站时间契约：start_time/end_time 使用 datetime+timezone（默认北京时间），不要提供 timestamp。后端负责换算。\n" + help_text
    return {"operation": operation, "write": is_write(path), "input_schema": schema,
            "help": help_text, "output_schema": (native_schema or {}).get("outputSchema")}


def build_command(operation: str, arguments: dict[str, Any], description: dict[str, Any]) -> str:
    path = resolve_operation(operation)
    if description.get("operation") != operation:
        raise ValueError("请先读取此操作的参数定义。")
    # Normalize only the local copy.  The model/tool trace should retain the
    # original arguments, while the command sent to the CLI must contain the
    # exact API payload shape.
    arguments = dict(arguments)
    errors = list(Draft202012Validator(description["input_schema"]).iter_errors(arguments))
    if errors:
        raise ValueError("参数校验失败：" + errors[0].message)
    arguments = normalize_calendar_arguments(operation, arguments)
    if operation == "im +messages-send":
        msg_type = str(arguments.get("msg-type", "text")).lower()
        if "content" in arguments:
            if msg_type == "text":
                _normalize_text_message_content(arguments)
            elif msg_type == "post":
                _normalize_post_message_content(arguments)
        if "text" in arguments:
            if not isinstance(arguments["text"], str):
                raise ValueError("文本消息 text 必须是字符串")
            arguments["text"] = _normalize_message_strings(arguments["text"])
            _validate_message_mentions(arguments["text"])
    args = ["lark-cli", *path]
    for key, value in arguments.items():
        json_input = path in JSON_INPUT_OPERATIONS and key == "json"
        if (key in FORBIDDEN_FLAGS and not json_input) or not re.fullmatch(r"[a-z][a-z0-9-]*", key):
            raise ValueError(f"禁止由模型设置参数 {key}")
        if key in {"params", "data"}:
            if not isinstance(value, dict):
                raise ValueError("原生 API 参数必须为 JSON 对象")
            args.extend([f"--{key}", json.dumps(value, ensure_ascii=False)])
        elif isinstance(value, bool):
            args.append(f"--{key}={'true' if value else 'false'}")
        elif isinstance(value, (str, int, float, list)):
            text = ",".join(value) if isinstance(value, list) else str(value)
            if text.startswith("@"):
                raise ValueError("工具参数不能引用服务器文件")
            args.extend([f"--{key}", text])
        else:
            raise ValueError(f"不支持的参数类型：{key}")
    args.extend(["--as", "user", "--format", "json"])
    return shlex.join(args)
