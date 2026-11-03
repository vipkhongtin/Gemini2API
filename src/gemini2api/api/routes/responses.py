"""OpenAI Responses API (``/v1/responses``)."""

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
from pydantic import BaseModel, ConfigDict

from ...core.media import load_image_ref
from ...core.models import resolve_model, resolve_thinking_level
from ...core.prompt import flatten_messages, message_turns
from ...drivers.base import ChatRequest, InputImage
from ...errors import Gemini2APIError, InvalidRequest
from ...services.container import Services
from ..deps import conversation_key, get_services, require_api_key

log = logging.getLogger(__name__)
router = APIRouter(tags=["responses"], dependencies=[Depends(require_api_key)])


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _sse(payload: dict | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


class ResponseRequest(BaseModel):
    model: str | None = None
    input: Any = None
    instructions: str | None = None
    stream: bool = False
    user: str | None = None
    max_output_tokens: int | None = None
    reasoning: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


_TOOL_ITEMS = {
    "function_call",
    "function_call_output",
    "reasoning",
    "computer_call",
    "file_search_call",
    "web_search_call",
}


def _to_messages(body: ResponseRequest) -> list[dict]:
    messages: list[dict] = []
    if body.instructions:
        messages.append({"role": "system", "content": body.instructions})
    inp = body.input
    if inp is None or (isinstance(inp, str) and not inp.strip()):
        pass
    elif isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                raise InvalidRequest("input items must be objects")
            itype = item.get("type")
            if itype in _TOOL_ITEMS:
                raise InvalidRequest(
                    f"input item type '{itype}' is not supported: "
                    "upstream has no function calling"
                )
            if "role" in item or itype in ("message", None):
                messages.append(
                    {"role": item.get("role", "user"), "content": item.get("content")}
                )
            else:
                raise InvalidRequest(f"unsupported input item type '{itype}'")
    else:
        raise InvalidRequest("input must be a string or a list of items")
    return messages


def _message_item(msg_id: str, text: str) -> dict:
    return {
        "id": msg_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "annotations": [], "text": text}],
    }


def _usage(input_text: str, output_text: str, reasoning_text: str = "") -> dict:
    i, o = _approx_tokens(input_text), _approx_tokens(output_text)
    r = len(reasoning_text) // 4 if reasoning_text else 0
    return {
        "input_tokens": i,
        "output_tokens": o + r,
        "total_tokens": i + o + r,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": r},
    }


def _response_object(
    resp_id: str,
    msg_id: str,
    created: int,
    model: str,
    text: str,
    input_text: str,
    reasoning_text: str = "",
    *,
    status: str = "completed",
) -> dict:
    return {
        "id": resp_id,
        "object": "response",
        "created_at": created,
        "model": model,
        "status": status,
        "output": [_message_item(msg_id, text)] if status == "completed" else [],
        "usage": _usage(input_text, text, reasoning_text),
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


@router.post("/v1/responses")
async def create_response(
    body: ResponseRequest, request: Request, svc: Services = Depends(get_services)
):
    request.state.model = body.model
    spec = resolve_model(body.model, "chat")
    request.state.model = body.model or spec.id
    thinking_level = resolve_thinking_level(
        spec,
        reasoning_effort=(body.reasoning or {}).get("effort"),
        thinking=body.reasoning,
        default_level=svc.settings.default_thinking_level,
    )
    messages = _to_messages(body)
    flat = flatten_messages(messages)
    if not flat.text and not flat.images:
        raise InvalidRequest("input contains no text or images")

    images = [InputImage(*(await load_image_ref(ref))) for ref in flat.images]
    cancel = asyncio.Event()
    req = ChatRequest(
        prompt=flat.text,
        model=spec.id,
        upstream_model=spec.upstream_id,
        thinking_level=thinking_level,
        images=images,
        timeout=svc.settings.chat_timeout,
        first_token_timeout=svc.settings.first_token_timeout,
        conversation_hint=conversation_key(request, body.user),
        turns=message_turns(messages),
        cancel=cancel,
    )
    stream = svc.gateway.chat_stream(req)
    resp_id = "resp_" + uuid.uuid4().hex[:24]
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = body.model or spec.id

    if not body.stream:
        thoughts: list[str] = []
        texts: list[str] = []
        async for d in stream:
            if getattr(d, "is_thought", False):
                thoughts.append(str(d))
            else:
                texts.append(str(d))
        return _response_object(
            resp_id,
            msg_id,
            created,
            model_name,
            "".join(texts),
            flat.text,
            "".join(thoughts),
        )

    async def events(first: str | None) -> AsyncIterator[bytes]:
        seq = 0
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
            yield _sse(
                {
                    "type": "response.created",
                    "sequence_number": seq,
                    "response": _response_object(
                        resp_id, msg_id, created, model_name, "", flat.text, status="in_progress"
                    ),
                }
            )
            seq += 1
            yield _sse(
                {
                    "type": "response.in_progress",
                    "sequence_number": seq,
                    "response": _response_object(
                        resp_id, msg_id, created, model_name, "", flat.text, status="in_progress"
                    ),
                }
            )
            seq += 1
            yield _sse(
                {
                    "type": "response.output_item.added",
                    "sequence_number": seq,
                    "output_index": 0,
                    "item": {
                        "id": msg_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }
            )
            seq += 1
            yield _sse(
                {
                    "type": "response.content_part.added",
                    "sequence_number": seq,
                    "item_id": msg_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }
            )
            seq += 1
            produced = ""
            reasoning_acc = ""

            def delta_event(delta: str) -> dict:
                return {
                    "type": "response.output_text.delta",
                    "sequence_number": seq,
                    "item_id": msg_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": delta,
                    "logprobs": [],
                }

            if first:
                if getattr(first, "is_thought", False):
                    reasoning_acc += str(first)
                else:
                    produced += str(first)
                    yield _sse(delta_event(produced))
                    seq += 1
            async for delta in stream:
                if getattr(delta, "is_thought", False):
                    reasoning_acc += str(delta)
                    continue
                s = str(delta)
                produced += s
                yield _sse(delta_event(s))
                seq += 1
            yield _sse(
                {
                    "type": "response.output_item.done",
                    "sequence_number": seq,
                    "output_index": 0,
                    "item": _message_item(msg_id, produced),
                }
            )
            seq += 1
            yield _sse(
                {
                    "type": "response.completed",
                    "sequence_number": seq,
                    "response": _response_object(
                        resp_id, msg_id, created, model_name, produced, flat.text, reasoning_acc
                    ),
                }
            )
        except Gemini2APIError as exc:
            yield _sse(exc.to_body())
        except Exception:  # noqa: BLE001
            log.exception("responses stream crashed")
            yield _sse({"error": {"message": "internal error", "type": "server_error"}})
        finally:
            cancel.set()
            await stream.aclose()

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
