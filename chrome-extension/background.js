"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:18610";
const AI_STUDIO_NEW_CHAT = "https://aistudio.google.com/prompts/new_chat";

let ws = null;
let wsConnected = false;
let activeRequests = new Map(); // request_id -> { cancelled: boolean }
let tabSessionMap = new Map();  // tabId -> session_id

async function getServerUrl() {
  const stored = await chrome.storage.local.get({ serverUrl: DEFAULT_SERVER });
  return (stored.serverUrl || DEFAULT_SERVER).trim().replace(/\/+$/, "");
}

function toWsUrl(httpUrl) {
  return httpUrl.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:") + "/ws/extension";
}

async function readAiStudioCookies() {
  const [aiCookies, googleCookies] = await Promise.all([
    chrome.cookies.getAll({ url: "https://aistudio.google.com/" }),
    chrome.cookies.getAll({ url: "https://accounts.google.com/" }),
  ]);
  const byName = new Map();
  for (const c of [...googleCookies, ...aiCookies]) {
    if (c.value) byName.set(c.name, c);
  }
  return Array.from(byName.values()).map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path,
    secure: c.secure,
    httpOnly: c.httpOnly,
    expirationDate: c.expirationDate ?? null,
  }));
}

async function findAiStudioTabs() {
  return await chrome.tabs.query({ url: "https://aistudio.google.com/*" });
}

async function sendStatusHello() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    const cookies = await readAiStudioCookies();
    const tabs = await findAiStudioTabs();
    const hasSid = cookies.some((c) => c.name === "SID" || c.name === "__Secure-1PSID");
    ws.send(
      JSON.stringify({
        type: "hello",
        label: "Chrome Extension (AI Studio)",
        cookies: hasSid ? cookies : [],
        aistudio_tabs: tabs.length,
        user_agent: navigator.userAgent,
      })
    );
  } catch (err) {
    console.warn("[gemini2api] sendStatusHello error:", err);
  }
}

async function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const serverUrl = await getServerUrl();
  const wsUrl = toWsUrl(serverUrl);
  try {
    ws = new WebSocket(wsUrl);
  } catch {
    wsConnected = false;
    return;
  }

  ws.onopen = async () => {
    wsConnected = true;
    await chrome.action.setBadgeText({ text: "ON" });
    await chrome.action.setBadgeBackgroundColor({ color: "#10b981" });
    await sendStatusHello();
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "chat_request") {
      handleChatRequest(msg);
    } else if (msg.type === "cancel_request" && msg.request_id) {
      const state = activeRequests.get(msg.request_id);
      if (state) state.cancelled = true;
    } else if (msg.type === "reload_extension") {
      chrome.runtime.reload();
    }
  };

  ws.onclose = async () => {
    wsConnected = false;
    ws = null;
    await chrome.action.setBadgeText({ text: "" });
  };

  ws.onerror = () => {
    wsConnected = false;
  };
}

