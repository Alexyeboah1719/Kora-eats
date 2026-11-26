/* Registers the service worker, offers "Install app", and tells the user when they go offline. */
const PWA = (() => {
  let deferred = null, inited = false;
  const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;

  function renderInstall() {
    const slot = document.getElementById("install-slot");
    if (!slot || standalone()) return;
    if (deferred) {
      slot.innerHTML = '<button class="panel w-100 text-start mb-2" id="install-btn" type="button"><i class="bi bi-download"></i> <strong>Install KORA EATS</strong><br><span class="meta">Add it to your home screen for faster ordering.</span></button>';
      document.getElementById("install-btn").addEventListener("click", async () => { deferred.prompt(); await deferred.userChoice; deferred = null; slot.innerHTML = ""; });
    } else if (isIOS()) {
      slot.innerHTML = '<div class="panel mb-2"><i class="bi bi-box-arrow-up"></i> <strong>Install on iPhone</strong><br><span class="meta">Tap the Share button, then "Add to Home Screen".</span></div>';
    }
  }

  // ---------------------------------------------------------------- global install banner
  /* A dismissible bottom banner so new users actually discover the app can be installed.
     Shows once the browser offers the install prompt (or on iOS, which needs manual steps).
     Dismissal is remembered for 7 days. */
  const DISMISS_KEY = "pwa.install.dismissed";
  const dismissedRecently = () => Date.now() - (Number(localStorage.getItem(DISMISS_KEY)) || 0) < 7 * 864e5;

  function showInstallBanner() {
    if (standalone() || dismissedRecently() || document.getElementById("pwa-install-banner")) return;
    if (!deferred && !isIOS()) return;                    // desktop browsers without the prompt: nothing to offer yet
    const el = document.createElement("div");
    el.id = "pwa-install-banner";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Install KORA EATS");
    el.style.cssText = "position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:1080;display:flex;align-items:center;gap:12px;background:#0F2E22;color:#fff;border-radius:14px;padding:12px 14px;box-shadow:0 8px 30px rgba(0,0,0,.25);font-family:inherit";
    el.innerHTML = `<img src="/icons/icon-192.png" alt="" width="44" height="44" style="border-radius:10px;flex:none">
      <div style="flex:1;min-width:0"><strong style="font-size:.95rem">Install KORA EATS</strong>
      <div id="pwa-install-hint" style="font-size:.8rem;opacity:.85">Add it to your home screen — faster ordering, works offline.</div></div>
      <button id="pwa-install-go" type="button" style="flex:none;border:0;border-radius:10px;background:#E8B93E;color:#1d1d1b;font-weight:700;padding:9px 16px;cursor:pointer">Install</button>
      <button id="pwa-install-x" type="button" aria-label="Dismiss" style="flex:none;border:0;background:transparent;color:#fff;font-size:1.3rem;line-height:1;cursor:pointer;padding:4px 6px;opacity:.8">&times;</button>`;
    document.body.append(el);

    el.querySelector("#pwa-install-x").addEventListener("click", () => { el.remove(); localStorage.setItem(DISMISS_KEY, String(Date.now())); });
    el.querySelector("#pwa-install-go").addEventListener("click", async () => {
      if (deferred) {
        deferred.prompt();
        await deferred.userChoice;
        deferred = null; el.remove();
      } else if (isIOS()) {
        el.querySelector("#pwa-install-hint").textContent = 'Tap the Share button <i class="bi bi-box-arrow-up"></i> at the bottom, then "Add to Home Screen".';
        el.querySelector("#pwa-install-go").textContent = "Got it";
      }
    });
  }

  function init() {
    if (inited) return; inited = true;
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { /* e.g. plain http on a non-localhost host */ });
    window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; renderInstall(); showInstallBanner(); });
    window.addEventListener("appinstalled", () => { deferred = null; document.getElementById("pwa-install-banner")?.remove(); UI.toast("KORA EATS installed."); });
    window.addEventListener("offline", () => UI.toast("You're offline. Saved menus are still available.", "error"));
    window.addEventListener("online", () => UI.toast("Back online."));
    renderInstall();
    setTimeout(() => { renderInstall(); showInstallBanner(); }, 1500);   // the profile page renders its slot after the shell
  }
  return { init };
})();
