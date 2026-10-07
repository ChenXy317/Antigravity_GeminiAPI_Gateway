"""配置管理 — config.json 读写与参数校验。"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

CONFIG_PATH = Path(os.getenv("GEMINI_GATEWAY_CONFIG") or Path(__file__).resolve().parent.parent / "config.json")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "*"})


class ConfigError(RuntimeError):
    pass


DEFAULT_MODELS = [
    {"id": "gemini-3.8-flash-high", "display_name": "Gemini 3.8 Flash (High)"},
    {"id": "gemini-3.8-flash-medium", "display_name": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.1-pro-high", "display_name": "Gemini 3.1 Pro (High)"},
    {"id": "gemini-pro-agent", "display_name": "Gemini Pro Agent"},
    {"id": "gemini-3.7-flash-high", "display_name": "Gemini 3.7 Flash (High)"},
    {"id": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro"},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6 (Thinking)"},
    {"id": "claude-opus-4-6-thinking", "display_name": "Claude Opus 4.6 (Thinking)"},
]

DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "gpt-4o": "gemini-3.8-flash-high",
    "gpt-4o-mini": "gemini-3.8-flash-medium",
    "gpt-4": "gemini-3.8-flash-high",
    "gpt-4-turbo": "gemini-3.8-flash-high",
    "gpt-3.5-turbo": "gemini-3.8-flash-medium",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
    "claude-3-7-sonnet": "claude-sonnet-4-6",
    "claude-3-7-sonnet-latest": "claude-sonnet-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-3-opus": "claude-opus-4-6-thinking",
    "claude-3-opus-latest": "claude-opus-4-6-thinking",
    "claude-opus": "claude-opus-4-6-thinking",
    "o1": "gemini-3.1-pro-high",
    "o1-preview": "gemini-3.1-pro-high",
    "o1-mini": "gemini-3.8-flash-high",
    "o3-mini": "gemini-3.8-flash-high",
}


class ModelInfo(BaseModel):
    id: str
    display_name: str = ""


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8789
    local_api_key: str = "sk-local"
    admin_api_key: str = ""
    retry_count: int = 1
    retry_backoff_ms: int = 400
    log_retain: int = 5000
    connect_timeout_s: float = 15
    first_token_timeout_s: float = 90
    read_idle_timeout_s: float = 120
    timeout_s: float = 180
    default_model: str = "gemini-3.8-flash-high"
    allow_unknown_models: bool = True
    project: str = "aicode-consumers"
    upstream_base_url: str = "https://daily-cloudcode-pa.googleapis.com"
    user_agent: str = "antigravity/2.16.0"
    strip_base_persona: bool = False
    models: list[ModelInfo] = Field(default_factory=lambda: [ModelInfo(**m) for m in DEFAULT_MODELS])
    model_aliases: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_MODEL_ALIASES))


    @field_validator("upstream_base_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith("http"):
            raise ValueError("upstream_base_url 必须以 http/https 开头")
        return v

    @field_validator("retry_count")
    @classmethod
    def validate_retry(cls, v: int) -> int:
        if v < 0 or v > 5:
            raise ValueError("retry_count 范围 0-5")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if v < 1 or v > 65535:
            raise ValueError("port 范围 1-65535")
        return v


def resolve_model_name(requested_model: str, cfg: ServerConfig) -> str:
    """根据配置与别名表解析目标模型名称。"""
    if not requested_model:
        return cfg.default_model
    for m in cfg.models:
        if m.id == requested_model:
            return requested_model
    aliases = cfg.model_aliases or DEFAULT_MODEL_ALIASES
    if requested_model in aliases:
        return aliases[requested_model]
    lower_req = requested_model.lower()
    for alias_k, alias_v in aliases.items():
        if alias_k.lower() == lower_req:
            return alias_v
    return requested_model


class GatewayConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_PATH
        self._lock = asyncio.Lock()
        self._cached: GatewayConfig | None = None

    @property
    def config(self) -> GatewayConfig:
        if self._cached is None:
            self._cached = self.load()
        return self._cached

    def load(self) -> GatewayConfig:
        if not self.path.exists():
            cfg = GatewayConfig()
            self.save_sync(cfg)
            self._cached = cfg
            return cfg

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            cfg = GatewayConfig(**raw)
            self._cached = cfg
            return cfg
        except Exception as e:
            raise ConfigError(f"读取配置失败 ({self.path}): {e}") from e

    def save_sync(self, cfg: GatewayConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(cfg.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self._cached = cfg

    async def update_server(self, patch: dict[str, Any]) -> GatewayConfig:
        async with self._lock:
            cur = self.config.model_dump()
            srv = cur.get("server", {})
            for k, v in patch.items():
                if v is not None:
                    srv[k] = v
            cur["server"] = srv
            new_cfg = GatewayConfig(**cur)
            self.save_sync(new_cfg)
            return new_cfg

    async def set_models(self, models: list[dict[str, str]]) -> GatewayConfig:
        async with self._lock:
            cur = self.config.model_dump()
            cur.setdefault("server", {})["models"] = models
            new_cfg = GatewayConfig(**cur)
            self.save_sync(new_cfg)
            return new_cfg


config_manager = ConfigManager()


def effective_bind_host() -> str:
    raw = (os.getenv("GEMINI_GATEWAY_BIND_HOST") or "").strip()
    return raw or config_manager.config.server.host


def is_public_bind(host: str | None = None) -> bool:
    h = (host or effective_bind_host()).strip().lower()
    return h in WILDCARD_HOSTS or (h not in LOOPBACK_HOSTS and not h.startswith("127."))


def assert_inbound_key_for_bind(host: str | None, key: str | None) -> None:
    if is_public_bind(host) and not (key or "").strip():
        raise ConfigError("公开或局域网绑定时必须配置非空的 local_api_key")


def should_lockdown(scope: dict | None = None) -> bool:
    cfg = config_manager.config.server
    if not is_public_bind():
        return False
    return not (cfg.admin_api_key.strip() or cfg.local_api_key.strip())


def server_public_dict(cfg: ServerConfig) -> dict[str, Any]:
    d = cfg.model_dump()
    for k in ("local_api_key", "admin_api_key"):
        val = d.get(k) or ""
        d[k + "_configured"] = bool(val)
        d[k + "_preview"] = (val[:3] + "..." + val[-3:]) if len(val) >= 8 else ("***" if val else "")
    # admin_api_key 不返回明文，避免敏感凭据泄露
    d["admin_api_key"] = ""
    return d


def apply_incoming_server(payload: dict[str, Any], current: ServerConfig) -> dict[str, Any]:
    out = dict(payload)
    if out.pop("clear_admin_api_key", False):
        out["admin_api_key"] = ""
    for key in ("local_api_key", "admin_api_key"):
        if key in out:
            val = out[key]
            if val == "[UNCHANGED]" or val is None:
                out[key] = getattr(current, key)
            elif isinstance(val, str):
                s = val.strip()
                if key == "admin_api_key" and (not s or s.strip("*") == ""):
                    out[key] = getattr(current, key)
                elif key == "local_api_key" and s.strip("*") == "" and s != "":
                    out[key] = getattr(current, key)
                else:
                    out[key] = s

    # 针对 upstream_base_url、project、host 等基础配置项增加传空保底逻辑
    for key in ("upstream_base_url", "project", "host", "default_model"):
        if key in out and not str(out[key] or "").strip():
            out[key] = getattr(current, key)

    if "strip_base_persona" in out:
        out["strip_base_persona"] = bool(out["strip_base_persona"])

    return out
