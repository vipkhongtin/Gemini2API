"""Primary upstream: Google Gemini Web Chat (`https://gemini.google.com/app`),
with Google AI Studio (`https://aistudio.google.com`) supported as secondary upstream.
"""

from __future__ import annotations

from .aistudio import (
    GEMINI_REST_BASE,
    OPTIONAL_SESSION_COOKIES,
    ROTATE_COOKIES_API,
    SESSION_COOKIES,
    USER_AGENT,
    cookie_header,
    missing_session_cookies,
    renew_session,
    sapisidhash_header,
)

SITE = "https://gemini.google.com"
NEW_THREAD_URL = f"{SITE}/app"
SESSION_API = f"{SITE}/app"

AISTUDIO_SITE = "https://aistudio.google.com"
AISTUDIO_NEW_THREAD_URL = f"{AISTUDIO_SITE}/prompts/new_chat"


def new_chat_url(model: str | None = None, *, mode: str = "gemini") -> str:
    """Build the new-chat URL for the selected upstream mode (`gemini` primary, `aistudio` secondary)."""
    if mode == "aistudio":
        if model:
            return f"{AISTUDIO_NEW_THREAD_URL}?model={model}"
        return AISTUDIO_NEW_THREAD_URL
    return NEW_THREAD_URL


__all__ = [
    "AISTUDIO_NEW_THREAD_URL",
    "AISTUDIO_SITE",
    "GEMINI_REST_BASE",
    "NEW_THREAD_URL",
    "OPTIONAL_SESSION_COOKIES",
    "ROTATE_COOKIES_API",
    "SESSION_API",
    "SESSION_COOKIES",
    "SITE",
    "USER_AGENT",
    "cookie_header",
    "missing_session_cookies",
    "new_chat_url",
    "renew_session",
    "sapisidhash_header",
]
