from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...core.media import load_image_ref
from ...core.models import resolve_model, resolve_thinking_level
from ...core.prompt import flatten_messages, message_turns
from ...drivers.base import ChatRequest, InputImage
from ...errors import Gemini2APIError, InvalidRequest
from ...services.container import Services
from ..deps import conversation_key, get_services, require_api_key
from ..schemas import ChatCompletionRequest

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"], dependencies=[Depends(require_api_key)])


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _sse(payload: dict | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest, request: Request, svc: Services = Depends(get_services)
):
    request.state.model = body.model  # logged even if it does not resolve
    spec = resolve_model(body.model, "chat")
    request.state.model = body.model or spec.id
    thinking_level = resolve_thinking_level(
        spec,
        reasoning_effort=body.reasoning_effort,
        thinking=body.thinking,
        default_level=svc.settings.default_thinking_level,
    )
    flat = flatten_messages([m.model_dump() for m in body.messages])
    if not flat.text and not flat.images:
        raise InvalidRequest("messages contain no text or images")

    images = [InputImage(*(await load_image_ref(ref))) for ref in flat.images]
    turns = message_turns([m.model_dump() for m in body.messages])
    conv_hint = conversation_key(request, body.user)
    resolved = await svc.sessions.resolve_for_chat(
        turns,
        flat.text,
        conversation_hint=conv_hint,
        model=spec.id,
        thinking_level=thinking_level,
    )
    last_user_text = next((t for r, t in reversed(turns) if r == "user"), flat.text)

    cancel = asyncio.Event()
    req = ChatRequest(
        prompt=flat.text,
        model=spec.id,
        upstream_model=spec.upstream_id,
        thinking_level=thinking_level,
        include_reasoning=body.include_reasoning,
        images=images,
        timeout=svc.settings.chat_timeout,
        first_token_timeout=svc.settings.first_token_timeout,
        conversation_hint=conv_hint,
        turns=turns,
        session_id=resolved.session.id,
        new_chat=resolved.is_new_chat,
        thread_url=resolved.session.thread_url,
        tab_id=resolved.session.tab_id,
        delta_prompt=resolved.delta_prompt,
        cancel=cancel,
    )
    completion_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = body.model or spec.id
    stream = svc.gateway.chat_stream(req)

    if not body.stream:
        thoughts: list[str] = []
        texts: list[str] = []
        async for d in stream:
            if getattr(d, "is_thought", False):
                thoughts.append(str(d))
            else:
                texts.append(str(d))
        reasoning = "".join(thoughts)
        raw_text = "".join(texts)
        await svc.sessions.record_turn(
            resolved.session.id,
            last_user_text,
            raw_text,
            reasoning,
            thread_url=req.result_meta.get("thread_url"),
            tab_id=req.result_meta.get("tab_id"),
        )
        text = raw_text
        if svc.settings.think_tags and reasoning:
            text = f"<think>\n{reasoning}\n</think>\n\n{text}"
        msg: dict[str, Any] = {"role": "assistant", "content": text}
        if reasoning and body.include_reasoning:
            msg["reasoning_content"] = reasoning
        prompt_toks = max(1, len(flat.text) // 4)
        comp_toks = max(1, (len(text) + len(reasoning)) // 4)
        reasoning_toks = _approx_tokens(reasoning)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "session_id": resolved.session.id,
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_toks,
                "completion_tokens": comp_toks,
                "total_tokens": prompt_toks + comp_toks,
                "completion_tokens_details": {"reasoning_tokens": reasoning_toks},
            },
        }

    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "session_id": resolved.session.id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    async def events(first: str | None) -> AsyncIterator[bytes]:
        try:
            if first is None and svc.settings.stream_heartbeat_interval > 0:
                pending = asyncio.ensure_future(anext(stream))
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {pending}, timeout=svc.settings.stream_heartbeat_interval
                        )
                        if done:
                            try:
                                first = pending.result()
                            except StopAsyncIteration:
                                first = ""
                            break
                        yield b": keepalive\n\n"
                finally:
                    if not pending.done():
                        pending.cancel()

            produced_text = ""
            produced_reasoning = ""
            in_think_tag = False
            if first is not None and getattr(first, "is_thought", False):
                s = str(first)
                produced_reasoning += s
                if svc.settings.think_tags:
                    in_think_tag = True
                    content_piece = f"<think>\n{s}"
                else:
                    content_piece = ""
                yield _sse(
                    chunk(
                        {
                            "role": "assistant",
                            "content": content_piece,
                            "reasoning_content": s,
                        }
                    )
                )
            else:
                produced_text = str(first or "")
                yield _sse(chunk({"role": "assistant", "content": produced_text}))

            async for delta in stream:
                if getattr(delta, "is_thought", False):
                    s = str(delta)
                    produced_reasoning += s
                    d_payload: dict[str, Any] = {"reasoning_content": s}
                    if svc.settings.think_tags:
                        if not in_think_tag:
                            in_think_tag = True
                            d_payload["content"] = f"<think>\n{s}"
                        else:
                            d_payload["content"] = s
                    yield _sse(chunk(d_payload))
                else:
                    s = str(delta)
                    if in_think_tag:
                        in_think_tag = False
                        s = f"\n</think>\n\n{s}"
                    produced_text += s
                    yield _sse(chunk({"content": s}))
            if in_think_tag:
                yield _sse(chunk({"content": "\n</think>\n\n"}))

            await svc.sessions.record_turn(
                resolved.session.id,
                last_user_text,
                produced_text,
                produced_reasoning,
                thread_url=req.result_meta.get("thread_url"),
                tab_id=req.result_meta.get("tab_id"),
            )
            yield _sse(chunk({}, "stop"))
            if (body.stream_options or {}).get("include_usage"):
                p_toks = max(1, len(flat.text) // 4)
                c_toks = max(1, (len(produced_text) + len(produced_reasoning)) // 4)
                r_toks = _approx_tokens(produced_reasoning)
                usage = {
                    "prompt_tokens": p_toks,
                    "completion_tokens": c_toks,
                    "total_tokens": p_toks + c_toks,
                    "completion_tokens_details": {"reasoning_tokens": r_toks},
                }
                yield _sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [],
                        "usage": usage,
                    }
                )
        except Gemini2APIError as exc:
            yield _sse(exc.to_body())
        except Exception:  # noqa: BLE001
            log.exception("stream crashed")
            yield _sse({"error": {"message": "internal error", "type": "server_error"}})
        finally:
            cancel.set()
            await stream.aclose()
        yield _sse("[DONE]")

    if svc.settings.stream_heartbeat_interval > 0:
        return StreamingResponse(
            events(None),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = ""

    return StreamingResponse(
        events(first),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
