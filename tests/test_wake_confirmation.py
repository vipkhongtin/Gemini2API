"""VM / session wake confirmation and per-kind video routing."""

from __future__ import annotations

import time

import httpx
import pytest

from gemini2api.accounts import Account, AccountPool, JsonAccountStore
from gemini2api.accounts.pool import WAKE_BLOCK_SECONDS, note_wake
from gemini2api.drivers.base import SessionInfo
from gemini2api.drivers.mock import MockDriver
from gemini2api.services.gateway import Gateway
from gemini2api.upstream import aistudio

COOKIES = {"SID": "s1", "__Secure-1PSID": "p", "SAPISID": "sap", "HSID": "h"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def upstream(monkeypatch):
    """Route aistudio.renew_session's HTTP calls to a scripted fake AI Studio."""
    real = httpx.AsyncClient
    state = {"sessions": [], "wake": httpx.Response(200, json={"accepted": True}),
             "gets": 0, "posts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == aistudio.VM_WAKE_API:
            state["posts"] += 1
            wake = state["wake"]
            if isinstance(wake, Exception):
                raise wake
            return wake
        assert str(request.url) == aistudio.SESSION_API
        idx = min(state["gets"], len(state["sessions"]) - 1)
        state["gets"] += 1
        resp = state["sessions"][idx]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(aistudio.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def session(vm_state: str | None, status: str = "assigned", **headers) -> httpx.Response:
    body = {"status": status, "vm_id": "vm-1"}
    if vm_state is not None:
        body["vm_state"] = vm_state
    return httpx.Response(200, json=body, headers=headers)


async def _renew(clock: FakeClock, **kw) -> SessionInfo:
    return await aistudio.renew_session(dict(COOKIES), sleep=clock.sleep, clock=clock, **kw)


async def test_wake_confirmed_once_running(upstream):
    upstream["sessions"] = [session("SUSPENDED"), session("STARTING"), session("RUNNING")]
    clock = FakeClock()
    info = await _renew(clock, confirm_s=20, poll_s=2)
    assert info.ok
    assert info.meta["vm_state"] == "SUSPENDED"
    assert info.meta["vm_state_after"] == "RUNNING"
    assert info.meta["vm_wake_ok"] is True
    assert upstream["posts"] == 1 and upstream["gets"] == 3
    assert clock.sleeps == [2]


async def test_already_running_needs_no_sleep(upstream):
    upstream["sessions"] = [session("RUNNING")]
    clock = FakeClock()
    info = await _renew(clock, confirm_s=20)
    assert info.meta["vm_wake_ok"] is True and clock.sleeps == []


async def test_never_running_is_not_ok_and_stops_at_the_deadline(upstream):
    upstream["sessions"] = [session("SUSPENDED")]
    clock = FakeClock()
    info = await _renew(clock, confirm_s=6, poll_s=2)
    assert info.meta["vm_wake_ok"] is False
    assert info.meta["vm_state_after"] == "SUSPENDED"
    assert clock.now <= 6
    assert upstream["gets"] == 1 + 4


@pytest.mark.parametrize("failure", [httpx.Response(503, text="busy"),
                                     httpx.ConnectError("connection reset")])
async def test_session_errors_while_confirming(upstream, failure):
    upstream["sessions"] = [session("SUSPENDED"), failure]
    clock = FakeClock()
    info = await _renew(clock, confirm_s=4, poll_s=2)
    assert info.ok
    assert info.meta["vm_wake_ok"] is None
    assert info.meta["vm_state_after"] is None


async def test_error_then_running_still_confirms(upstream):
    upstream["sessions"] = [session("SUSPENDED"), httpx.Response(502), session("RUNNING")]
    info = await _renew(FakeClock(), confirm_s=10, poll_s=2)
    assert info.meta["vm_wake_ok"] is True


@pytest.mark.parametrize("wake,code", [
    (httpx.Response(403, json={"error": "Forbidden"}), "403"),
    (httpx.Response(500), "500"),
    (httpx.ConnectTimeout("slow"), "ConnectTimeout"),
])
async def test_rejected_wake_on_a_running_vm_is_still_ok(upstream, wake, code):
    upstream["sessions"] = [session("RUNNING")]
    upstream["wake"] = wake
    clock = FakeClock()
    info = await _renew(clock, confirm_s=20)
    assert info.meta["vm_wake_ok"] is True and info.meta["vm_state_after"] == "RUNNING"
    assert info.meta["vm_wake_http"] == code
    assert clock.sleeps == []


async def test_rejected_wake_on_a_stopped_vm_is_not_ok(upstream):
    upstream["sessions"] = [session("SUSPENDED")]
    upstream["wake"] = httpx.Response(403, json={"error": "Forbidden"})
    info = await _renew(FakeClock(), confirm_s=4, poll_s=2)
    assert info.meta["vm_wake_ok"] is False and info.meta["vm_state_after"] == "SUSPENDED"


def test_unknown_wake_outcome_does_not_block_video():
    acc = Account(id="a1", cookies=dict(COOKIES))
    note_wake(acc, {"vm_wake_ok": None, "vm_state_after": None})
    assert not (acc.meta.get("kind_blocked_until") or {}).get("video")


async def test_no_wake_without_vm(upstream):
    upstream["sessions"] = [httpx.Response(200, json={"status": "assigned"})]
    info = await _renew(FakeClock())
    assert "vm_wake_ok" not in info.meta and "vm_state_after" not in info.meta
    assert upstream["posts"] == 0


async def test_cookies_rotated_while_confirming_are_kept(upstream):
    upstream["sessions"] = [
        session("SUSPENDED"),
        session("RUNNING", **{"set-cookie": "__Secure-1PSIDTS=v2; Domain=.google.com; Path=/"}),
    ]
    info = await _renew(FakeClock(), confirm_s=10)
    assert info.cookies["__Secure-1PSIDTS"] == "v2"


async def test_confirm_window_from_env(upstream, monkeypatch):
    monkeypatch.setenv("GEMINI2API_WAKE_CONFIRM_S", "0")
    upstream["sessions"] = [session("SUSPENDED")]
    clock = FakeClock()
    info = await _renew(clock)
    assert info.meta["vm_wake_ok"] is False
    assert upstream["gets"] == 2 and clock.sleeps == []


async def _pool(tmp_path, n=2) -> AccountPool:
    pool = AccountPool(JsonAccountStore(tmp_path / "acc.json"), acquire_timeout=0.2)
    for i in range(n):
        await pool.upsert(Account(id=f"a{i}", cookies={"SID": "x"}))
    return pool


async def test_video_not_routed_to_unconfirmed_account(tmp_path):
    pool = await _pool(tmp_path)
    note_wake(pool.get("a0"), {"vm_wake_ok": False, "vm_state_after": "SUSPENDED"})
    blocked = pool.get("a0").meta["kind_blocked_until"]["video"]
    assert time.time() + WAKE_BLOCK_SECONDS - 5 < blocked <= time.time() + WAKE_BLOCK_SECONDS
    for _ in range(3):
        acc = await pool.acquire(kind="video")
        assert acc.id == "a1"
        await pool.release(acc)
    assert (await pool.acquire(exclude={"a1"}, kind="image")).id == "a0"


async def test_confirmed_wake_lifts_its_block(tmp_path):
    pool = await _pool(tmp_path)
    acc = pool.get("a0")
    note_wake(acc, {"vm_wake_ok": False})
    note_wake(acc, {"vm_wake_ok": True, "vm_state_after": "RUNNING"})
    assert "video" not in acc.meta["kind_blocked_until"]
    assert (await pool.acquire(exclude={"a1"}, kind="video")).id == "a0"


async def test_confirmed_wake_keeps_a_longer_block(tmp_path):
    pool = await _pool(tmp_path)
    acc = pool.get("a0")
    longer = time.time() + 12 * 3600
    acc.meta["kind_blocked_until"] = {"video": longer}
    note_wake(acc, {"vm_wake_ok": False})
    note_wake(acc, {"vm_wake_ok": True})
    assert acc.meta["kind_blocked_until"]["video"] == longer


async def test_renewal_without_wake_leaves_blocks_alone(tmp_path):
    pool = await _pool(tmp_path)
    acc = pool.get("a0")
    note_wake(acc, {"vm_wake_ok": False})
    before = dict(acc.meta["kind_blocked_until"])
    note_wake(acc, {"session_status": "assigned"})
    assert acc.meta["kind_blocked_until"] == before


async def test_every_account_unconfirmed_still_serves_video(tmp_path):
    pool = await _pool(tmp_path)
    for acc in pool.all():
        note_wake(acc, {"vm_wake_ok": False})
    assert (await pool.acquire(kind="video")).id in {"a0", "a1"}


async def test_gateway_routes_video_around_an_unconfirmed_account(tmp_path):
    pool = await _pool(tmp_path)
    note_wake(pool.get("a0"), {"vm_wake_ok": False})
    gateway = Gateway(pool, MockDriver())

    async def which(acc: Account) -> str:
        return acc.id

    for _ in range(3):
        assert await gateway._run(which, kind="video") == "a1"
    assert {await gateway._run(which) for _ in range(4)} == {"a0", "a1"}


async def test_admin_lists_wake_fields_and_reset_clears_the_block(client, admin):
    acc_id = client.post("/admin/accounts", headers=admin,
                         json={"cookies": {"SID": "secretvalue123"}}).json()["account"]["id"]
    pool = client.app.state.services.pool
    acc = pool.get(acc_id)
    acc.meta.update({"vm_wake_ok": False, "vm_state_after": "SUSPENDED"})
    note_wake(acc, acc.meta)
    listed = client.get("/admin/accounts", headers=admin).json()["data"][0]["meta"]
    assert listed["vm_wake_ok"] is False and listed["vm_state_after"] == "SUSPENDED"
    assert listed["kind_blocked_until"]["video"] > time.time()
    client.post(f"/admin/accounts/{acc_id}/reset", headers=admin)
    meta = pool.get(acc_id).meta
    assert "kind_blocked_until" not in meta and "wake_blocked_until" not in meta
