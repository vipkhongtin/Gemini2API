"""Tests specifically covering Gemini 4 Argon thinking/reasoning modes, SAPISIDHASH,
and the HTTP driver SSE with thought chunks."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gemini2api.accounts import Account
from gemini2api.app import create_app
from gemini2api.drivers.base import ChatRequest, ChatThought
from gemini2api.drivers.http.driver import HttpDriver
from gemini2api.drivers.mock import MockDriver
from gemini2api.upstream.aistudio import sapisidhash_header


def test_gemini_4_argon_thinking_variants_non_stream(client, auth):
    # High thinking (default for gemini-4-argon)
    r = client.post("/v1/chat/completions", headers=auth, json={
        "model": "gemini-4-argon",
        "messages": [{"role": "user", "content": "solve x^2 = 4"}],
    })
    assert r.status_code == 200
    body = r.json()
    msg = body["choices"][0]["message"]
    assert "solve x^2 = 4" in msg["content"]
    assert "[thinking:gemini-4-argon:high]" in msg["reasoning_content"]
    assert body["usage"]["completion_tokens_details"]["reasoning_tokens"] > 0

    # Low thinking via model variant
    r_low = client.post("/v1/chat/completions", headers=auth, json={
        "model": "gemini-4-argon-low",
        "messages": [{"role": "user", "content": "quick check"}],
    })
    assert r_low.status_code == 200
    msg_low = r_low.json()["choices"][0]["message"]
    assert "[thinking:gemini-4-argon-low:low]" in msg_low["reasoning_content"]

    # No-think via model variant
    r_no = client.post("/v1/chat/completions", headers=auth, json={
        "model": "gemini-4-argon-nothink",
        "messages": [{"role": "user", "content": "no thinking"}],
    })
    assert r_no.status_code == 200
    msg_no = r_no.json()["choices"][0]["message"]
    assert "reasoning_content" not in msg_no


def test_gemini_4_argon_reasoning_effort_override(client, auth):
    r = client.post("/v1/chat/completions", headers=auth, json={
        "model": "gemini-4-argon",
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": "override effort"}],
    })
    assert r.status_code == 200
    msg = r.json()["choices"][0]["message"]
    assert "[thinking:gemini-4-argon:low]" in msg["reasoning_content"]

    r_off = client.post("/v1/chat/completions", headers=auth, json={
        "model": "gemini-4-argon",
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": "disable thinking"}],
    })
    assert r_off.status_code == 200
    assert "reasoning_content" not in r_off.json()["choices"][0]["message"]


def test_gemini_4_argon_think_tags_setting(settings, auth):
    settings.think_tags = True
    app = create_app(settings, MockDriver(delay=0))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers=auth, json={
            "model": "gemini-4-argon-high",
            "messages": [{"role": "user", "content": "tagged thoughts"}],
        })
        assert r.status_code == 200
        msg = r.json()["choices"][0]["message"]
        assert msg["content"].startswith("<think>\n")
        assert "</think>\n\n" in msg["content"]
        assert msg["reasoning_content"]

        # Also verify streaming with think_tags=True emits <think> and </think> in content
        with c.stream("POST", "/v1/chat/completions", headers=auth, json={
            "model": "gemini-4-argon-high",
            "messages": [{"role": "user", "content": "stream tagged"}],
            "stream": True,
        }) as sr:
            assert sr.status_code == 200
            lines = [ln[6:] for ln in sr.iter_lines() if ln.startswith("data: ")]
        chunks = [json.loads(x) for x in lines[:-1]]
        content = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks if ch["choices"])
        assert "<think>\n" in content and "\n</think>\n\n" in content


def test_sapisidhash_header():
    hdr = sapisidhash_header({"SAPISID": "my-sapisid"})
    assert hdr and hdr.startswith("SAPISIDHASH ")
    parts = hdr.split(" ")[1].split("_")
    assert len(parts) == 2
    assert parts[0].isdigit()
    assert len(parts[1]) == 40
    assert sapisidhash_header({}) is None


async def test_http_driver_streams_thoughts_and_content(settings, monkeypatch):
    sse_lines = [
        'data: {"candidates": [{"content": {"parts": [{"text": "Step 1: analyze Argon.", "thought": true}]}}]}',
        'data: {"candidates": [{"content": {"parts": [{"text": "Final Argon answer."}]}}]}',
        "data: [DONE]",
    ]
    captured_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        body = "\n".join(sse_lines) + "\n"
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "gemini2api.drivers.http.driver.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    drv = HttpDriver(settings)
    acc = Account(id="http1", cookies={"api_key": "AIzaSyFakeKey"})
    req = ChatRequest(
        prompt="Explain Argon",
        model="gemini-4-argon",
        upstream_model="models/gemini-4-argon",
        thinking_level="high",
    )
    deltas = [d async for d in drv.chat_stream(acc, req)]
    assert len(deltas) == 2
    assert isinstance(deltas[0], ChatThought)
    assert deltas[0] == "Step 1: analyze Argon."
    assert not getattr(deltas[1], "is_thought", False)
    assert deltas[1] == "Final Argon answer."
    assert captured_payloads[0]["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 24576,
    }


async def test_http_driver_without_api_key_instructs_browser_driver(settings):
    from gemini2api.errors import FeatureNotImplemented

    drv = HttpDriver(settings)
    acc = Account(id="cookie_only", cookies={"SID": "123"})
    req = ChatRequest(prompt="hi", model="gemini-4-argon")
    with pytest.raises(FeatureNotImplemented, match="driver=browser"):
        _ = [d async for d in drv.chat_stream(acc, req)]


def test_open_access_requires_no_api_key(settings):
    settings.open_access = True
    app = create_app(settings, MockDriver(delay=0))
    with TestClient(app) as c:
        assert c.get("/admin/status").status_code == 200
        assert c.get("/v1/models").status_code == 200
        r = c.post(
            "/v1/chat/completions",
            json={"model": "gemini-4-argon", "messages": [{"role": "user", "content": "no key"}]},
        )
        assert r.status_code == 200
        assert "no key" in r.json()["choices"][0]["message"]["content"]


def test_extension_websocket_syncs_cookies(settings):
    settings.open_access = True
    app = create_app(settings, MockDriver(delay=0))
    with TestClient(app) as c:
        with c.websocket_connect("/ws/extension") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "label": "My Chrome AI Studio",
                    "cookies": [{"name": "SID", "value": "sid123"}, {"name": "__Secure-1PSID", "value": "p123"}],
                    "aistudio_tabs": 1,
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "cookies_synced"
            assert ack["account_id"] == "chrome-extension"

            st = c.get("/admin/status").json()
            assert st["extension"]["connected"] is True
            assert st["accounts"]["total"] == 1


def test_session_lifecycle_new_chat_and_reopen_old_chat(client, auth, admin, monkeypatch):
    captured: list[ChatRequest] = []
    gateway = client.app.state.services.gateway
    original_stream = gateway.chat_stream

    async def spy_stream(req: ChatRequest):
        captured.append(req)
        req.result_meta["thread_url"] = f"https://aistudio.google.com/prompts/{req.session_id}"
        req.result_meta["tab_id"] = 101
        async for d in original_stream(req):
            yield d

    monkeypatch.setattr(gateway, "chat_stream", spy_stream)

    # 1. Start Chat #1 (New Chat) -> must set new_chat=True
    r1 = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "gemini-4-argon", "messages": [{"role": "user", "content": "Chat 1 turn 1"}]},
    ).json()
    sess1_id = r1["session_id"]
    reply1 = r1["choices"][0]["message"]["content"]
    assert captured[-1].new_chat is True
    assert captured[-1].session_id == sess1_id

    # 2. Start Chat #2 (Another brand new chat) -> must create a new session & set new_chat=True
    r2 = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "gemini-4-argon", "messages": [{"role": "user", "content": "Chat 2 turn 1"}]},
    ).json()
    sess2_id = r2["session_id"]
    assert sess2_id != sess1_id
    assert captured[-1].new_chat is True
    assert captured[-1].session_id == sess2_id

    # 3. Reopen old Chat #1 by sending its message history + a new turn (even without user field)
    # -> must automatically recognize sess1_id, set new_chat=False, pass sess1's thread_url,
    #    and send only the new turn ("Chat 1 turn 2") as delta_prompt!
    r1_cont = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={
            "model": "gemini-4-argon",
            "messages": [
                {"role": "user", "content": "Chat 1 turn 1"},
                {"role": "assistant", "content": reply1},
                {"role": "user", "content": "Chat 1 turn 2"},
            ],
        },
    ).json()
    assert r1_cont["session_id"] == sess1_id
    assert captured[-1].new_chat is False
    assert captured[-1].session_id == sess1_id
    assert captured[-1].thread_url == f"https://aistudio.google.com/prompts/{sess1_id}"
    assert captured[-1].delta_prompt == "Chat 1 turn 2"

    # 4. Verify /admin/sessions lists both sessions and returns full history for sess1_id
    sessions_list = client.get("/admin/sessions", headers=admin).json()["data"]
    assert {s["id"] for s in sessions_list} >= {sess1_id, sess2_id}

    sess1_detail = client.get(f"/admin/sessions/{sess1_id}", headers=admin).json()["session"]
    assert len(sess1_detail["messages"]) == 4
    assert sess1_detail["messages"][0]["content"] == "Chat 1 turn 1"
    assert sess1_detail["messages"][2]["content"] == "Chat 1 turn 2"

    assert client.delete(f"/admin/sessions/{sess2_id}", headers=admin).status_code == 200


