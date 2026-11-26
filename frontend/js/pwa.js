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

  function init() {
    if (inited) return; inited = true;
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { /* e.g. plain http on a non-localhost host */ });
    window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; renderInstall(); });
    window.addEventListener("appinstalled", () => { deferred = null; UI.toast("KORA EATS installed."); });
    window.addEventListener("offline", () => UI.toast("You're offline. Saved menus are still available.", "error"));
    window.addEventListener("online", () => UI.toast("Back online."));
    renderInstall();
    setTimeout(renderInstall, 1500);           // the profile page renders its slot after the shell
  }
  return { init };
})();
