"""密钥解析与脱敏：环境变量引用解析，敏感 Key 掩码过滤。"""
from __future__ import annotations

import os
import re
from typing import Any

REF_ENV_PREFIX = "env:"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY_PAT = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[^\s\"',;]+|(?:x-api-key|api[_-]?key)[\"'\s:=]+[^\s\"',;]+)"
)


def is_secret_ref(value: str | None) -> bool:
    """判断是否为环境变量引用。"""
    v = (value or "").strip()
    if not v:
        return False
    if v.lower().startswith(REF_ENV_PREFIX):
        return True
    if v.startswith("${") and v.endswith("}") and len(v) > 3:
        return True
    if v.startswith("$") and _ENV_NAME.match(v[1:]):
        return True
    return False


def _env_name(value: str) -> str | None:
    v = value.strip()
    if v.lower().startswith(REF_ENV_PREFIX):
        name = v[4:].strip()
        return name if _ENV_NAME.match(name) else None
    if v.startswith("${") and v.endswith("}"):
        name = v[2:-1].strip()
        return name if _ENV_NAME.match(name) else None
    if v.startswith("$") and _ENV_NAME.match(v[1:]):
        return v[1:]
    return None


def parse_env_name(value: str | None) -> str | None:
    """从引用形式解析出环境变量名。"""
    v = (value or "").strip()
    if not v:
        return None
    named = _env_name(v)
    if named:
        return named
    return v if _ENV_NAME.match(v) else None


def to_env_ref(value: str | None) -> str:
    """将变量名规范化为 env:NAME 格式。"""
    name = parse_env_name(value)
    if not name:
        raise ValueError("环境变量名无效，需匹配 [A-Za-z_][A-Za-z0-9_]*")
    return f"{REF_ENV_PREFIX}{name}"


def resolve_secret(value: str | None) -> str:
    """明文直接返回，引用格式读取环境变量。"""
    v = (value or "").strip()
    if not v:
        return ""
    name = _env_name(v)
    if name:
        return os.environ.get(name, "")
    return v


def collect_secrets(*values: str | None) -> list[str]:
    """汇总需脱敏的密钥明文。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not raw:
            continue
        for candidate in (raw, resolve_secret(raw)):
            c = (candidate or "").strip()
            if len(c) >= 8 and c not in seen and not is_secret_ref(c):
                seen.add(c)
                out.append(c)
    return out


def redact(text: str | None, extras: list[str] | None = None) -> str:
    """对字符串中的敏感凭据打码。"""
    if not text:
        return ""
    out = _KEY_PAT.sub("[REDACTED]", text)
    for s in extras or []:
        if s and len(s) >= 8 and s in out:
            out = out.replace(s, "[REDACTED]")
    return out


def redact_any(obj: Any, extras: list[str] | None = None) -> Any:
    """递归对结构体、字典、列表及文本脱敏。"""
    if obj is None:
        return None
    if isinstance(obj, str):
        return redact(obj, extras)
    if isinstance(obj, bytes):
        return redact(obj.decode(errors="ignore"), extras).encode()
    if isinstance(obj, dict):
        sensitive = {"api_key", "inbound_key", "authorization", "x-api-key", "admin_api_key", "local_api_key"}
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in sensitive and isinstance(v, str) and v and not is_secret_ref(v):
                out[k] = "[REDACTED]" if v else v
            else:
                out[k] = redact_any(v, extras)
        return out
    if isinstance(obj, list):
        return [redact_any(x, extras) for x in obj]
    return obj
