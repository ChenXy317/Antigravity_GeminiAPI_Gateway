"""Gemini Gateway 入口应用。"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import config_manager, effective_bind_host, should_lockdown
from .server import manage_router, proxy_router
from .transform import error_payload

ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]
Message = dict


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body: int):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        detail = {"detail": f"请求体过大，限制 {self.max_body} bytes"}
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl and cl.isdigit() and int(cl) > self.max_body:
            await JSONResponse(detail, status_code=413)(scope, receive, send)
            return
        total = 0
        response_started = False

        async def wrapped_receive() -> Message:
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body") or b"")
                if total > self.max_body:
                    raise _BodyTooLarge()
            return msg

        async def wrapped_send(msg: Message) -> None:
            nonlocal response_started
            if msg["type"] == "http.response.start":
                response_started = True
            await send(msg)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except _BodyTooLarge:
            if not response_started:
                await JSONResponse(detail, status_code=413)(scope, receive, send)


class AuthLockdownMiddleware:
    """公开绑定但未设 Key 时除 /health 外一律 403。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if should_lockdown(scope) and path not in ("/health", "/health/"):
            await JSONResponse({"detail": "绑定非本机地址时必须设置 local_api_key 或 admin_api_key"}, status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.httpx_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15, read=300, write=60, pool=15),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    yield
    await app.state.httpx_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Gemini Gateway",
        version=__version__,
        description="基于 Antigravity 余额的本地多协议 Gemini 网关",
        lifespan=lifespan,
    )

    max_body = int(os.getenv("GEMINI_GATEWAY_MAX_BODY", str(10 * 1024 * 1024)))
    app.add_middleware(BodySizeLimitMiddleware, max_body=max_body)
    app.add_middleware(AuthLockdownMiddleware)

    allowed_origins = [o.strip() for o in os.getenv("GEMINI_GATEWAY_CORS_ORIGINS", "").split(",") if o.strip()]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health():
        from .antigravity_auth import antigravity_auth

        cfg = config_manager.config.server
        try:
            sess = await antigravity_auth.get_session()
            logged_in = bool(sess.access_token)
        except Exception:
            logged_in = False

        return {
            "ok": True,
            "version": __version__,
            "logged_in": logged_in,
            "models": len(cfg.models),
            "host": effective_bind_host(),
            "port": cfg.port,
        }

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/v1/"):
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            inbound = "messages" if request.url.path.rstrip("/").endswith("messages") else "chat"
            if request.url.path.rstrip("/").endswith("responses"):
                inbound = "responses"
            return JSONResponse(error_payload(exc.status_code, detail, inbound=inbound), status_code=exc.status_code)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.include_router(manage_router)
    app.include_router(proxy_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()


def run():
    import uvicorn

    cfg = config_manager.config.server
    uvicorn.run(
        "app.main:app",
        host=effective_bind_host(),
        port=cfg.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
