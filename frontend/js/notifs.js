/* Notification bell badge + in-app alert (sound + toast) + Web Push enrolment.
   Works for every signed-in role (customer, vendor, rider, admin): the unread-count poll runs on
   every page, so whoever receives a notification hears/sees it within ~15 seconds. */
const Notifs = (() => {
  let lastCount = null;
  let audioCtx = null;

  /* Browsers block audio until the user interacts with the page once; we unlock on the first gesture. */
  function ensureAudio() {
    if (!audioCtx) { try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) { /* no audio */ } }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  }
  ["pointerdown", "keydown"].forEach((ev) => window.addEventListener(ev, ensureAudio, { once: true }));

  /* Short two-note chime, generated with Web Audio — no sound file to download. */
  function chime() {
    try {
      ensureAudio();
      if (!audioCtx || audioCtx.state !== "running") return;
      const t = audioCtx.currentTime;
      [987.77, 1318.51].forEach((f, i) => {              // B5 -> E6
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.type = "sine"; o.frequency.value = f;
        const at = t + i * 0.16;
        g.gain.setValueAtTime(0.0001, at);
        g.gain.exponentialRampToValueAtTime(0.22, at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, at + 0.3);
        o.connect(g).connect(audioCtx.destination);
        o.start(at); o.stop(at + 0.35);
      });
    } catch (_) { /* audio is best-effort */ }
  }

  async function onNewNotification() {
    chime();
    try {
      const r = await App.api("/notifications", { params: { unread_only: true, limit: 1 } });
      const n = r.ok && Array.isArray(r.data) ? r.data[0] : null;
      UI.toast(n ? `${n.title}: ${n.body}`.slice(0, 140) : "You have a new notification.", "success");
    } catch (_) { UI.toast("You have a new notification.", "success"); }
  }

  async function refreshBadge() {
    let r;
    try { r = await App.api("/notifications/unread-count"); } catch (_) { return; }
    if (!r.ok || !r.data) return;
    const count = r.data.count;
    if (typeof count !== "number") return;
    if (lastCount !== null && count > lastCount) onNewNotification();   // first fetch only sets the baseline
    lastCount = count;
    const b = document.getElementById("notif-badge");
    if (b) { b.textContent = count > 99 ? "99+" : count; b.classList.toggle("d-none", !count); }
  }
  const b64ToBytes = (s) => { const pad = "=".repeat((4 - (s.length % 4)) % 4); const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(raw, (c) => c.charCodeAt(0)); };
  const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  async function pushState() {
    if (!supported()) return "unsupported";
    const k = await App.api("/notifications/push/public-key");
    if (!k.ok || !k.data.public_key) return "unconfigured";
    if (Notification.permission === "denied") return "denied";
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    return sub ? "on" : "off";
  }
  async function enablePush() {
    const k = await App.api("/notifications/push/public-key");
    if (!k.ok || !k.data.public_key) return UI.toast("Push notifications aren't set up on this server yet.", "error");
    if ((await Notification.requestPermission()) !== "granted") return UI.toast("Notifications were blocked in your browser settings.", "error");
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(k.data.public_key) }));
    const r = await App.api("/notifications/push/subscribe", { method: "POST", body: sub.toJSON() });
    UI.toast(r.ok ? "Push notifications enabled." : App.errorMessage(r), r.ok ? "success" : "error");
    return r.ok;
  }
  async function disablePush() {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) { await App.api("/notifications/push/unsubscribe", { method: "POST", params: { endpoint: sub.endpoint } }); await sub.unsubscribe(); }
    UI.toast("Push notifications turned off.");
  }
  setInterval(() => { if (App.state.user) refreshBadge(); }, 15000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && App.state.user) refreshBadge(); });
  return { refreshBadge, pushState, enablePush, disablePush };
})();
