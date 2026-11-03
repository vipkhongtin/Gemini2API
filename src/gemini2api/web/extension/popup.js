"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:18610";
const $ = (id) => document.getElementById(id);

function showLog(text, kind = "ok") {
  const el = $("log");
  el.textContent = text;
  el.className = `show ${kind}`;
}

async function refreshStatus() {
  chrome.runtime.sendMessage({ type: "get_bridge_state" }, (state) => {
    if (chrome.runtime.lastError || !state) return;

    const wsDot = $("wsDot");
    const wsText = $("wsText");
    if (state.wsConnected) {
      wsDot.className = "dot ok";
      wsText.textContent = "Đã kết nối (Live Bridge)";
    } else {
      wsDot.className = "dot warn";
      wsText.textContent = "Chưa kết nối server";
    }

    const cookieDot = $("cookieDot");
    const cookieText = $("cookieText");
    if (state.hasSid) {
      cookieDot.className = "dot ok";
      cookieText.textContent = `Đã đăng nhập (${state.cookieCount} cookies)`;
    } else {
      cookieDot.className = "dot bad";
      cookieText.textContent = "Chưa đăng nhập AI Studio";
    }

    $("tabText").textContent = `${state.tabCount || 0} tab`;
  });
}

async function loadSettings() {
  const stored = await chrome.storage.local.get({
    serverUrl: DEFAULT_SERVER,
    adminKey: "",
    label: "Chrome AI Studio",
  });
  $("serverUrl").value = stored.serverUrl || DEFAULT_SERVER;
  $("adminKey").value = stored.adminKey || "";
  $("label").value = stored.label || "Chrome AI Studio";
}

async function saveSettings() {
  const serverUrl = ($("serverUrl").value.trim() || DEFAULT_SERVER).replace(/\/+$/, "");
  const adminKey = $("adminKey").value.trim();
  const label = $("label").value.trim() || "Chrome AI Studio";
  await chrome.storage.local.set({ serverUrl, adminKey, label });
  return { serverUrl, adminKey, label };
}

async function syncAndConnect() {
  const btn = $("syncBtn");
  btn.disabled = true;
  try {
    const { serverUrl, adminKey, label } = await saveSettings();

    // 1. Reconnect WebSocket Bridge & auto-sync cookies via background worker
    await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "reconnect_and_sync" }, () => resolve());
    });

    // 2. Also POST cookies to /admin/accounts so the account pool is updated immediately
    const [aiCookies, googleCookies] = await Promise.all([
      chrome.cookies.getAll({ url: "https://aistudio.google.com/" }),
      chrome.cookies.getAll({ url: "https://accounts.google.com/" }),
    ]);
    const byName = new Map();
    for (const c of [...googleCookies, ...aiCookies]) {
      if (c.value) byName.set(c.name, c);
    }
    const cookies = Array.from(byName.values()).map((c) => ({
      name: c.name,
      value: c.value,
      domain: c.domain,
      path: c.path,
      secure: c.secure,
      httpOnly: c.httpOnly,
      expirationDate: c.expirationDate ?? null,
    }));

    const headers = { "Content-Type": "application/json" };
    if (adminKey) headers["Authorization"] = `Bearer ${adminKey}`;

    if (cookies.length > 0) {
      const resp = await fetch(`${serverUrl}/admin/accounts`, {
        method: "POST",
        headers,
        body: JSON.stringify({ label, cookies, enabled: true }),
      }).catch(() => null);

      if (resp && resp.ok) {
        const body = await resp.json();
        showLog(
          `✓ Đã kết nối Bridge & đồng bộ tài khoản (${body.account?.id || "ok"}) với ${cookies.length} cookies!\nBạn có thể chat với Gemini 4 Argon ngay.`,
          "ok"
        );
      } else {
        showLog(
          `✓ Đã kích hoạt Live Bridge tới ${serverUrl}.\nHãy giữ 1 tab https://aistudio.google.com mở trên Chrome.`,
          "ok"
        );
      }
    } else {
      showLog(
        "⚠ Chưa tìm thấy phiên đăng nhập Google AI Studio. Hãy bấm 'Mở AI Studio' và đăng nhập tài khoản Google của bạn.",
        "bad"
      );
    }
  } catch (err) {
    showLog(`Lỗi: ${err.message || err}`, "bad");
  } finally {
    btn.disabled = false;
    await refreshStatus();
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  await refreshStatus();
  setInterval(refreshStatus, 2000);

  $("syncBtn").addEventListener("click", syncAndConnect);
  $("serverUrl").addEventListener("change", saveSettings);
  $("adminKey").addEventListener("change", saveSettings);
  $("label").addEventListener("change", saveSettings);

  $("openAiStudioBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://aistudio.google.com/prompts/new_chat" });
  });

  $("openDashboardBtn").addEventListener("click", async () => {
    const { serverUrl } = await saveSettings();
    chrome.tabs.create({ url: `${serverUrl}/dashboard` });
  });
});
