"""Persistent multi-turn chat session store.

Maps each conversation (from the Web Dashboard or any OpenAI-compatible client)
to its Google AI Studio thread URL (`https://aistudio.google.com/prompts/<id>`)
and Chrome tab ID so that:
- Every new chat automatically starts a new chat on Google AI Studio.
- Continuing an active chat sends only the new message on the same thread.
- Reopening an old chat automatically navigates back to (or restores) that chat
  session and continues it seamlessly.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _norm(text: str) -> str:
    return " ".join((text or "").split()).strip()


@dataclass
class SessionResolution:
    session: ChatSession
    is_new_chat: bool
    delta_prompt: str
    full_prompt: str


@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: "sess_" + uuid.uuid4().hex[:12])
    title: str = "Cuộc trò chuyện mới"
    model: str = "gemini-4-argon"
    thinking_level: str = "high"
    thread_url: str = ""
    tab_id: int | None = None
    hint: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def user_texts(self) -> list[str]:
        return [_norm(m.get("content", "")) for m in self.messages if m.get("role") == "user"]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "thread_url": self.thread_url,
            "tab_id": self.tab_id,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def detail(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "messages": self.messages,
        }


class SessionStore:
    """Persists chat sessions to ``data/chat_sessions.json``."""

    def __init__(self, path: Path, *, max_sessions: int = 200) -> None:
        self._path = path
        self._max_sessions = max_sessions
        self._sessions: dict[str, ChatSession] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        async with self._lock:
            if not self._path.is_file():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict) and item.get("id"):
                            sess = ChatSession(
                                id=str(item["id"]),
                                title=str(item.get("title") or "Cuộc trò chuyện"),
                                model=str(item.get("model") or "gemini-4-argon"),
                                thinking_level=str(item.get("thinking_level") or "high"),
                                thread_url=str(item.get("thread_url") or ""),
                                tab_id=item.get("tab_id"),
                                hint=str(item.get("hint") or ""),
                                messages=list(item.get("messages") or []),
                                created_at=float(item.get("created_at") or time.time()),
                                updated_at=float(item.get("updated_at") or time.time()),
                            )
                            self._sessions[sess.id] = sess
            except Exception:
                return

    async def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        if len(ordered) > self._max_sessions:
            ordered = ordered[: self._max_sessions]
            self._sessions = {s.id: s for s in ordered}
        payload = [s.detail() for s in ordered]
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        tmp = self._path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_bytes(data)
        try:
            os.replace(tmp, self._path)
        except PermissionError:
            self._path.write_bytes(data)
            tmp.unlink(missing_ok=True)

    def list_summaries(self) -> list[dict[str, Any]]:
        ordered = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        return [s.summary() for s in ordered]

    def get(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    async def create(
        self,
        *,
        title: str = "Cuộc trò chuyện mới",
        model: str = "gemini-4-argon",
        thinking_level: str = "high",
    ) -> ChatSession:
        async with self._lock:
            sess = ChatSession(title=title, model=model, thinking_level=thinking_level)
            self._sessions[sess.id] = sess
            await self.save()
            return sess

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            removed = self._sessions.pop(session_id, None)
            if removed is not None:
                await self.save()
                return True
            return False

    async def resolve_for_chat(
        self,
        turns: list[tuple[str, str]],
        full_prompt: str,
        *,
        conversation_hint: str | None = None,
        model: str = "gemini-4-argon",
        thinking_level: str = "high",
    ) -> SessionResolution:
        """Determine whether this request starts a new chat or continues/reopens an existing one."""
        async with self._lock:
            user_turns = [(r, t) for r, t in turns if r in ("user", "assistant")]
            last_user_text = ""
            for r, t in reversed(turns):
                if r == "user":
                    last_user_text = t
                    break
            if not last_user_text:
                last_user_text = full_prompt

            # 1. Explicit session ID passed via user / conversation_hint (e.g. "local:sess_xxx" or "sess_xxx")
            explicit_sess: ChatSession | None = None
            if conversation_hint:
                raw_id = conversation_hint.split(":", 1)[-1] if ":" in conversation_hint else conversation_hint
                if raw_id in self._sessions:
                    explicit_sess = self._sessions[raw_id]
                else:
                    for s in self._sessions.values():
                        if s.hint == conversation_hint:
                            explicit_sess = s
                            break

            # If explicit session was created via "+ New Chat" and has 0 messages yet:
            if explicit_sess is not None and len(explicit_sess.messages) == 0:
                explicit_sess.model = model
                explicit_sess.thinking_level = thinking_level
                if explicit_sess.title in ("Cuộc trò chuyện mới", "New Chat", ""):
                    explicit_sess.title = last_user_text[:60].strip() or "Cuộc trò chuyện mới"
                explicit_sess.updated_at = time.time()
                return SessionResolution(
                    session=explicit_sess,
                    is_new_chat=True,
                    delta_prompt=full_prompt if len(turns) > 1 else last_user_text,
                    full_prompt=full_prompt,
                )

            # If explicit session already has messages, check if this continues it
            if explicit_sess is not None and len(explicit_sess.messages) > 0:
                # If client sent only 1 user message AND did not explicitly pick a session ID,
                # that would be a new chat; if client picked explicit "sess_xxx", continue it!
                raw_id = (
                    conversation_hint.split(":", 1)[-1]
                    if conversation_hint and ":" in conversation_hint
                    else (conversation_hint or "")
                )
                if raw_id.startswith("sess_") or len(user_turns) > 1:
                    explicit_sess.model = model
                    explicit_sess.thinking_level = thinking_level
                    explicit_sess.updated_at = time.time()
                    return SessionResolution(
                        session=explicit_sess,
                        is_new_chat=False,
                        delta_prompt=last_user_text,
                        full_prompt=full_prompt,
                    )

            # 2. Match by conversation history prefix (for OpenAI clients that send messages array)
            incoming_users = [_norm(t) for r, t in user_turns if r == "user"]
            if len(incoming_users) > 1:
                prefix_users = incoming_users[:-1]
                best: ChatSession | None = None
                for s in sorted(self._sessions.values(), key=lambda x: x.updated_at, reverse=True):
                    if s.user_texts() == prefix_users:
                        best = s
                        break
                if best is not None:
                    best.model = model
                    best.thinking_level = thinking_level
                    if conversation_hint:
                        best.hint = conversation_hint
                    best.updated_at = time.time()
                    return SessionResolution(
                        session=best,
                        is_new_chat=False,
                        delta_prompt=last_user_text,
                        full_prompt=full_prompt,
                    )

            # 3. Otherwise, this is a brand-new chat!
            title = last_user_text[:60].strip() or "Cuộc trò chuyện mới"
            sess = ChatSession(
                title=title,
                model=model,
                thinking_level=thinking_level,
                hint=conversation_hint or "",
            )
            # If the client sent a multi-turn history we've never seen before, pre-populate prior turns
            if len(user_turns) > 1:
                now_ts = time.time()
                for r, t in user_turns[:-1]:
                    sess.messages.append({"role": r, "content": t, "ts": now_ts})
            self._sessions[sess.id] = sess
            return SessionResolution(
                session=sess,
                is_new_chat=True,
                delta_prompt=full_prompt,
                full_prompt=full_prompt,
            )

    async def record_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        reasoning_text: str = "",
        *,
        thread_url: str | None = None,
        tab_id: int | None = None,
    ) -> None:
        async with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return
            now_ts = time.time()
            sess.messages.append({"role": "user", "content": user_text, "ts": now_ts})
            msg: dict[str, Any] = {"role": "assistant", "content": assistant_text, "ts": now_ts}
            if reasoning_text:
                msg["reasoning_content"] = reasoning_text
            sess.messages.append(msg)
            if sess.title in ("Cuộc trò chuyện mới", "New Chat", "") and user_text.strip():
                sess.title = user_text.strip()[:60]
            if thread_url:
                sess.thread_url = thread_url
            if tab_id is not None:
                sess.tab_id = tab_id
            sess.updated_at = now_ts
            await self.save()
