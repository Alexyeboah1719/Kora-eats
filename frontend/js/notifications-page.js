(async () => {
  if (!(await App.mountShell({ requireLogin: true }))) return;
  const root = document.getElementById("page");
  const ICON = { order_created: "bi-receipt", payment_success: "bi-check-circle", payment_failed: "bi-x-circle", order_accepted: "bi-hand-thumbs-up", food_preparing: "bi-fire", food_ready: "bi-bag-check", rider_assigned: "bi-bicycle", order_picked_up: "bi-box-seam", out_for_delivery: "bi-truck", rider_nearby: "bi-geo-alt", rider_arrived: "bi-pin-map", order_delivered: "bi-emoji-smile", order_cancelled: "bi-slash-circle", order_refunded: "bi-arrow-counterclockwise", new_order: "bi-bell", delivery_available: "bi-bicycle", review_reminder: "bi-star", promotion: "bi-megaphone", vendor_approved: "bi-patch-check", rider_approved: "bi-patch-check" };
  root.innerHTML = `<div class="d-flex justify-content-between align-items-center mb-3"><h1 class="h4 mb-0">Notifications</h1><button class="btn btn-sm btn-outline-dark" id="read-all">Mark all as read</button></div>
    <div id="push-card"></div><div id="list">${App.skeletons(3)}</div>`;
  async function renderPush() {
    const st = await Notifs.pushState(), el = document.getElementById("push-card");
    const msg = { off: ["Get alerts even when the app is closed", "Enable push notifications", "enable"], on: ["Push notifications are on for this device", "Turn off", "disable"],
      denied: ["Notifications are blocked in your browser settings.", null], unsupported: ["This browser doesn't support push notifications.", null], unconfigured: ["Push notifications aren't set up on this server yet.", null] }[st];
    el.innerHTML = `<div class="panel mb-3 d-flex justify-content-between align-items-center flex-wrap gap-2"><span><i class="bi bi-bell"></i> ${msg[0]}</span>${msg[1] ? `<button class="btn btn-sm btn-dark" data-push="${msg[2]}">${msg[1]}</button>` : ""}</div>`;
  }
  async function load() {
    const list = await App.call("/notifications"); if (!list) return;
    document.getElementById("list").innerHTML = list.length ? list.map((n) => `<a class="panel d-flex gap-3 text-decoration-none text-reset mb-2" href="${App.esc(n.url || "#")}" data-n="${n.id}" style="${n.is_read ? "" : "border-color:var(--gold);background:#fffaf0"}">
      <i class="bi ${ICON[n.type] || "bi-bell"} fs-4" style="color:var(--ok)"></i><div class="flex-grow-1"><strong>${App.esc(n.title)}</strong><div class="meta">${App.esc(n.body)}</div><div class="meta">${App.timeAgo(n.created_at)}</div></div></a>`).join("")
      : App.empty("bi-bell", "You're all caught up", "Order updates and offers show up here.");
  }
  root.addEventListener("click", async (e) => {
    const p = e.target.closest("[data-push]"); if (p) { p.dataset.push === "enable" ? await Notifs.enablePush() : await Notifs.disablePush(); return renderPush(); }
    if (e.target.id === "read-all") { await App.call("/notifications/read-all", { method: "POST" }); load(); Notifs.refreshBadge(); return; }
    const n = e.target.closest("[data-n]"); if (n) App.api(`/notifications/${n.dataset.n}/read`, { method: "POST" });
  });
  renderPush(); load();
})();
