"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .accounts import Account, AccountStatus
from .accounts.keepalive import keepalive_loop
from .api.middleware import RequestLogMiddleware
from .api.routes import build_router
from .api.routes.admin import parse_cookies
from .config import Settings, get_settings
from .drivers.base import GeminiDriver
from .drivers.extension_bridge import extension_bridge
from .errors import Gemini2APIError
from .services.container import Services

log = logging.getLogger("gemini2api")

# How often pending key "last used" times are written and old request rows pruned.
_FLUSH_INTERVAL = 60.0
_PRUNE_INTERVAL = 3600.0


async def _housekeeping(services: Services) -> None:
    last_prune = time.monotonic()
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL)
        await services.keys.flush()
        if time.monotonic() - last_prune >= _PRUNE_INTERVAL:
            last_prune = time.monotonic()
            await services.requests.prune(services.settings.request_log_retention_days)
            removed = services.media.prune(services.settings.media_retention_days * 86400)
            if removed:
                log.info("media cleanup: removed %d expired file(s)", removed)


def create_app(settings: Settings | None = None, driver: GeminiDriver | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.resolve_api_key()
    services = Services.build(settings, driver)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await services.pool.load()
        await services.tasks.load()
        await services.keys.load()
        await services.sessions.load()
        await services.requests.open()
        await services.requests.prune(settings.request_log_retention_days)
        await services.requests.backfill_tasks(services.tasks.list(limit=services.tasks.max_kept))
        await services.driver.startup()
        for acc in services.pool.all():
            if acc.enabled and acc.status != AccountStatus.INVALID:
                services.driver.prewarm(acc)
        housekeeping = asyncio.create_task(_housekeeping(services))
        keepalive = None
        if settings.keepalive_enabled and services.driver.capabilities.renew_session:
            keepalive = asyncio.create_task(
                keepalive_loop(services.pool, services.driver, settings.keepalive_interval)
            )
        log.info("gemini2api %s ready (driver=%s)", __version__, services.driver.name)
        try:
            yield
        finally:
            for bg in (keepalive, housekeeping):
                if bg:
                    bg.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await bg
            await services.tasks.shutdown()
            await services.driver.shutdown()
            await services.keys.flush()
            await services.requests.close()

    app = FastAPI(title="gemini2api", version=__version__, lifespan=lifespan)
    app.state.services = services

    @app.exception_handler(Gemini2APIError)
    async def _gemini_error(_: Request, exc: Gemini2APIError) -> JSONResponse:
        return JSONResponse(exc.to_body(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return JSONResponse(
            {"error": {"message": msg, "type": "invalid_request_error", "code": "invalid_request"}},
            status_code=400,
        )

    app.include_router(build_router())

    @app.websocket("/ws/extension")
    async def _ws_extension(ws: WebSocket) -> None:
        cid = await extension_bridge.register(ws)
        try:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                if mtype in ("hello", "sync_cookies") and msg.get("cookies"):
                    with contextlib.suppress(Exception):
                        cookies, expires = parse_cookies(msg["cookies"])
                        cookies = {k: v for k, v in cookies.items() if v}
                        if cookies:
                            existing = services.pool.get("chrome-extension")
                            acc = existing or Account(
                                id="chrome-extension",
                                label=str(msg.get("label") or "Chrome Extension (Chat Gemini / AI Studio)"),
                            )
                            acc.cookies = cookies
                            acc.cookie_expires = expires
                            acc.status = AccountStatus.ACTIVE
                            acc.enabled = True
                            acc.last_error = None
                            await services.pool.upsert(acc)
                            extension_bridge.update_meta(cid, {"has_cookies": True})
                            await ws.send_json(
                                {
                                    "type": "cookies_synced",
                                    "account_id": acc.id,
                                    "cookie_count": len(cookies),
                                }
                            )
                await extension_bridge.handle_message(cid, msg)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("extension websocket closed (%s): %s", cid, exc)
        finally:
            await extension_bridge.unregister(cid)

    app.add_middleware(RequestLogMiddleware, request_log=services.requests)
    # The cookie-import extension calls /admin/accounts from chrome-extension://.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"(chrome-extension://.*|http://127\.0\.0\.1.*|http://localhost.*)",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
