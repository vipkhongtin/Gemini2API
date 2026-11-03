"""WebSocket bridge between ``gemini2api`` and the Chrome Extension running in the
user's real Chrome browser.

When the user installs the Chrome Extension and opens ``https://aistudio.google.com``,
the extension's background service worker connects to ``/ws/extension``. Incoming
``/v1/chat/completions`` and ``/v1/responses`` requests can then be executed directly
inside the user's real Chrome tab on Google AI Studio — requiring no Google API key
and bypassing headless browser login/BotGuard challenges.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import WebSocket

from ..drivers.base import ChatRequest, ChatThought
from ..errors import UpstreamError, UpstreamQuotaError, UpstreamTimeout

log = logging.getLogger(__name__)

_MODEL_TURN_HEADER_RE = re.compile(
    r"^(?:(?:edit|more_vert)\s*)*(?:Model[\s\u00a0\u202f]+\d{1,2}:\d{2}(?:[\s\u00a0\u202f]*(?:AM|PM|SA|CH))?\s*)?",
    re.IGNORECASE,
)


class ExtensionBridge:
    """Manages active Chrome Extension WebSocket connections and dispatches chat jobs."""

    def __init__(self) -> None:
        self._clients: dict[str, WebSocket] = {}
        self._client_meta: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    def has_connected_client(self) -> bool:
        return bool(self._clients)

    def status(self) -> dict[str, Any]:
        clients = []
        for cid, meta in self._client_meta.items():
            clients.append(
                {
                    "id": cid,
                    "connected_at": meta.get("connected_at"),
                    "last_seen_at": meta.get("last_seen_at"),
                    "aistudio_tabs": meta.get("aistudio_tabs", 0),
                    "has_cookies": meta.get("has_cookies", False),
                    "user_agent": meta.get("user_agent", ""),
                }
            )
        return {
            "connected": bool(self._clients),
            "client_count": len(self._clients),
            "clients": clients,
        }

    async def register(self, ws: WebSocket) -> str:
        await ws.accept()
        cid = "ext_" + uuid.uuid4().hex[:8]
        now = time.time()
        async with self._lock:
            self._clients[cid] = ws
            self._client_meta[cid] = {
                "connected_at": now,
                "last_seen_at": now,
                "aistudio_tabs": 0,
                "has_cookies": False,
            }
        log.info("Chrome extension connected: %s", cid)
        return cid

    async def unregister(self, cid: str) -> None:
        async with self._lock:
            self._clients.pop(cid, None)
            self._client_meta.pop(cid, None)
        log.info("Chrome extension disconnected: %s", cid)

    def update_meta(self, cid: str, data: dict[str, Any]) -> None:
        meta = self._client_meta.get(cid)
        if meta is not None:
            meta["last_seen_at"] = time.time()
            if "aistudio_tabs" in data:
                meta["aistudio_tabs"] = int(data.get("aistudio_tabs") or 0)
            if "has_cookies" in data:
                meta["has_cookies"] = bool(data.get("has_cookies"))
            if "user_agent" in data:
                meta["user_agent"] = str(data.get("user_agent") or "")[:120]

    async def handle_message(self, cid: str, msg: dict[str, Any]) -> None:
        self.update_meta(cid, msg)
        req_id = msg.get("request_id")
        if req_id and req_id in self._pending:
            await self._pending[req_id].put(msg)

    def _pick_client(self) -> tuple[str, WebSocket]:
        if not self._clients:
            raise UpstreamError(
                "Chrome Extension chưa kết nối. Hãy cài đặt tiện ích từ thư mục "
                "'chrome-extension' vào Chrome và mở https://aistudio.google.com."
            )
        # Prefer the most recently active extension client
        cid = max(
            self._clients.keys(),
            key=lambda k: (self._client_meta.get(k) or {}).get("last_seen_at", 0.0),
        )
        return cid, self._clients[cid]

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        cid, ws = self._pick_client()
        req_id = "req_" + uuid.uuid4().hex[:12]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending[req_id] = queue

        images_payload = [
            {"data_url": img.data_url, "mime": img.mime} for img in (req.images or [])
        ]
        command = {
            "type": "chat_request",
            "request_id": req_id,
            "prompt": req.delta_prompt or req.prompt,
            "full_prompt": req.prompt,
            "session_id": req.session_id,
            "new_chat": req.new_chat,
            "thread_url": req.thread_url,
            "tab_id": req.tab_id,
            "model": req.model,
            "upstream_model": req.upstream_model,
            "thinking_level": req.thinking_level,
            "include_reasoning": req.include_reasoning,
            "images": images_payload,
            "conversation_hint": req.conversation_hint or "",
            "timeout": req.timeout,
        }

        finished_done = False
        try:
            try:
                await ws.send_json(command)
            except Exception as exc:
                await self.unregister(cid)
                raise UpstreamError(f"failed to send request to Chrome Extension: {exc}") from exc

            started = time.monotonic()
            got_first = False
            stripped_header = False
            retried_same_tab = False
            while True:
                if req.cancel and req.cancel.is_set():
                    return

                elapsed = time.monotonic() - started
                # If a background tab had not enabled the Run button within 160ms on the first
                # injection, re-send once at 8s with new_chat=True so the already-fresh
                # /prompts/new_chat tab clicks the now-enabled Run button without reloading.
                if not got_first and not retried_same_tab and elapsed >= 8.0 and req.session_id:
                    retried_same_tab = True
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "cancel_request", "request_id": req_id})
                    self._pending.pop(req_id, None)
                    req_id = "req_" + uuid.uuid4().hex[:12]
                    self._pending[req_id] = queue
                    retry_cmd = {
                        **command,
                        "request_id": req_id,
                        "new_chat": True,
                    }
                    with contextlib.suppress(Exception):
                        await ws.send_json(retry_cmd)

                effective_first_timeout = min(req.first_token_timeout, 25.0)
                timeout_left = (
                    (effective_first_timeout - elapsed)
                    if not got_first
                    else (req.timeout - elapsed)
                )
                if timeout_left <= 0:
                    raise UpstreamTimeout("Google AI Studio (Chrome Extension) timed out")

                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=min(1.0, max(0.1, timeout_left))
                    )
                except TimeoutError:
                    continue

                etype = event.get("type")
                if etype == "heartbeat":
                    got_first = True
                elif etype == "thought_delta":
                    delta = event.get("delta") or ""
                    if delta:
                        got_first = True
                        if req.include_reasoning and req.thinking_level != "none":
                            yield ChatThought(delta)
                elif etype == "text_delta":
                    delta = str(event.get("delta") or "")
                    if delta:
                        got_first = True
                        if not stripped_header:
                            cleaned = _MODEL_TURN_HEADER_RE.sub("", delta)
                            if cleaned != delta:
                                stripped_header = True
                                delta = cleaned
                            elif len(delta.strip()) > 20:
                                stripped_header = True
                        if delta:
                            yield delta
                elif etype == "done":
                    finished_done = True
                    if event.get("thread_url"):
                        req.result_meta["thread_url"] = str(event["thread_url"])
                    if event.get("tab_id") is not None:
                        req.result_meta["tab_id"] = int(event["tab_id"])
                    return
                elif etype == "error":
                    err_msg = str(event.get("message") or "Chrome Extension AI Studio error")
                    if "quota" in err_msg.lower() or "rate limit" in err_msg.lower():
                        raise UpstreamQuotaError(err_msg)
                    raise UpstreamError(err_msg)
        finally:
            self._pending.pop(req_id, None)
            if not finished_done:
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "cancel_request", "request_id": req_id})


# Singleton bridge shared across the app instance
extension_bridge = ExtensionBridge()
