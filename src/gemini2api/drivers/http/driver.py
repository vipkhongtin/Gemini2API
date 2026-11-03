"""Direct HTTP protocol driver for Google AI Studio / Gemini API.

Supports:
1. Direct streaming via Google's ``generativelanguage.googleapis.com`` SSE endpoint when
   an account carries ``cookies["api_key"]`` (including ``thinkingConfig`` for Gemini 4 Argon).
2. Cookie-based session renewal via ``aistudio.renew_session``.
3. Raises ``FeatureNotImplemented`` (HTTP 501) when chat is invoked without an ``api_key``
   in HTTP driver mode, directing callers to ``driver=browser`` for cookie-only accounts.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ...accounts.model import Account
from ...config import Settings
from ...errors import (
    FeatureNotImplemented,
    UpstreamAuthError,
    UpstreamError,
    UpstreamQuotaError,
)
from ...upstream import aistudio
from ..base import ChatRequest, ChatThought, DriverCapabilities, GeminiDriver, SessionInfo

_THINKING_BUDGETS = {
    "low": 1024,
    "medium": 8192,
    "high": 24576,
}


class HttpDriver(GeminiDriver):
    name = "http"
    capabilities = DriverCapabilities(
        chat=True, chat_images=True, thinking=True, renew_session=True
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def chat_stream(self, account: Account, req: ChatRequest) -> AsyncIterator[str]:
        api_key = (account.cookies or {}).get("api_key", "").strip()
        if not api_key:
            raise FeatureNotImplemented(
                "http driver chat is not implemented for cookie-only accounts yet; "
                "use driver=browser or add 'api_key' to account cookies"
            )

        parts: list[dict[str, Any]] = []
        for img in req.images:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img.mime,
                        "data": base64.b64encode(img.data).decode(),
                    }
                }
            )
        if req.prompt:
            parts.append({"text": req.prompt})

        gen_cfg: dict[str, Any] = {}
        if req.thinking_level and req.thinking_level != "none":
            gen_cfg["thinkingConfig"] = {
                "includeThoughts": bool(req.include_reasoning),
                "thinkingBudget": _THINKING_BUDGETS.get(req.thinking_level, 24576),
            }

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
        }
        if gen_cfg:
            payload["generationConfig"] = gen_cfg

        url = (
            f"{aistudio.GEMINI_REST_BASE}/models/{req.upstream_model}"
            f":streamGenerateContent?alt=sse&key={api_key}"
        )

        async with httpx.AsyncClient(timeout=req.timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code in (401, 403):
                    raise UpstreamAuthError(
                        f"Google AI Studio API key rejected (HTTP {resp.status_code})"
                    )
                if resp.status_code == 429:
                    raise UpstreamQuotaError("Google AI Studio API key rate limit exceeded (429)")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise UpstreamError(f"upstream HTTP {resp.status_code}: {body}")

                async for line in resp.aiter_lines():
                    if req.cancel and req.cancel.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    for cand in chunk.get("candidates") or []:
                        content = cand.get("content") or {}
                        for part in content.get("parts") or []:
                            text = part.get("text")
                            if not text:
                                continue
                            if part.get("thought"):
                                if req.include_reasoning:
                                    yield ChatThought(text)
                            else:
                                yield text

    async def renew_session(self, account: Account) -> SessionInfo:
        return await aistudio.renew_session(account.cookies)
