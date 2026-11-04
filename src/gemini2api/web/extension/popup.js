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
      cookieText.textContent = "Chưa đăng nhập Google";
    }

    if ($("geminiTabText")) {
      $("geminiTabText").textContent = `${state.geminiTabCount || 0} tab`;
    }
    if ($("aiStudioTabText")) {
      $("aiStudioTabText").textContent = `${state.aiStudioTabCount || 0} tab`;
    }
  });
}

async function loadSettings() {
  const stored = await chrome.storage.local.get({
    serverUrl: DEFAULT_SERVER,
    adminKey: "",
    label: "Chrome Chat Gemini",
    upstreamMode: "gemini",
  });
  $("serverUrl").value = stored.serverUrl || DEFAULT_SERVER;
  $("adminKey").value = stored.adminKey || "";
  $("label").value = stored.label || "Chrome Chat Gemini";
  if ($("upstreamMode")) {
    $("upstreamMode").value = stored.upstreamMode === "aistudio" ? "aistudio" : "gemini";
  }
}

async function saveSettings() {
  const serverUrl = ($("serverUrl").value.trim() || DEFAULT_SERVER).replace(/\/+$/, "");
  const adminKey = $("adminKey").value.trim();
  const label = $("label").value.trim() || "Chrome Chat Gemini";
  const upstreamMode = $("upstreamMode") ? $("upstreamMode").value : "gemini";
  await chrome.storage.local.set({ serverUrl, adminKey, label, upstreamMode });
  return { serverUrl, adminKey, label, upstreamMode };
}

async function syncAndConnect() {
  const btn = $("syncBtn");
  btn.disabled = true;
  try {
    const { serverUrl, adminKey, label, upstreamMode } = await saveSettings();

    await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "reconnect_and_sync" }, () => resolve());
    });

    const [geminiCookies, aiCookies, googleCookies] = await Promise.all([
      chrome.cookies.getAll({ url: "https://gemini.google.com/" }),
      chrome.cookies.getAll({ url: "https://aistudio.google.com/" }),
      chrome.cookies.getAll({ url: "https://accounts.google.com/" }),
    ]);
    const byName = new Map();
    for (const c of [...googleCookies, ...aiCookies, ...geminiCookies]) {
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

    const modeName =
      upstreamMode === "aistudio"
        ? "Google AI Studio (Phụ)"
        : "Chat Gemini - gemini.google.com (Chính)";

    if (cookies.length > 0) {
      const resp = await fetch(`${serverUrl}/admin/accounts`, {
        method: "POST",
        headers,
        body: JSON.stringify({ label, cookies, enabled: true }),
      }).catch(() => null);

      if (resp && resp.ok) {
        const body = await resp.json();
        showLog(
          `✓ Đã kết nối Bridge (${modeName}) & đồng bộ tài khoản (${body.account?.id || "ok"}) với ${cookies.length} cookies!\nBạn có thể gọi API hoặc chat ngay.`,
          "ok"
        );
      } else {
        showLog(
          `✓ Đã kích hoạt Live Bridge (${modeName}) tới ${serverUrl}.`,
          "ok"
        );
      }
    } else {
      showLog(
        "⚠ Chưa tìm thấy phiên đăng nhập Google. Hãy bấm '💬 Mở Chat Gemini (Chính)' và đăng nhập tài khoản Google của bạn.",
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
  if ($("upstreamMode")) {
    $("upstreamMode").addEventListener("change", async () => {
      await saveSettings();
      chrome.runtime.sendMessage({ type: "reconnect_and_sync" }, () => refreshStatus());
    });
  }

  if ($("openGeminiBtn")) {
    $("openGeminiBtn").addEventListener("click", () => {
      chrome.tabs.create({ url: "https://gemini.google.com/app" });
    });
  }

  $("openAiStudioBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://aistudio.google.com/prompts/new_chat" });
  });

  $("openDashboardBtn").addEventListener("click", async () => {
    const { serverUrl } = await saveSettings();
    chrome.tabs.create({ url: `${serverUrl}/dashboard` });
  });
});
