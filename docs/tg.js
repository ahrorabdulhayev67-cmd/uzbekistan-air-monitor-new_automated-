/* Toshkent havosi — Telegram Mini App integratsiyasi va "kengaytirish / kichraytirish" tugmasi (barcha sahifalar) */
(() => {
  "use strict";
  const tg = window.Telegram && window.Telegram.WebApp;
  const inTg = !!(tg && tg.initData);
  if (inTg) { try { tg.ready(); tg.expand(); } catch (e) { /* eski mijoz */ } }

  const canTgFull = inTg && typeof tg.requestFullscreen === "function" && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0");
  const canDocFull = !inTg && !!document.documentElement.requestFullscreen;
  if (inTg && !canTgFull && tg.isExpanded) return;          // eski Telegram: faqat expand, tugma kerak emas

  const css = document.createElement("style");
  css.textContent = `
    .fs-btn { margin-left: 10px; width: 34px; height: 34px; border-radius: 9px; border: 1px solid var(--line);
      background: var(--card); color: var(--ink); cursor: pointer; display: inline-grid; place-items: center; flex: 0 0 auto; }
    .fs-btn:hover { background: var(--bg); }
    .fs-btn svg { width: 18px; height: 18px; }
    .topbar .updated { margin-left: auto; }
    @media (max-width: 560px) { .fs-btn { margin-left: auto; } }
    body.tg-full .topbar { padding-top: calc(10px + var(--tg-content-safe-area-inset-top, 0px) + var(--tg-safe-area-inset-top, 0px)); }`;
  document.head.appendChild(css);

  const ICON_EXPAND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
  const ICON_SHRINK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>';

  function isFull() { return canTgFull ? !!tg.isFullscreen : !!document.fullscreenElement; }
  function paint(btn) {
    const f = isFull();
    btn.innerHTML = f ? ICON_SHRINK : ICON_EXPAND;
    btn.title = f ? "Kichraytirish" : "Kengaytirish";
    btn.setAttribute("aria-label", btn.title);
    document.body.classList.toggle("tg-full", f && canTgFull);
    setTimeout(() => dispatchEvent(new Event("resize")), 120);   // grafik va xaritalar o'lchamini yangilaydi
  }
  function toggle() {
    try {
      if (canTgFull) { isFull() ? tg.exitFullscreen() : tg.requestFullscreen(); }
      else if (canDocFull) { isFull() ? document.exitFullscreen() : document.documentElement.requestFullscreen(); }
      else if (inTg) { tg.expand(); }
    } catch (e) { console.warn("fullscreen", e); }
  }

  function mount() {
    const bar = document.querySelector(".topbar");
    if (!bar || bar.querySelector(".fs-btn")) return;
    const btn = document.createElement("button");
    btn.className = "fs-btn"; btn.type = "button"; btn.onclick = toggle;
    bar.appendChild(btn); paint(btn);
    if (canTgFull) {
      tg.onEvent("fullscreenChanged", () => paint(btn));
      tg.onEvent("fullscreenFailed", (e) => console.warn("Telegram fullscreen:", e && e.error));
    } else {
      document.addEventListener("fullscreenchange", () => paint(btn));
    }
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", mount) : mount();
})();
