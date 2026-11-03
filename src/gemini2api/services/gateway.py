"""Gateway: runs driver calls on pooled accounts with automatic failover."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from ..accounts.model import Account
from ..accounts.pool import AccountPool, Lease
from ..drivers.base import (
    ChatRequest,
    ChatThought,
    GeminiDriver,
    ImageRequest,
    MediaResult,
    VideoRequest,
)
from ..drivers.extension_bridge import extension_bridge
from ..errors import UpstreamError
from .request_log import note_account

log = logging.getLogger(__name__)
T = TypeVar("T")

_ANONYMOUS = Account(id="anonymous", label="driver without accounts")


class Gateway:
    def __init__(
        self, pool: AccountPool, driver: GeminiDriver, *, max_failover: int = 2
    ) -> None:
        self.pool = pool
        self.driver = driver
        self.max_failover = max_failover

    @asynccontextmanager
    async def _lease(
        self, exclude: list[str], hint: str | None, kind: str | None = None
    ) -> AsyncIterator[Lease]:
        if not self.driver.requires_account and not self.pool.all():
            note_account(_ANONYMOUS.id)
            yield Lease(_ANONYMOUS)
            return
        async with self.pool.lease(exclude=exclude, hint=hint, kind=kind) as lease:
            note_account(lease.account.id)
            yield lease

    def _should_retry(
        self, exc: UpstreamError, attempt: int, tried: list[str], kind: str | None = None
    ) -> bool:
        return (
            exc.retryable
            and attempt < self.max_failover
            and self.pool.has_alternative(tried, kind)
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        if self.driver.name == "browser" and extension_bridge.has_connected_client():
            note_account("chrome-extension")
            emitted_text = False
            emitted_thought = False
            ext_err: UpstreamError | None = None
            try:
                async for delta in extension_bridge.chat_stream(req):
                    if getattr(delta, "is_thought", False):
                        emitted_thought = True
                    elif str(delta).strip():
                        emitted_text = True
                    yield delta
            except UpstreamError as exc:
                ext_err = exc
                if emitted_text:
                    raise

            if not emitted_text and hasattr(self.driver, "read_latest_prompt_reply"):
                acc = self.pool.get("chrome-extension") or (
                    self.pool.all()[0] if self.pool.all() else None
                )
                if acc is not None:
                    try:
                        thought, answer = await self.driver.read_latest_prompt_reply(acc, req)
                        if answer:
                            if (
                                thought
                                and not emitted_thought
                                and req.include_reasoning
                                and req.thinking_level != "none"
                            ):
                                yield ChatThought(thought)
                            yield answer
                            return
                    except Exception as fallback_exc:  # noqa: BLE001
                        log.warning("CDP prompt reply recovery failed: %s", fallback_exc)

            if ext_err is not None:
                raise ext_err
            return

        tried: list[str] = []
        for attempt in range(self.max_failover + 1):
            async with self._lease(tried, req.conversation_hint) as lease:
                emitted = False
                try:
                    async for delta in self.driver.chat_stream(lease.account, req):
                        emitted = True
                        yield delta
                    return
                except UpstreamError as exc:
                    lease.fail(exc)
                    tried.append(lease.account.id)
                    if emitted or not self._should_retry(exc, attempt, tried):
                        raise
                    log.warning("chat failed on %s (%s), failing over", lease.account.id, exc)

    async def _run(
        self,
        fn: Callable[[Account], Awaitable[T]],
        hint: str | None = None,
        kind: str | None = None,
    ) -> T:
        tried: list[str] = []
        for attempt in range(self.max_failover + 1):
            async with self._lease(tried, hint, kind) as lease:
                try:
                    return await fn(lease.account)
                except UpstreamError as exc:
                    lease.fail(exc)
                    tried.append(lease.account.id)
                    if not self._should_retry(exc, attempt, tried, kind):
                        raise
                    log.warning("call failed on %s (%s), failing over", lease.account.id, exc)
        raise AssertionError("unreachable")

    async def generate_image(self, req: ImageRequest) -> list[MediaResult]:
        return await self._run(lambda acc: self.driver.generate_image(acc, req))

    async def generate_video(self, req: VideoRequest) -> MediaResult:
        return await self._run(lambda acc: self.driver.generate_video(acc, req), kind="video")
