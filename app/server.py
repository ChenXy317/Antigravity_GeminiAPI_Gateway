"""管理 API 与三协议代理路由。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__, access_log
from .antigravity_auth import AntigravityAuthError, antigravity_auth
from .config import (
    ServerConfig,
    apply_incoming_server,
    assert_inbound_key_for_bind,
    config_manager,
    effective_bind_host,
    is_public_bind,
    resolve_model_name,
    server_public_dict,
)
from .converter.common import sse_format as _sse
from .quota import fetch_quota_and_tier
from .secrets import collect_secrets, redact, redact_any
from .stream import convert_stream
from .transform import (
    error_payload,
    gemini_to_ir_response,
    ir_to_gemini,
    to_ir,
    upstream_resp_to_inbound,
)
from .upstream import (
    StreamTimeoutError,
    UpstreamHTTPError,
    fetch_upstream_models,
    post_non_stream,
    stream_upstream,
)

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}
STARTED = time.time()
proxy_router = APIRouter(prefix="/v1")
_fallback_client: httpx.AsyncClient | None = None


def _get_client(request: Request) -> httpx.AsyncClient:
    """获取应用级 HTTP 客户端或备用客户端"""
    global _fallback_client
    client = getattr(request.app.state, "httpx_client", None)
    if client is not None:
        return client
    if _fallback_client is None or _fallback_client.is_closed:
        _fallback_client = httpx.AsyncClient(timeout=120.0)
    return _fallback_client


def _secret_extras() -> list[str]:
    cfg = config_manager.config
    return collect_secrets(cfg.server.local_api_key, cfg.server.admin_api_key)


def _safe_error(obj: Any) -> Any:
    return redact_any(obj, _secret_extras())


def _token_eq(a: str, b: str) -> bool:
    da = hashlib.sha256(a.encode("utf-8")).digest()
    db = hashlib.sha256(b.encode("utf-8")).digest()
    return hmac.compare_digest(da, db)


def _admin_token(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.removeprefix("Bearer ").removeprefix("bearer ").strip() if auth else ""
    if not token:
        token = (request.headers.get("x-admin-key") or request.headers.get("X-Admin-Key") or "").strip()
    if not token:
        token = (request.headers.get("x-api-key") or "").strip()
    return token


async def require_admin(request: Request) -> None:
    cfg = config_manager.config.server
    admin_key = (cfg.admin_api_key or "").strip()
    local_key = (cfg.local_api_key or "").strip()
    public_bind = is_public_bind(cfg.host)

    # 绑定非本机地址（如 0.0.0.0）时，必须设置 key 保护管理面
    if public_bind:
        required_key = admin_key or local_key
        if not required_key:
            raise HTTPException(403, "绑定非本机地址时必须设置 admin_api_key 或 local_api_key")
        token = _admin_token(request)
        if not token or not _token_eq(token, required_key):
            raise HTTPException(401, "管理 API 需要有效的 admin_api_key 或 local_api_key")
        return

    # 本机回环地址 (127.0.0.1)：仅在用户显式设置了 admin_api_key 时才要求登录管理端
    if admin_key:
        token = _admin_token(request)
        if not token or not _token_eq(token, admin_key):
            raise HTTPException(401, "管理 API 需要有效的 admin_api_key")


manage_router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _auth_check(request: Request) -> None:
    cfg = config_manager.config.server
    local_key = (cfg.local_api_key or "").strip()
    if not local_key:
        if is_public_bind(cfg.host):
            raise HTTPException(403, "绑定非本机地址时必须设置 local_api_key")
        return
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.removeprefix("Bearer ").removeprefix("bearer ").strip() if auth else ""
    if not token:
        token = (request.headers.get("x-api-key") or "").strip()
    if token and _token_eq(token, local_key):
        return
    raise HTTPException(401, "无效的 API Key（需匹配 server.local_api_key）")


def _is_retryable(status: int) -> bool:
    return status in RETRYABLE or status >= 500


# ---------- 代理 API ----------


@proxy_router.get("/models")
async def list_models(request: Request):
    """获取可用模型列表。"""
    _auth_check(request)
    cfg = config_manager.config.server
    data = []
    seen = set()
    for m in cfg.models:
        seen.add(m.id)
        data.append({
            "id": m.id,
            "object": "model",
            "created": int(STARTED),
            "owned_by": "google-antigravity",
        })
    aliases = cfg.model_aliases or {}
    for alias in aliases:
        if alias not in seen:
            seen.add(alias)
            data.append({
                "id": alias,
                "object": "model",
                "created": int(STARTED),
                "owned_by": "openai" if (alias.startswith("gpt") or alias.startswith("o")) else "anthropic",
            })
    return {"object": "list", "data": data}


@proxy_router.get("/models/{model_id:path}")
async def retrieve_model(model_id: str, request: Request):
    """获取单个模型详情。"""
    _auth_check(request)
    cfg = config_manager.config.server
    resolved = resolve_model_name(model_id, cfg)
    target = next((m for m in cfg.models if m.id == resolved), None)
    if not target and not cfg.allow_unknown_models and model_id not in (cfg.model_aliases or {}):
        raise HTTPException(404, f"模型不存在: {model_id}")
    return {
        "id": model_id,
        "object": "model",
        "created": int(STARTED),
        "owned_by": "google-antigravity",
    }


@proxy_router.post("/chat/completions")
async def chat_completions(request: Request):
    return await _handle_proxy_request(request, "chat")


@proxy_router.post("/responses")
async def responses(request: Request):
    return await _handle_proxy_request(request, "responses")


@proxy_router.post("/messages")
async def messages(request: Request):
    return await _handle_proxy_request(request, "messages")


async def _handle_proxy_request(request: Request, inbound: str):
    _auth_check(request)
    cfg = config_manager.config.server
    client: httpx.AsyncClient = _get_client(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体需为有效 JSON")

    model_raw = body.get("model") or cfg.default_model
    model_req = resolve_model_name(model_raw, cfg)
    known = any(m.id == model_req for m in cfg.models)
    if not known and not cfg.allow_unknown_models:
        raise HTTPException(400, f"未知模型: {model_raw}")

    stream_req = bool(body.get("stream"))
    try:
        ir = to_ir(inbound, body)
    except Exception as e:
        raise HTTPException(400, f"协议解析失败: {e}")

    ir.model = model_req

    payload = ir_to_gemini(ir, project=cfg.project, strip_base_persona=cfg.strip_base_persona)
    t0 = time.time()
    attempts = 0
    last_err: Exception | None = None

    token = await antigravity_auth.get_token()

    for attempt in range(cfg.retry_count + 1):
        attempts += 1
        try:
            if stream_req:
                upstream_gen = stream_upstream(
                    client=client,
                    base_url=cfg.upstream_base_url,
                    payload=payload,
                    token=token,
                    user_agent=cfg.user_agent,
                    first_token_timeout=cfg.first_token_timeout_s,
                    read_idle_timeout=cfg.read_idle_timeout_s,
                )
                usage_sink: dict[str, int] = {}
                converted_gen = convert_stream(upstream_gen, inbound, model=ir.model, usage_sink=usage_sink)

                async def logging_generator():
                    nonlocal attempts, t0
                    status_code = 200
                    err_msg = None
                    try:
                        async for chunk in converted_gen:
                            yield chunk
                    except asyncio.CancelledError:
                        status_code = 499
                        err_msg = "client disconnected"
                        raise
                    except Exception as ex:
                        status_code = 502
                        err_msg = str(ex)
                        yield _sse(error_payload(502, f"流传输中断: {ex}", inbound))
                    finally:
                        lat = int((time.time() - t0) * 1000)
                        access_log.add(
                            inbound=inbound,
                            model=ir.model,
                            provider_id="antigravity",
                            stream=True,
                            status=status_code,
                            latency_ms=lat,
                            error=err_msg,
                            attempts=attempts,
                            prompt_tokens=usage_sink.get("prompt_tokens"),
                            completion_tokens=usage_sink.get("completion_tokens"),
                        )

                return StreamingResponse(logging_generator(), media_type="text/event-stream")

            status_code, resp_json = await post_non_stream(
                client=client,
                base_url=cfg.upstream_base_url,
                payload=payload,
                token=token,
                timeout=cfg.timeout_s,
                user_agent=cfg.user_agent,
            )

            ir_resp = gemini_to_ir_response(resp_json, fallback_model=ir.model)
            out = upstream_resp_to_inbound(ir_resp, inbound)
            lat = int((time.time() - t0) * 1000)
            u = ir_resp.usage or {}
            access_log.add(
                inbound=inbound,
                model=ir.model,
                provider_id="antigravity",
                stream=False,
                status=200,
                latency_ms=lat,
                attempts=attempts,
                prompt_tokens=u.get("prompt_tokens"),
                completion_tokens=u.get("completion_tokens"),
            )
            return JSONResponse(out)

        except UpstreamHTTPError as e:
            last_err = e
            if e.status_code == 401:
                token = await antigravity_auth.get_token(force_refresh=True)
                continue
            if _is_retryable(e.status_code) and attempt < cfg.retry_count:
                await asyncio.sleep(cfg.retry_backoff_ms / 1000.0 * (2**attempt))
                continue
            break
        except (httpx.TimeoutException, StreamTimeoutError) as e:
            last_err = e
            if attempt < cfg.retry_count:
                await asyncio.sleep(cfg.retry_backoff_ms / 1000.0 * (2**attempt))
                continue
            break
        except Exception as e:
            last_err = e
            break

    lat = int((time.time() - t0) * 1000)
    err_str = str(last_err)
    status_code = getattr(last_err, "status_code", 502) if isinstance(last_err, UpstreamHTTPError) else 502
    access_log.add(
        inbound=inbound,
        model=ir.model,
        provider_id="antigravity",
        stream=stream_req,
        status=status_code,
        latency_ms=lat,
        error=err_str,
        attempts=attempts,
    )
    return JSONResponse(
        _safe_error(error_payload(status_code, f"上游错误: {err_str}", inbound)),
        status_code=status_code,
    )


# ---------- 管理 API ----------


@manage_router.get("/status")
async def api_status(request: Request):
    cfg = config_manager.config
    client: httpx.AsyncClient = _get_client(request)

    sess_info = {
        "logged_in": False,
        "email": "",
        "expired": True,
        "error": None,
    }
    quota_info: dict[str, Any] | None = None

    try:
        sess = await antigravity_auth.get_session()
        sess_info = {
            "logged_in": bool(sess.access_token),
            "email": antigravity_auth.mask_email(sess.email),
            "expired": sess.expired,
            "error": None,
        }
        if sess.access_token:
            quota_info = await fetch_quota_and_tier(
                client=client,
                token=sess.access_token,
                base_url=cfg.server.upstream_base_url,
                user_agent=cfg.server.user_agent,
            )
    except Exception as e:
        sess_info["error"] = redact(str(e), _secret_extras())

    return {
        "ok": True,
        "version": __version__,
        "uptime_s": int(time.time() - STARTED),
        "host": cfg.server.host,
        "port": cfg.server.port,
        "models": [m.model_dump() for m in cfg.server.models],
        "model_aliases": cfg.server.model_aliases,
        "default_model": cfg.server.default_model,
        "auth": bool((cfg.server.local_api_key or "").strip()),
        "admin_auth": bool((cfg.server.admin_api_key or "").strip())
        or is_public_bind(cfg.server.host),
        "bind_host": effective_bind_host(),
        "antigravity": sess_info,
        "quota": quota_info,
        "logs": access_log.stats(),
        "upstream": {
            "base_url": cfg.server.upstream_base_url,
            "project": cfg.server.project,
        },
    }


@manage_router.get("/config")
async def get_config():
    return {"server": server_public_dict(config_manager.config.server)}


@manage_router.put("/config")
async def update_config(body: dict[str, Any]):
    srv = body.get("server")
    if not srv or not isinstance(srv, dict):
        raise HTTPException(400, "需提供 server 配置")
    try:
        srv = apply_incoming_server(srv, config_manager.config.server)
        new_srv = ServerConfig.model_validate(srv)
        assert_inbound_key_for_bind(new_srv.host, new_srv.local_api_key)
        await config_manager.update_server(srv)
        return {"ok": True, "server": server_public_dict(config_manager.config.server)}
    except Exception as e:
        raise HTTPException(400, str(e))


@manage_router.post("/auth/refresh")
async def auth_refresh():
    try:
        sess = await antigravity_auth.get_session(force_refresh=True)
        return {
            "ok": True,
            "email": antigravity_auth.mask_email(sess.email),
            "expired": sess.expired,
            "expires_at": sess.expires_at.isoformat() if sess.expires_at else None,
        }
    except Exception as e:
        raise HTTPException(500, f"凭据刷新失败: {e}")


@manage_router.post("/test")
async def api_test(request: Request, body: dict[str, Any] | None = None):
    cfg = config_manager.config.server
    client: httpx.AsyncClient = _get_client(request)
    model = (body or {}).get("model") or cfg.default_model

    t0 = time.time()
    try:
        tok = await antigravity_auth.get_token()
        payload = {
            "project": cfg.project,
            "model": model,
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "Hello, ping test!"}]}],
                "generationConfig": {"maxOutputTokens": 10},
            },
        }
        _, resp = await post_non_stream(
            client=client,
            base_url=cfg.upstream_base_url,
            payload=payload,
            token=tok,
            timeout=20.0,
            user_agent=cfg.user_agent,
        )
        ir_resp = gemini_to_ir_response(resp, fallback_model=model)
        lat = int((time.time() - t0) * 1000)
        return {
            "ok": True,
            "model": model,
            "latency_ms": lat,
            "reply": ir_resp.content,
        }
    except Exception as e:
        lat = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "model": model,
            "latency_ms": lat,
            "error": redact(str(e), _secret_extras()),
        }


@manage_router.post("/models/fetch")
async def api_models_fetch(request: Request):
    cfg = config_manager.config.server
    client: httpx.AsyncClient = _get_client(request)
    try:
        tok = await antigravity_auth.get_token()
        models = await fetch_upstream_models(
            client=client,
            base_url=cfg.upstream_base_url,
            token=tok,
            user_agent=cfg.user_agent,
        )
        if models:
            await config_manager.set_models(models)
        return {"ok": True, "models": models}
    except Exception as e:
        raise HTTPException(500, f"拉取模型失败: {e}")


@manage_router.get("/logs")
async def get_logs(limit: int = 100, offset: int = 0):
    return access_log.list_logs(limit=limit, offset=offset)


@manage_router.delete("/logs")
async def delete_logs():
    access_log.clear()
    return {"ok": True}


@manage_router.post("/play/{mode}")
async def playground(mode: str, body: dict[str, Any], request: Request):
    if mode not in ("chat", "responses", "messages"):
        raise HTTPException(400, "仅支持 chat / responses / messages")
    return await _handle_proxy_request(request, mode)
