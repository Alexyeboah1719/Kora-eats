/* Notification bell badge + Web Push enrolment. */
const Notifs = (() => {
  async function refreshBadge() {
    const r = await App.api("/notifications/unread-count");
    const b = document.getElementById("notif-badge");
    if (r.ok && b) { b.textContent = r.data.count > 99 ? "99+" : r.data.count; b.classList.toggle("d-none", !r.data.count); }
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
  setInterval(() => { if (App.state.user) refreshBadge(); }, 30000);
  return { refreshBadge, pushState, enablePush, disablePush };
})();
