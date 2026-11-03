"""DOM selectors and in-page scripts for Google AI Studio (aistudio.google.com).

All knowledge about the page structure lives here. When Google AI Studio ships a UI
change, this is normally the only file that needs updating. Scripts return
JSON-serialisable values and never throw (errors are reported in the payload).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# Google AI Studio (Angular Material ms-* components) + fallback selectors
INPUT = (
    'ms-autosize-textarea textarea, textarea[aria-label*="Type something" i], '
    'textarea[aria-label*="prompt" i], footer textarea, textarea'
)
AGENT_BUBBLE = (
    'ms-chat-turn .model-prompt-container, ms-chat-turn[data-turn-role="Model"], '
    'div[class*="model-prompt-container"], div[class*="hatch-agent-bubble-bg"]'
)
THOUGHT_CHUNK = (
    'ms-thought-chunk, mat-expansion-panel.thought-panel, [data-test-id*="thought"]'
)
ATTACHMENT = (
    'ms-chat-turn img, ms-chat-turn video, [data-testid^="hatch-chat-attachment-presentation-"]'
)
STOP_BUTTON = (
    'button.run-button.stoppable, ms-run-button button.stoppable, '
    'button[aria-label*="Stop" i]'
)
RUN_BUTTON = (
    'ms-run-button button:not(.stoppable), button.run-button:not(.stoppable), '
    'button[aria-label="Run" i], button[aria-label^="Run " i]:not([aria-label*="setting" i]), '
    'button[aria-label*="Send" i]'
)
APPROVAL_CARD = '[data-testid="hatch-inline-approval-card"], ms-safety-intervention-card'
FILE_INPUT = 'input[type="file"]'

LOGIN_HINTS = (
    "sign in",
    "choose an account",
    "to continue to google ai studio",
    "accounts.google.com",
    "log in",
    "create an account",
    "use another account",
)
QUOTA_HINTS = (
    "resource has been exhausted",
    "quota exceeded",
    "rate limit",
    "out of credits",
    "usage limit",
    "limit reached",
    "token limit",
)
STALL_HINTS = ("Still sending", "Connecting...", "Loading model...")


def q(value: str) -> str:
    """JSON-quote a Python string for safe interpolation into JS."""
    return json.dumps(value)


PAGE_STATE = f"""(() => {{
  const body = document.body ? document.body.innerText : '';
  const hydration = document.querySelector('[data-hatch-shell-hydration-state]');
  const onLogin = location.hostname.includes('accounts.google.com');
  return {{
    ready: document.readyState === 'complete' && !onLogin && !!document.querySelector({q(INPUT)})
           && (!hydration || hydration.getAttribute('data-hatch-shell-hydration-state') === 'hydrated')
           && !body.includes('Connecting...'),
    hasInput: !!document.querySelector({q(INPUT)}),
    path: location.pathname,
    head: body.slice(0, 800),
  }};
}})()"""

# Open a blank chat through Google AI Studio's SPA router (or Next.js router fallback)
SOFT_NEW_THREAD = """(() => {
  if (location.hostname.endsWith('aistudio.google.com')) {
    const link = document.querySelector('a[href*="/prompts/new_chat"]');
    if (link) {
      link.click();
      return true;
    }
  }
  const router = window.next && window.next.router;
  if (router && typeof router.push === 'function') {
    router.push('/thread/new');
    return true;
  }
  return false;
})()"""

FRESH_THREAD = f"""(() => {{
  const page = {PAGE_STATE};
  const isNewPath = page.path.includes('/prompts/new_chat') || page.path === '/thread/new';
  return page.ready && isNewPath
         && document.querySelectorAll({q(AGENT_BUBBLE)}).length === 0;
}})()"""

# Snapshot of the conversation used to detect both thinking deltas and answer deltas
CHAT_STATE = f"""(() => {{
  const scrollers = [...document.querySelectorAll('*')].filter(e => {{
    const s = getComputedStyle(e);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.scrollHeight > e.clientHeight + 50;
  }});
  scrollers.forEach(e => {{ e.scrollTop = e.scrollHeight; }});
  const bubbles = [...document.querySelectorAll({q(AGENT_BUBBLE)})];
  let lastText = '';
  let lastThought = '';
  if (bubbles.length) {{
    const lastBubble = bubbles[bubbles.length - 1];
    const thoughts = [...lastBubble.querySelectorAll({q(THOUGHT_CHUNK)})];
    lastThought = thoughts.map(t => (t.innerText || '').trim()).filter(Boolean).join('\\n\\n');
    if (thoughts.length) {{
      const clone = lastBubble.cloneNode(true);
      clone.querySelectorAll({q(THOUGHT_CHUNK)}).forEach(n => n.remove());
      lastText = (clone.innerText || '').trim();
    }} else {{
      lastText = (lastBubble.innerText || '').trim();
    }}
  }}
  const atts = [...document.querySelectorAll({q(ATTACHMENT)})].map(a => {{
    const v = a.tagName === 'VIDEO' ? a : a.querySelector('video');
    const i = a.tagName === 'IMG' ? a : a.querySelector('img');
    const m = v || i;
    return {{
      tid: a.getAttribute('data-testid') || '',
      kind: v ? 'video' : (i ? 'image' : 'unknown'),
      src: m ? (m.currentSrc || m.src || '') : '',
      w: m ? (m.videoWidth || m.naturalWidth || 0) : 0,
      h: m ? (m.videoHeight || m.naturalHeight || 0) : 0,
    }};
  }});
  const tail = document.body ? document.body.innerText.slice(-600) : '';
  return {{
    agentCount: bubbles.length,
    lastText: lastText,
    lastThought: lastThought,
    generating: !!document.querySelector({q(STOP_BUTTON)}),
    approval: !!document.querySelector({q(APPROVAL_CARD)}),
    attachments: atts,
    tail,
  }};
}})()"""


def configure_run_settings(model: str, thinking_level: str | None) -> str:
    """Configure model selection and thinking level (High / Medium / Low / None) in AI Studio."""
    return f"""(() => {{
  try {{
    const targetModel = {q(model or "gemini-4-argon")};
    const level = {q((thinking_level or "high").lower())};
    const result = {{ modelSet: false, thinkingSet: false, level }};

    // 1. Toggle Thinking mode switch if present in AI Studio right panel
    const toggles = [...document.querySelectorAll('mat-slide-toggle, [role="switch"]')];
    const thinkToggle = toggles.find(t => /thinking/i.test(t.innerText || t.getAttribute('aria-label') || ''));
    if (thinkToggle) {{
      const isChecked = thinkToggle.classList.contains('mat-mdc-slide-toggle-checked')
        || thinkToggle.getAttribute('aria-checked') === 'true'
        || !!(thinkToggle.querySelector('input[type="checkbox"]') || {{}}).checked;
      const wantChecked = level !== 'none';
      if (isChecked !== wantChecked) {{
        const btn = thinkToggle.querySelector('button, input') || thinkToggle;
        btn.click();
      }}
      result.thinkingSet = true;
    }}

    // 2. Select Thinking level / budget chip or option (High / Medium / Low)
    if (level !== 'none') {{
      const candidates = [...document.querySelectorAll(
        'mat-chip-option, mat-button-toggle, button, [role="option"], [role="radio"]'
      )];
      const match = candidates.find(el => {{
        const txt = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
        return txt === level || txt === `thinking: ${{level}}` || txt === `${{level}} thinking`;
      }});
      if (match) {{
        match.click();
        result.thinkingSet = true;
      }}
    }}
    return result;
  }} catch (e) {{
    return {{ ok: false, err: String(e) }};
  }}
}})()"""


DENY_APPROVALS = f"""(() => {{
  const denied = [];
  for (const card of document.querySelectorAll({q(APPROVAL_CARD)})) {{
    const btn = [...card.querySelectorAll('button,[role=button]')]
      .find(b => /^(deny|decline|reject|don.t allow|dismiss)$/i.test((b.innerText || '').trim()));
    if (btn && !btn.disabled) {{
      btn.click();
      denied.push(card.getAttribute('aria-label') || 'approval request');
    }}
  }}
  return denied;
}})()"""

LAST_BUBBLE_MEDIA = f"""(() => {{
  const bubbles = [...document.querySelectorAll({q(AGENT_BUBBLE)})];
  const b = bubbles.length ? bubbles[bubbles.length - 1] : null;
  if (!b) return {{ links: [], html: '' }};
  const urls = [];
  const push = (u) => {{ if (u && !urls.includes(u)) urls.push(u); }};
  b.querySelectorAll('video, source').forEach(m => push(m.currentSrc || m.src || ''));
  b.querySelectorAll('a[href]').forEach(a => push(a.href || ''));
  b.querySelectorAll('img').forEach(m => push(m.currentSrc || m.src || ''));
  const text = b.innerText || '';
  const re = /https?:\\/\\/[^\\s)<>"']+/g;
  let mm; while ((mm = re.exec(text))) push(mm[0]);
  return {{ links: urls, html: (b.outerHTML || '').slice(0, 2000) }};
}})()"""


def fill_input(text: str) -> str:
    """Set the textarea value through the native setter so Angular / React picks it up."""
    return f"""(() => {{
  const ta = document.querySelector({q(INPUT)});
  if (!ta) return {{ ok: false, err: 'no-input' }};
  ta.focus();
  const proto = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  if (proto && proto.set) {{
    proto.set.call(ta, {q(text)});
  }} else {{
    ta.value = {q(text)};
  }}
  ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
  ta.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return {{ ok: true, len: ta.value.length }};
}})()"""


CLICK_SEND = f"""(() => {{
  const runBtn = document.querySelector({q(RUN_BUTTON)});
  if (runBtn && runBtn.offsetParent !== null) {{
    if (runBtn.disabled) return 'disabled';
    runBtn.click();
    return 'clicked';
  }}
  const btn = [...document.querySelectorAll('button,[role=button]')]
    .filter(b => b.offsetParent !== null)
    .find(b => {{
      const label = (
        (b.getAttribute('aria-label') || '') + ' ' +
        (b.getAttribute('data-testid') || '') + ' ' +
        (b.innerText || '')
      ).toLowerCase();
      if (label.includes('setting') || label.includes('stop') || label.includes('close')) return false;
      return /\\brun\\b|send|submit|发送|gửi/i.test(label);
    }});
  if (!btn) return 'missing';
  if (btn.disabled) return 'disabled';
  btn.click();
  return 'clicked';
}})()"""

INPUT_EMPTY = f"!(document.querySelector({q(INPUT)}) || {{value: ''}}).value"


def attach_file(b64: str, mime: str, filename: str) -> str:
    """Attach a file via input[type=file] or synthetic ClipboardEvent paste on the prompt box."""
    return f"""(() => {{
  try {{
    const bin = atob({q(b64)});
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const file = new File([bytes], {q(filename)}, {{ type: {q(mime)} }});
    const dt = new DataTransfer();
    dt.items.add(file);

    const input = document.querySelector({q(FILE_INPUT)});
    if (input) {{
      input.files = dt.files;
      input.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return {{ ok: true, via: 'file-input' }};
    }}

    const ta = document.querySelector({q(INPUT)});
    if (ta) {{
      ta.focus();
      const evt = new ClipboardEvent('paste', {{
        bubbles: true,
        cancelable: true,
        clipboardData: dt,
      }});
      ta.dispatchEvent(evt);
      return {{ ok: true, via: 'paste' }};
    }}
    return {{ ok: false, err: 'no-file-input' }};
  }} catch (e) {{ return {{ ok: false, err: String(e) }}; }}
}})()"""


def fetch_as_base64(url: str) -> str:
    """Fetch a (possibly blob:) URL inside the page context and return base64 bytes."""
    return f"""(async () => {{
  try {{
    const r = await fetch({q(url)});
    const b = await r.blob();
    const buf = new Uint8Array(await b.arrayBuffer());
    let s = '';
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return {{ ok: true, mime: b.type || '', b64: btoa(s) }};
  }} catch (e) {{ return {{ ok: false, err: String(e) }}; }}
}})()"""


# ---------------------------------------------------------------- quota
SETTINGS_LABELS = ("Settings", "設定", "设置")
SETTINGS_DOCK = '[data-testid="hatch-dock-more"], button[aria-label*="Settings" i]'

_VISIBLE = (
    "const visible = e => e.getClientRects().length > 0"
    " && getComputedStyle(e).visibility !== 'hidden';"
)
_CENTER = (
    "if (!b) return null; const r = b.getBoundingClientRect();"
    " return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};"
)

QUOTA_SETTINGS_ENTRY = f"""(() => {{
  {_VISIBLE}
  const labels = {json.dumps(list(SETTINGS_LABELS), ensure_ascii=False)};
  const b = [...document.querySelectorAll({q(SETTINGS_DOCK)})].find(visible)
    || [...document.querySelectorAll('button,[role=button]')].find(e =>
         visible(e) && labels.includes((e.getAttribute('aria-label') || '').trim()));
  {_CENTER}
}})()"""

QUOTA_MENU_ITEM = f"""(() => {{
  {_VISIBLE}
  const labels = {json.dumps(list(SETTINGS_LABELS), ensure_ascii=False)};
  const exact = e => labels.includes((e.textContent || '').trim());
  const b = [...document.querySelectorAll('[role=menuitem]')].find(e =>
      visible(e) && (exact(e) || [...e.querySelectorAll('span,div')].some(exact)))
    || [...document.querySelectorAll('[role=menu] button,[role=menu] span,[role=menu] div')]
         .find(e => visible(e) && exact(e));
  {_CENTER}
}})()"""

QUOTA_DIALOG_TEXT = """(() => {
  const d = [...document.querySelectorAll('[role=dialog],[aria-modal=true]')]
    .find(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden');
  return d ? (d.innerText || '') : '';
})()"""

_USAGE_HEADINGS = ("usage", "用量", "使用量")
_LANGUAGE_HEADINGS = ("language", "语言", "語言")
_PLAN = re.compile(r".{1,60}?(?:\s+plan|計劃|计划|方案|tier\s*\d+)", re.IGNORECASE)
_RESET_EN = re.compile(r"Weekly limit resets? on ([^\n]+)", re.IGNORECASE)
_RESET_ZH = re.compile(
    r"(?:每(?:週|周|星期)|週|周)(?:上限|限額|限额|額度|额度)"
    r"(?:會|会|將|将)?(?:於|于|在)\s*([^\n]+?)\s*(?:重設|重置)"
)
_EXTRA = re.compile(
    r"^(?:(?:Additional|Extra)\s+tokens|(?:額外|额外)(?:的)?"
    r"(?:符元|符記|代幣|代币|令牌|憑證|凭证|tokens))",
    re.IGNORECASE,
)
_PCT_USED = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:used\b|已(?:使)?用)", re.IGNORECASE)
_USED_PCT = re.compile(r"已(?:使)?用\s*(\d+(?:\.\d+)?)\s*%")


def _used_pct(line: str) -> float | None:
    m = _PCT_USED.search(line) or _USED_PCT.search(line)
    if not m:
        return None
    value = float(m.group(1))
    return value if 0 <= value <= 100 else None


def parse_usage_text(text: str | None) -> dict[str, Any]:
    """Parse the settings dialog's Usage section (English / zh-CN / zh-TW)."""
    normalized = unicodedata.normalize("NFKC", text or "")
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    start = next((i + 1 for i, ln in enumerate(lines) if ln.lower() in _USAGE_HEADINGS), 0)
    lines = lines[start:]
    end = next((i for i, ln in enumerate(lines) if ln.lower() in _LANGUAGE_HEADINGS), len(lines))
    lines = lines[:end]
    joined = "\n".join(lines)

    out: dict[str, Any] = {
        "plan": None,
        "weekly_used_pct": None,
        "resets_at": None,
        "extra_left": None,
    }
    out["plan"] = next((ln for ln in lines if _PLAN.fullmatch(ln)), None)
    if reset := (_RESET_EN.search(joined) or _RESET_ZH.search(joined)):
        out["resets_at"] = reset.group(1).strip()
    extra_at = next((i for i, ln in enumerate(lines) if _EXTRA.match(ln)), len(lines))
    out["weekly_used_pct"] = next(
        (v for ln in lines[:extra_at] if (v := _used_pct(ln)) is not None), None
    )
    for ln in lines[extra_at:]:
        if _used_pct(ln) is not None:
            if left := re.search(r"\(([^)]+)\)", ln):
                out["extra_left"] = left.group(1).strip()
            break
    out["found"] = out["weekly_used_pct"] is not None
    return out
