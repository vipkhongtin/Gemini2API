import pytest

from gemini2api.core.models import resolve_model, resolve_thinking_level
from gemini2api.core.prompt import flatten_messages, followup_text, message_turns
from gemini2api.errors import InvalidRequest


def test_single_user_message_verbatim():
    assert flatten_messages([{"role": "user", "content": "hi"}]).text == "hi"


def test_multi_turn_has_roles_and_images():
    flat = flatten_messages([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]},
    ])
    assert "[System]\nbe brief" in flat.text
    assert "[User]\nwhat is this" in flat.text
    assert flat.images == ["data:image/png;base64,AAAA"]


def test_followup_sends_only_new_user_text():
    first = message_turns([{"role": "user", "content": "hi"}])
    second = message_turns([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ])
    assert followup_text(first, second) == "again"


def test_followup_rejects_a_different_history():
    first = message_turns([{"role": "user", "content": "hi"}])
    other = message_turns([{"role": "user", "content": "a different chat"}])
    edited = message_turns([
        {"role": "user", "content": "hi, edited"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ])
    assert followup_text(first, other) is None
    assert followup_text(first, edited) is None
    assert followup_text(first, first) is None


def test_thread_url_ignores_the_new_thread_page():
    from gemini2api.drivers.browser.driver import is_thread_url

    assert is_thread_url("https://aistudio.google.com/prompts/123abc")
    assert not is_thread_url("https://aistudio.google.com/prompts/new_chat")
    assert not is_thread_url("https://aistudio.google.com/prompts/new_chat/")
    assert not is_thread_url("https://aistudio.google.com/")


def test_resolve_model():
    assert resolve_model("gemini-4-argon", "chat").id == "gemini-4-argon"
    assert resolve_model("gemini-4-argon-high", "chat").id == "gemini-4-argon-high"
    assert resolve_model("gemini-4-argon-low", "chat").id == "gemini-4-argon-low"
    assert resolve_model("gemini-4-argon-nothink", "chat").id == "gemini-4-argon-nothink"
    assert resolve_model("gpt-4o", "chat").id == "gemini-4-argon"
    assert resolve_model("unknown", "image").id == "gemini-image"
    assert resolve_model(None, "video").id == "gemini-video"
    with pytest.raises(InvalidRequest):
        resolve_model("gemini-image", "chat")


def test_resolve_thinking_level():
    spec = resolve_model("gemini-4-argon", "chat")
    assert resolve_thinking_level(spec) == "high"
    assert resolve_thinking_level(spec, reasoning_effort="low") == "low"
    assert resolve_thinking_level(spec, thinking={"type": "disabled"}) == "none"
    assert resolve_thinking_level(spec, thinking={"budget_tokens": 1024}) == "low"
    assert resolve_thinking_level(spec, thinking={"budget_tokens": 4096}) == "medium"
    assert resolve_thinking_level(spec, thinking={"budget_tokens": 16384}) == "high"

    low_spec = resolve_model("gemini-4-argon-low", "chat")
    assert resolve_thinking_level(low_spec) == "low"

    nothink_spec = resolve_model("gemini-4-argon-nothink", "chat")
    assert resolve_thinking_level(nothink_spec) == "none"


def test_media_prune_removes_only_expired_files(tmp_path):
    import os
    import time

    from gemini2api.core.media import MediaStore

    store = MediaStore(tmp_path / "media")
    old_name = store.save(b"old", "image/png")
    new_name = store.save(b"new", "image/png")
    old_time = time.time() - 30 * 86400  # 30 days ago
    os.utime(store.path_of(old_name), (old_time, old_time))

    removed = store.prune(14 * 86400)
    assert removed == 1
    assert not (tmp_path / "media" / old_name).exists()
    assert (tmp_path / "media" / new_name).exists()


def test_media_prune_disabled_and_foreign_files(tmp_path):
    from gemini2api.core.media import MediaStore

    store = MediaStore(tmp_path / "media")
    name = store.save(b"data", "image/png")
    (tmp_path / "media" / "foreign.txt").write_text("do not touch")

    assert store.prune(0) == 0
    assert (tmp_path / "media" / name).exists()
    # prune with negative threshold never deletes
    assert store.prune(-1) == 0
    assert (tmp_path / "media" / "foreign.txt").exists()


def test_blank_browser_profile_dir_falls_back_to_data_dir(tmp_path, monkeypatch):
    from gemini2api.config import Settings

    monkeypatch.setenv("GEMINI2API_BROWSER_PROFILE_DIR", "")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.profile_dir == tmp_path / "browser-profile"
