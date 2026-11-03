"""Facts about the Google AI Studio (aistudio.google.com) web app shared by all drivers.

Everything here was observed from the Google AI Studio web client and Google's
cookie/session architecture. Keep upstream-specific constants in this module so
that a site update is a one-file fix.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx

from ..drivers.base import SessionInfo
from ..errors import UpstreamAuthError, UpstreamError

SITE = "https://aistudio.google.com"
NEW_THREAD_URL = f"{SITE}/prompts/new_chat"
SESSION_API = f"{SITE}/prompts/new_chat"
ROTATE_COOKIES_API = "https://accounts.google.com/RotateCookies"
VM_WAKE_API = f"{SITE}/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ResolveDriveResource"
GEMINI_REST_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Core Google session cookies required for a logged-in Google AI Studio session.
SESSION_COOKIES = ("SID", "__Secure-1PSID")
OPTIONAL_SESSION_COOKIES = (
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-3PSID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "SIDCC",
    "__Secure-1PSIDCC",
    "OSID",
    "NID",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

WAKE_CONFIRM_SECONDS = 20.0
WAKE_POLL_SECONDS = 2.0


def new_chat_url(model: str | None = None) -> str:
    """Build the Google AI Studio new-chat URL, optionally pre-selecting ``model``."""
    if model:
        return f"{NEW_THREAD_URL}?model={model}"
    return NEW_THREAD_URL


def wake_confirm_seconds() -> float:
    """``GEMINI2API_WAKE_CONFIRM_S``."""
    raw = os.environ.get("GEMINI2API_WAKE_CONFIRM_S") or os.environ.get(
        "MUSE2API_WAKE_CONFIRM_S", WAKE_CONFIRM_SECONDS
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return WAKE_CONFIRM_SECONDS


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v and k != "api_key")


def missing_session_cookies(cookies: dict[str, str]) -> list[str]:
    """Return missing required cookies.

    Accounts that supply a direct ``api_key`` (for the HTTP driver) need no browser
    cookies.
    """
    if cookies.get("api_key"):
        return []
    return [name for name in SESSION_COOKIES if not cookies.get(name)]


def sapisidhash_header(cookies: dict[str, str], origin: str = SITE) -> str | None:
    """Compute Google's ``SAPISIDHASH`` Authorization header for MakerSuite/AI Studio RPCs."""
    sapisid = (
        cookies.get("SAPISID")
        or cookies.get("__Secure-3PAPISID")
        or cookies.get("__Secure-1PAPISID")
    )
    if not sapisid:
        return None
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def _merge_cookies(resp: httpx.Response, jar: dict[str, str], expires: dict[str, int]) -> None:
    for c in resp.cookies.jar:
        if c.value:
            jar[c.name] = c.value
        if c.expires:
            expires[c.name] = int(c.expires)


def _json_body(resp: httpx.Response) -> dict:
    if not resp.headers.get("content-type", "").startswith("application/json"):
        return {}
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def renew_session(
    cookies: dict[str, str],
    *,
    wake_vm: bool = True,
    timeout: float = 15.0,
    confirm_s: float | None = None,
    poll_s: float = WAKE_POLL_SECONDS,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SessionInfo:
    """Verify the Google AI Studio session and rotate short-lived cookies (``__Secure-1PSIDTS``,
    ``SIDCC``, etc.) via ``Set-Cookie``.

    Also preserves full compatibility with workspace/VM wake confirmation when upstream
    returns ``vm_id`` / ``vm_state`` metadata.
    """
    if cookies.get("api_key") and not any(cookies.get(k) for k in SESSION_COOKIES):
        return SessionInfo(
            ok=True,
            cookies=dict(cookies),
            cookie_expires={},
            meta={"session_status": "api_key", "vm_id": None, "vm_state": "RUNNING"},
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Origin": SITE,
        "Referer": NEW_THREAD_URL,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }
    if auth := sapisidhash_header(cookies):
        headers["Authorization"] = auth

    jar = dict(cookies)
    expires: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False) as client:
        resp = await client.get(SESSION_API, headers={"Cookie": cookie_header(jar)})
        if resp.status_code in (401, 403):
            raise UpstreamAuthError(f"session rejected by upstream (HTTP {resp.status_code})")
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            if "accounts.google.com" in loc or "ServiceLogin" in loc:
                raise UpstreamAuthError("Google AI Studio redirected to login; cookies expired")
        if resp.status_code >= 400:
            raise UpstreamError(f"session endpoint returned HTTP {resp.status_code}")

        _merge_cookies(resp, jar, expires)
        body = _json_body(resp)
        default_status = "assigned" if resp.status_code < 400 and not body else None
        meta = {
            key: (str(body[src])[:128] if body.get(src) is not None else default)
            for key, src, default in (
                ("session_status", "status", default_status),
                ("vm_id", "vm_id", None),
                ("vm_state", "vm_state", None),
            )
        }

        if wake_vm and meta["vm_id"] and meta["vm_state"] != "DISABLED":
            try:
                w = await client.post(
                    VM_WAKE_API,
                    headers={"Cookie": cookie_header(jar)},
                    json={
                        "vm_id": meta["vm_id"],
                        "retry_count": 0,
                        "connect_attempt_id": str(uuid.uuid4()),
                    },
                )
                meta["vm_wake_http"] = str(w.status_code)
            except httpx.HTTPError as exc:
                meta["vm_wake_http"] = type(exc).__name__
            meta["vm_state_after"] = await _confirm_running(
                client,
                jar,
                expires,
                timeout=timeout,
                confirm_s=wake_confirm_seconds() if confirm_s is None else confirm_s,
                poll_s=poll_s,
                sleep=sleep,
                clock=clock,
            )
            meta["vm_wake_ok"] = (
                None if meta["vm_state_after"] is None else meta["vm_state_after"] == "RUNNING"
            )

    return SessionInfo(
        ok=meta["session_status"] == "assigned",
        cookies=jar,
        cookie_expires=expires,
        meta=meta,
    )


async def _confirm_running(
    client: httpx.AsyncClient,
    jar: dict[str, str],
    expires: dict[str, int],
    *,
    timeout: float,
    confirm_s: float,
    poll_s: float,
    sleep: Callable[[float], Awaitable[object]],
    clock: Callable[[], float],
) -> str | None:
    """Poll the session endpoint until the workspace is ``RUNNING`` or ``confirm_s`` passes."""
    deadline = clock() + confirm_s
    state: str | None = None
    while True:
        remaining = deadline - clock()
        try:
            resp = await client.get(
                SESSION_API,
                headers={"Cookie": cookie_header(jar)},
                timeout=max(1.0, min(timeout, remaining)),
            )
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code < 400:
            _merge_cookies(resp, jar, expires)
            if (seen := _json_body(resp).get("vm_state")) is not None:
                state = str(seen)[:128]
                if state == "RUNNING":
                    return state
        if clock() + poll_s > deadline:
            return state
        await sleep(poll_s)