function sendWs(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function execScriptWithTimeout(options, timeoutMs = 4500) {
  let timer = null;
  try {
    return await Promise.race([
      chrome.scripting.executeScript(options),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("executeScript timeout")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function disableBeforeUnload(tabId) {
  await execScriptWithTimeout(
    {
      target: { tabId },
      world: "MAIN",
      func: () => {
        try {
          window.onbeforeunload = null;
          window.addEventListener(
            "beforeunload",
            (e) => {
              e.stopImmediatePropagation();
            },
            true
          );
        } catch {}
      },
    },
    1500
  ).catch(() => null);
}

function isPromptPageUrl(url) {
  const u = String(url || "");
  return u.includes("aistudio.google.com") && u.includes("/prompts/");
}

function isSpecificSavedThreadUrl(url) {
  const u = String(url || "").split("?")[0].replace(/\/+$/, "");
  return u.includes("aistudio.google.com") && u.includes("/prompts/") && !u.endsWith("/new_chat");
}

async function navigateTabAndWait(tabId, targetUrl, makeActive = false) {
  await disableBeforeUnload(tabId);
  const updateProps = { url: targetUrl };
  if (makeActive) updateProps.active = true;
  await chrome.tabs.update(tabId, updateProps);
  for (let i = 0; i < 60; i++) {
    await sleep(400);
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (t && t.status === "complete") {
      await sleep(500);
      return t;
    }
  }
  return await chrome.tabs.get(tabId).catch(() => null);
}

// Executed inside the AI Studio tab to click "New Chat" via SPA router if possible
async function domResetToNewChat() {
  const queryAllDeep = (root, selector) => {
    const out = [];
    const visit = (node) => {
      if (!node) return;
      if (node.querySelectorAll) {
        try {
          out.push(...node.querySelectorAll(selector));
        } catch {}
      }
      const all = node.querySelectorAll ? node.querySelectorAll("*") : [];
      for (const el of all) {
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    visit(root);
    return out;
  };

  const turns = queryAllDeep(
    document,
    'ms-chat-turn .model-prompt-container, ms-chat-turn[data-turn-role="Model"], [data-turn-role="Model"], ms-chat-turn'
  );
  const onNew = location.pathname.endsWith("/new_chat") || location.pathname.endsWith("/new_chat/");
  if (onNew && turns.length === 0) {
    return { alreadyFresh: true };
  }

  // Try clicking the in-page "New chat" / "Clear chat" link/button for fast SPA reset
  const newLinks = queryAllDeep(
    document,
    'a[href*="/prompts/new_chat"], button[aria-label*="New chat" i], button[aria-label*="Clear chat" i], button[aria-label*="Reset" i], button[aria-label*="Create new" i], [data-test-id*="new-chat" i]'
  );
  for (const el of newLinks) {
    const s = window.getComputedStyle(el);
    if (s.display !== "none" && s.visibility !== "hidden") {
      el.click();
      await new Promise((r) => setTimeout(r, 350));
      // If a confirmation dialog ("Discard unsaved changes?") popped up, confirm it
      const dialogBtns = queryAllDeep(document, "mat-dialog-container button, [role='dialog'] button");
      for (const db of dialogBtns) {
        const dt = (db.innerText || db.getAttribute("aria-label") || "").trim().toLowerCase();
        if (
          dt.includes("discard") ||
          dt.includes("continue") ||
          dt.includes("leave") ||
          dt.includes("clear") ||
          dt.includes("reset") ||
          dt.includes("confirm") ||
          dt.includes("huỷ") ||
          dt.includes("rời") ||
          dt.includes("xoá") ||
          dt.includes("đồng ý")
        ) {
          db.click();
          await new Promise((r) => setTimeout(r, 300));
        }
      }
      const afterTurns = queryAllDeep(document, "ms-chat-turn");
      if (afterTurns.length === 0) {
        return { clickedSpaNew: true };
      }
    }
  }
  return { needHardNav: true };
}

/**
 * Resolves the right Chrome tab and prepares it for either:
 * 1) A brand-new chat (req.new_chat === true) -> opens/resets to /prompts/new_chat
 * 2) Continuing / reopening an existing chat (req.new_chat === false) -> reuses the tab
 *    holding that session or navigates back to req.thread_url.
 */
async function prepareTabForSession(req) {
  const isNewChat = Boolean(req.new_chat);
  const sessionId = req.session_id || "";
  const targetThreadUrl = req.thread_url || "";
  const hasSavedUrl = isSpecificSavedThreadUrl(targetThreadUrl);
  let promptToSend = req.prompt || "";

  let tabs = await findAiStudioTabs();

  // Case 1: Continuing or reopening an existing chat session
  if (!isNewChat && sessionId) {
    // 1a. Check if a tab is already mapped to this sessionId
    for (const t of tabs) {
      if (tabSessionMap.get(t.id) === sessionId && isPromptPageUrl(t.url)) {
        return { tab: t, promptToSend };
      }
    }
    // 1b. Check if a tab is already open at the saved thread_url
    if (hasSavedUrl) {
      const cleanTarget = targetThreadUrl.split("?")[0];
      const matchTab = tabs.find((t) => String(t.url || "").startsWith(cleanTarget));
      if (matchTab) {
        tabSessionMap.set(matchTab.id, sessionId);
        return { tab: matchTab, promptToSend };
      }
    }
    // 1c. Reopening an old chat not currently loaded in any tab:
    let tab = tabs.find((t) => isPromptPageUrl(t.url)) || tabs[0] || null;
    if (!tab) {
      const startUrl = hasSavedUrl ? targetThreadUrl : AI_STUDIO_NEW_CHAT;
      tab = await chrome.tabs.create({ url: startUrl, active: true });
      await sleep(1500);
    } else if (hasSavedUrl) {
      tab = await navigateTabAndWait(tab.id, targetThreadUrl, false);
    } else {
      tab = await navigateTabAndWait(tab.id, AI_STUDIO_NEW_CHAT, false);
      promptToSend = req.full_prompt || req.prompt || "";
    }
    if (tab && tab.id) tabSessionMap.set(tab.id, sessionId);
    return { tab, promptToSend };
  }

  // Case 2: Starting a brand-new chat (req.new_chat === true)
  let tab = tabs.find((t) => isPromptPageUrl(t.url)) || tabs[0] || null;
  if (!tab) {
    tab = await chrome.tabs.create({ url: AI_STUDIO_NEW_CHAT, active: true });
    for (let i = 0; i < 40; i++) {
      await sleep(400);
      const cur = await chrome.tabs.get(tab.id).catch(() => null);
      if (cur && cur.status === "complete") {
        tab = cur;
        break;
      }
    }
    await sleep(600);
  } else if (!isPromptPageUrl(tab.url)) {
    tab = await navigateTabAndWait(tab.id, AI_STUDIO_NEW_CHAT, false);
  } else {
    await disableBeforeUnload(tab.id);
    const resetRes = await execScriptWithTimeout(
      { target: { tabId: tab.id }, func: domResetToNewChat },
      3000
    ).catch(() => null);
    if (!resetRes) {
      // Tab is frozen by a modal dialog -> open a clean new tab
      tab = await chrome.tabs.create({ url: AI_STUDIO_NEW_CHAT, active: true });
      await sleep(1800);
    } else {
      const info = resetRes?.[0]?.result;
      if (!info || info.needHardNav) {
        tab = await navigateTabAndWait(tab.id, AI_STUDIO_NEW_CHAT, false);
      }
    }
  }

  if (tab && tab.id && sessionId) {
    tabSessionMap.set(tab.id, sessionId);
  }
  return { tab, promptToSend };
}

// Async function injected into the Google AI Studio tab to find the prompt box, fill it, and click Run
async function domPrepareAndSend(promptText, modelId, thinkingLevel) {
  const sleepInPage = (ms) => new Promise((r) => setTimeout(r, ms));

  const queryAllDeep = (root, selector) => {
    const out = [];
    const visit = (node) => {
      if (!node) return;
      if (node.querySelectorAll) {
        try {
          out.push(...node.querySelectorAll(selector));
        } catch {}
      }
      const all = node.querySelectorAll ? node.querySelectorAll("*") : [];
      for (const el of all) {
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    visit(root);
    return out;
  };

  const isUsable = (el) => {
    if (!el || el.disabled || el.readOnly) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    return true;
  };

  const isSystemInstructionInput = (el) => {
    const aria = (el.getAttribute("aria-label") || "").toLowerCase();
    const ph = (el.getAttribute("placeholder") || "").toLowerCase();
    if (aria.includes("system") || ph.includes("system instruction")) return true;
    if (el.closest && el.closest("ms-system-instructions, .system-instructions, .system-instructions-card")) {
      return true;
    }
    return false;
  };

  // Dismiss any welcome/consent/unsaved overlay button if present
  try {
    const dismissBtns = queryAllDeep(
      document,
      'button[aria-label="Close" i], button[aria-label="Dismiss" i], mat-dialog-container button'
    );
    for (const b of dismissBtns) {
      const txt = (b.innerText || "").trim().toLowerCase();
      if (
        txt === "got it" ||
        txt === "dismiss" ||
        txt === "continue" ||
        txt === "discard" ||
        txt === "đã hiểu"
      ) {
        b.click();
        await sleepInPage(150);
      }
    }
  } catch {}

  // 1. Configure Model & Thinking Level if present in Run Settings
  try {
    const wantModel = String(modelId || "").toLowerCase();
    if (wantModel) {
      const sel = queryAllDeep(
        document,
        'ms-model-selector, [data-test-id="model-selector"], button[aria-label*="Model" i], mat-select[aria-label*="Model" i]'
      )[0];
      if (sel) {
        const cur = (sel.innerText || "").toLowerCase();
        const key = wantModel.replace("models/", "").replace(/-/g, " ");
        if (!cur.includes(key) && !(wantModel.includes("argon") && cur.includes("argon"))) {
          const opts = queryAllDeep(document, 'mat-option, [role="option"]');
          const match = opts.find((o) => {
            const t = (o.innerText || "").toLowerCase();
            return t.includes(key) || (wantModel.includes("argon") && t.includes("argon"));
          });
          if (match) match.click();
        }
      }
    }

    const wantThink = String(thinkingLevel || "").toLowerCase();
    if (wantThink) {
      const toggles = queryAllDeep(
        document,
        'mat-slide-toggle, [role="switch"], ms-thinking-budget-setting'
      );
      const thinkToggle = toggles.find((el) =>
        /thinking/i.test(el.innerText || el.getAttribute("aria-label") || "")
      );
      if (thinkToggle) {
        const btn =
          thinkToggle.querySelector('button[role="switch"], input[type="checkbox"]') || thinkToggle;
        const checked = btn.getAttribute("aria-checked") === "true" || btn.checked === true;
        if (wantThink === "none" && checked) btn.click();
        else if (wantThink !== "none" && !checked) btn.click();
      }
    }
  } catch {}

  // 2. Find the chat prompt input element (supports both <textarea> and contenteditable across light & shadow DOM)
  const PRIORITY_SELECTORS = [
    "ms-prompt-input-wrapper textarea",
    "ms-autosize-textarea textarea",
    "ms-text-chunk textarea",
    "ms-chunk-editor textarea",
    ".prompt-input-wrapper textarea",
    ".text-input-field textarea",
    'textarea[aria-label*="Type" i]',
    'textarea[aria-label*="Ask" i]',
    'textarea[aria-label*="prompt" i]',
    'textarea[aria-label*="Enter" i]',
    'textarea[aria-label*="chat" i]',
    'textarea[placeholder*="Type" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="prompt" i]',
    'textarea[placeholder*="Enter" i]',
    "footer textarea",
    'ms-prompt-input-wrapper [contenteditable="true"]',
    'ms-chunk-editor [contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
  ];

  let inputEl = null;
  for (const sel of PRIORITY_SELECTORS) {
    const candidates = queryAllDeep(document, sel).filter(
      (el) => isUsable(el) && !isSystemInstructionInput(el)
    );
    if (candidates.length > 0) {
      inputEl = candidates[candidates.length - 1];
      break;
    }
  }

  // Fallback: any non-system textarea or contenteditable on the page
  if (!inputEl) {
    const allTextareas = queryAllDeep(document, "textarea").filter(
      (el) => isUsable(el) && !isSystemInstructionInput(el)
    );
    if (allTextareas.length > 0) {
      inputEl = allTextareas[allTextareas.length - 1];
    }
  }
  if (!inputEl) {
    const allEditables = queryAllDeep(
      document,
      '[contenteditable="true"], [role="textbox"]'
    ).filter((el) => isUsable(el) && !isSystemInstructionInput(el));
    if (allEditables.length > 0) {
      inputEl = allEditables[allEditables.length - 1];
    }
  }

  if (!inputEl) {
    return {
      ok: false,
      needNav: !location.pathname.includes("/prompts/"),
      currentUrl: location.href,
      pageTitle: document.title,
      error: `Không tìm thấy ô nhập prompt trên trang hiện tại (${location.href}).`,
    };
  }

  // Count existing model answer turns before sending (excluding thought-activity-host turns)
  const countModelTurns = () => {
    const allModel = queryAllDeep(document, "ms-chat-turn:not(.thought-activity-host)").filter(
      (t) =>
        t.querySelector('.model-prompt-container, [data-turn-role="Model"]') ||
        !t.querySelector('.user-prompt-container, [data-turn-role="User"]')
    );
    return allModel.length;
  };
  const turnsBefore = countModelTurns();

  // 3. Insert prompt text reliably into <textarea> or contenteditable
  inputEl.focus();
  if (inputEl.tagName === "TEXTAREA" || inputEl.tagName === "INPUT") {
    const proto =
      inputEl.tagName === "TEXTAREA"
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    const nativeSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (nativeSetter) {
      nativeSetter.call(inputEl, "");
    } else {
      inputEl.value = "";
    }
    try {
      inputEl.select();
      document.execCommand("insertText", false, promptText);
    } catch {}
    if ((inputEl.value || "").trim() !== promptText.trim()) {
      if (nativeSetter) {
        nativeSetter.call(inputEl, promptText);
      } else {
        inputEl.value = promptText;
      }
    }
    inputEl.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        composed: true,
        cancelable: true,
        inputType: "insertText",
        data: promptText,
      })
    );
    inputEl.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  } else {
    inputEl.focus();
    document.execCommand("selectAll", false, null);
    if (!document.execCommand("insertText", false, promptText)) {
      inputEl.textContent = promptText;
      inputEl.dispatchEvent(
        new InputEvent("input", { bubbles: true, composed: true, data: promptText })
      );
    }
  }

  // Wait for Angular to process input change and enable the Run button
  await sleepInPage(180);

  // 4. Click the Run / Send button (explicitly excluding "Run settings" / "Stop")
  const isRunPromptButton = (b) => {
    if (!b || !isUsable(b)) return false;
    if (b.disabled || b.getAttribute("aria-disabled") === "true") return false;
    if (b.classList && b.classList.contains("stoppable")) return false;
    const label = (
      (b.getAttribute("aria-label") || "") +
      " " +
      (b.getAttribute("mattooltip") || "") +
      " " +
      (b.innerText || "")
    ).toLowerCase();
    if (
      label.includes("setting") ||
      label.includes("stop") ||
      label.includes("close") ||
      label.includes("cài đặt") ||
      label.includes("dừng")
    ) {
      return false;
    }
    return true;
  };

  const SEND_SELECTORS = [
    "ms-run-button button:not(.stoppable)",
    "button.run-button:not(.stoppable)",
    "ms-prompt-input-wrapper button[type='submit']",
    'button[aria-label="Run" i]',
    'button[aria-label^="Run " i]',
    'button[aria-label*="Send" i]',
  ];

  let clicked = false;
  for (let waitAttempt = 0; waitAttempt < 20; waitAttempt++) {
    for (const s of SEND_SELECTORS) {
      const btns = queryAllDeep(document, s).filter(isRunPromptButton);
      if (btns.length > 0) {
        btns[btns.length - 1].click();
        clicked = true;
        break;
      }
    }
    if (clicked) break;
    await sleepInPage(120);
  }

  // Verify submission after 300ms; if input box still has text, dispatch Ctrl+Enter & force-click Run
  await sleepInPage(300);
  const currentVal = (inputEl.value !== undefined ? inputEl.value : inputEl.textContent || "").trim();
  if (!clicked || (currentVal.length > 0 && countModelTurns() <= turnsBefore)) {
    const rawRunBtn = queryAllDeep(
      document,
      "ms-run-button button:not(.stoppable), button.run-button:not(.stoppable)"
    )[0];
    if (rawRunBtn) {
      rawRunBtn.disabled = false;
      rawRunBtn.removeAttribute("disabled");
      rawRunBtn.click();
    }
    for (const ctrl of [true, false]) {
      for (const evType of ["keydown", "keyup"]) {
        inputEl.dispatchEvent(
          new KeyboardEvent(evType, {
            key: "Enter",
            code: "Enter",
            keyCode: 13,
            which: 13,
            ctrlKey: ctrl,
            metaKey: ctrl,
            bubbles: true,
            composed: true,
            cancelable: true,
          })
        );
      }
    }
  }

  return {
    ok: true,
    turnsBefore,
    currentUrl: location.href,
  };
}

// Executed inside the tab to poll streaming thoughts & answer text
function domReadChatState() {
  const queryAllDeep = (root, selector) => {
    const out = [];
    const visit = (node) => {
      if (!node) return;
      if (node.querySelectorAll) {
        try {
          out.push(...node.querySelectorAll(selector));
        } catch {}
      }
      const all = node.querySelectorAll ? node.querySelectorAll("*") : [];
      for (const el of all) {
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    visit(root);
    return out;
  };

  const STOP_SELECTORS = [
    "button.run-button.stoppable",
    "ms-run-button button.stoppable",
    'ms-run-button button[aria-label*="Stop" i]',
    'button[aria-label*="Stop generating" i]',
    'button[aria-label="Stop" i]',
    "ms-stop-button button",
  ];
  const isVisible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const allChatTurns = queryAllDeep(document, "ms-chat-turn");
  const lastOverallTurn = allChatTurns.length ? allChatTurns[allChatTurns.length - 1] : null;
  const lastTurnIsThought = Boolean(
    lastOverallTurn &&
      lastOverallTurn.classList &&
      lastOverallTurn.classList.contains("thought-activity-host")
  );

  const answerTurns = queryAllDeep(document, "ms-chat-turn:not(.thought-activity-host)").filter(
    (t) =>
      t.querySelector('.model-prompt-container, [data-turn-role="Model"]') ||
      !t.querySelector('.user-prompt-container, [data-turn-role="User"]')
  );

  const last = answerTurns.length ? answerTurns[answerTurns.length - 1] : null;
  let lastThought = "";
  let lastText = "";

  const thoughtTurn =
    lastOverallTurn && lastTurnIsThought
      ? lastOverallTurn
      : allChatTurns.length >= 2 &&
          allChatTurns[allChatTurns.length - 2].classList?.contains("thought-activity-host")
        ? allChatTurns[allChatTurns.length - 2]
        : last;

  if (thoughtTurn) {
    const thoughtNodes = queryAllDeep(
      thoughtTurn,
      'ms-thought-chunk, .thought-chunk, mat-expansion-panel.thought-panel, [data-test-id*="thought"]'
    );
    if (thoughtNodes.length) {
      lastThought = thoughtNodes
        .map((n) => (n.innerText || "").trim())
        .filter(Boolean)
        .join("\n\n");
    }
  }

  if (last) {
    const clone = last.cloneNode(true);
    clone
      .querySelectorAll(
        'ms-thought-chunk, .thought-chunk, mat-expansion-panel.thought-panel, [data-test-id*="thought"], .turn-footer, ms-chat-turn-options, .turn-header, .author-label, button'
      )
      .forEach((n) => n.remove());
    lastText = (clone.innerText || "")
      .replace(/^(?:(?:edit|more_vert)\s*)*(?:Model[\s\u00a0\u202f]+\d{1,2}:\d{2}(?:[\s\u00a0\u202f]*(?:AM|PM|SA|CH))?\s*)?/i, "")
      .trim();
  }

  const runBtn = queryAllDeep(document, "ms-run-button button, button.ctrl-enter-submits")[0];
  const runBtnIsStop = Boolean(
    runBtn &&
      ((runBtn.classList && runBtn.classList.contains("stoppable")) ||
        /^\s*stop\b/i.test(runBtn.innerText || ""))
  );
  const stopVisible =
    runBtnIsStop || STOP_SELECTORS.some((s) => queryAllDeep(document, s).some(isVisible));
  const runBusy = Boolean(
    queryAllDeep(
      document,
      'ms-run-button button .mat-mdc-progress-spinner, ms-run-button button.running, [aria-busy="true"]'
    ).length
  );

  return {
    agentCount: answerTurns.length,
    lastThought,
    lastText,
    generating: stopVisible || runBusy || lastTurnIsThought,
    currentUrl: location.href,
  };
}

async function handleChatRequest(req) {
  const reqId = req.request_id;
  const state = { cancelled: false };
  activeRequests.set(reqId, state);

  try {
    let { tab, promptToSend } = await prepareTabForSession(req);
    if (!tab || !tab.id) {
      sendWs({
        type: "error",
        request_id: reqId,
        message: "Không thể mở tab Google AI Studio trên Chrome.",
      });
      return;
    }

    let prepResult = null;
    let navigatedFallback = false;

    for (let attempt = 0; attempt < 35; attempt++) {
      if (state.cancelled) return;
      const injected = await execScriptWithTimeout(
        {
          target: { tabId: tab.id },
          func: domPrepareAndSend,
          args: [
            promptToSend,
            req.upstream_model || req.model || "gemini-4-argon",
            req.thinking_level || "high",
          ],
        },
        6000
      ).catch((err) => [{ result: { ok: false, error: String(err.message || err) } }]);

      prepResult = injected?.[0]?.result;
      if (prepResult && prepResult.ok) break;

      // If the tab wasn't on /prompts/new_chat or Angular hadn't mounted in a background tab,
      // navigate & activate the tab once after attempt 5 so Chrome renders the editor.
      if (!navigatedFallback && (attempt === 5 || prepResult?.needNav)) {
        navigatedFallback = true;
        const navUrl =
          !req.new_chat && isSpecificSavedThreadUrl(req.thread_url)
            ? req.thread_url
            : AI_STUDIO_NEW_CHAT;
        tab = (await navigateTabAndWait(tab.id, navUrl, true)) || tab;
      }
      await sleep(500);
    }

    if (!prepResult || !prepResult.ok) {
      sendWs({
        type: "error",
        request_id: reqId,
        message:
          prepResult?.error ||
          "Không tìm thấy ô nhập prompt trên Google AI Studio. Hãy mở https://aistudio.google.com/prompts/new_chat trên Chrome.",
      });
      return;
    }

    const baseCount = prepResult.turnsBefore || 0;
    let emittedThought = "";
    let emittedText = "";
    let lastThought = "";
    let lastText = "";
    let stableTicks = 0;
    let startedResponse = false;
    let lastHeartbeat = 0;
    const deadline = Date.now() + (Number(req.timeout) || 300) * 1000;

    await sleep(250);

    while (Date.now() < deadline) {
      if (state.cancelled) return;
      const poll = await execScriptWithTimeout(
        {
          target: { tabId: tab.id },
          func: domReadChatState,
        },
        3500
      ).catch(() => null);

      const snap = poll?.[0]?.result;
      if (!snap) {
        await sleep(200);
        continue;
      }

      if (snap.agentCount > baseCount || snap.generating) {
        startedResponse = true;
        if (Date.now() - lastHeartbeat > 2000) {
          lastHeartbeat = Date.now();
          sendWs({ type: "heartbeat", request_id: reqId });
        }
      }

      if (startedResponse && snap.agentCount > baseCount) {
        const curThought = snap.lastThought || "";
        const curText = snap.lastText || "";

        if (curThought && curThought !== emittedThought) {
          const delta = curThought.startsWith(emittedThought)
            ? curThought.slice(emittedThought.length)
            : curThought;
          if (delta) {
            emittedThought = curThought;
            sendWs({ type: "thought_delta", request_id: reqId, delta });
          }
        }

        if (curText && curText !== emittedText) {
          const delta = curText.startsWith(emittedText)
            ? curText.slice(emittedText.length)
            : curText;
          if (delta) {
            emittedText = curText;
            sendWs({ type: "text_delta", request_id: reqId, delta });
          }
        }

        if (
          !snap.generating &&
          curText.length > 0 &&
          curText === lastText &&
          curThought === lastThought
        ) {
          stableTicks += 1;
          if (stableTicks >= 3) {
            if (req.session_id) {
              tabSessionMap.set(tab.id, req.session_id);
            }
            sendWs({
              type: "done",
              request_id: reqId,
              thread_url: snap.currentUrl || "",
              tab_id: tab.id,
            });
            return;
          }
        } else {
          stableTicks = 0;
        }
        lastThought = curThought;
        lastText = curText;
      }

      await sleep(150);
    }

    sendWs({
      type: "error",
      request_id: reqId,
      message: "Hết thời gian chờ phản hồi từ Google AI Studio.",
    });
  } catch (err) {
    sendWs({
      type: "error",
      request_id: reqId,
      message: String(err?.message || err),
    });
  } finally {
    activeRequests.delete(reqId);
  }
}

// Keep WebSocket alive via Chrome Alarms and messages from Popup
chrome.alarms.create("gemini2api-keepalive", { periodInMinutes: 0.33 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "gemini2api-keepalive") {
    connectWebSocket();
    sendStatusHello();
  }
});

chrome.runtime.onInstalled.addListener(() => {
  connectWebSocket();
});

chrome.runtime.onStartup.addListener(() => {
  connectWebSocket();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "get_bridge_state") {
    Promise.all([readAiStudioCookies(), findAiStudioTabs(), getServerUrl()]).then(
      ([cookies, tabs, serverUrl]) => {
        const hasSid = cookies.some((c) => c.name === "SID" || c.name === "__Secure-1PSID");
        sendResponse({
          wsConnected,
          cookieCount: cookies.length,
          hasSid,
          tabCount: tabs.length,
          serverUrl,
        });
      }
    );
    return true;
  }
  if (msg?.type === "reconnect_and_sync") {
    (async () => {
      if (ws) {
        try {
          ws.close();
        } catch {}
        ws = null;
      }
      await connectWebSocket();
      await sleep(300);
      await sendStatusHello();
      const cookies = await readAiStudioCookies();
      const tabs = await findAiStudioTabs();
      sendResponse({
        ok: true,
        wsConnected,
        cookieCount: cookies.length,
        tabCount: tabs.length,
      });
    })();
    return true;
  }
});

connectWebSocket();
