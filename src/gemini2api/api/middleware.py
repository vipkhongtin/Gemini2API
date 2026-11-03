"""ASGI middleware that writes one request-log row per ``/v1/*`` call."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..services.request_log import RequestLog, current_record

_SKIP_PREFIXES = ("/v1/media/",)
_POLL_RE = re.compile(r"^/v1/(?:images/generations|videos)/([^/]+)$")
_MAX_ERROR_BODY = 8192
_MAX_ERROR_LEN = 300


def client_ip(scope: Scope) -> str | None:
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
    if ip := headers.get("cf-connecting-ip", "").strip():
        return ip
    if fwd := headers.get("x-forwarded-for", "").split(",")[0].strip():
        return fwd
    if ip := headers.get("x-real-ip", "").strip():
        return ip
    client = scope.get("client")
    return client[0] if client else None


def _error_message(body: bytes) -> str | None:
    try:
        data = json.loads(body)
    except ValueError:
        text = body.decode("utf-8", "replace").strip()
        return text[:_MAX_ERROR_LEN] or None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    msg = err.get("message") if isinstance(err, dict) else err or data.get("detail")
    return str(msg)[:_MAX_ERROR_LEN] if msg else None


def _sse_error(chunk: bytes) -> str | None:
    for line in chunk.split(b"\n"):
        if line.startswith(b'data: {"error"'):
            return _error_message(line[6:])
    return None


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp, request_log: RequestLog) -> None:
        self.app = app
        self.request_log = request_log
        self._pending: set[asyncio.Task] = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/v1/") or path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        state: dict[str, Any] = scope.setdefault("state", {})
        poll = _POLL_RE.match(path) if scope["method"] == "GET" else None
        record: dict[str, Any] = {
            "ts": time.time(),
            "method": scope["method"],
            "path": path,
            "poll": int(bool(poll)),
            "task_id": poll.group(1) if poll else None,
            "status_code": 500,
            "stream": 0,
            "client_ip": client_ip(scope),
        }
        for k, v in scope.get("headers", []):
            if k == b"user-agent":
                record["user_agent"] = v.decode("latin-1")[:200]
        error_body = bytearray()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                record["status_code"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type" and v.startswith(b"text/event-stream"):
                        record["stream"] = 1
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if record["status_code"] >= 400:
                    if len(error_body) < _MAX_ERROR_BODY:
                        error_body.extend(body[: _MAX_ERROR_BODY - len(error_body)])
                elif record["stream"] and not record.get("error") and b'"error"' in body:
                    record["error"] = _sse_error(body)
            await send(message)

        token = current_record.set(record)
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            record["status_code"] = 500
            record["error"] = (str(exc) or type(exc).__name__)[:_MAX_ERROR_LEN]
            raise
        finally:
            current_record.reset(token)
            record["latency_ms"] = int((time.perf_counter() - started) * 1000)
            if error_body and not record.get("error"):
                record["error"] = _error_message(bytes(error_body))
            for field in ("model", "key_id", "key_name", "task_id"):
                if state.get(field) is not None:
                    record[field] = state[field]
            task = asyncio.create_task(self.request_log.add(record))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
            await asyncio.shield(task)
