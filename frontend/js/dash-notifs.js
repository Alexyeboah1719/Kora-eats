/* A Notifications section for every dashboard (vendor / rider / admin). */
["vendor", "rider", "admin"].forEach((area) => Dash.register(area, { id: "notifications", label: "Notifications", icon: "bi-bell", async render(el) {
  async function load() {
    const list = await App.call("/notifications"); if (!list) return;
    // Push prompt: without a subscription, order alerts only exist inside the app. Show a one-tap
    // enable card until the user turns alerts on (or the browser can't do push).
    const pst = await Notifs.pushState();
    const pushCard = pst === "off" ? `<div class="panel mb-3 d-flex justify-content-between align-items-center flex-wrap gap-2" style="border-color:var(--gold);background:#fffaf0">
        <span><i class="bi bi-bell"></i> <strong>Get alerts on this device</strong><div class="meta">New orders and updates will pop up like a phone notification, even when this app is closed.</div></span>
        <button class="btn btn-dark btn-sm" id="push-on">Enable alerts</button></div>` : "";
    el.innerHTML = pushCard + `<div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2"><span class="meta">${list.filter((n) => !n.is_read).length} unread</span><span><a class="btn btn-sm btn-outline-dark" href="/notifications.html"><i class="bi bi-bell"></i> Alert settings</a> <button class="btn btn-sm btn-outline-dark" id="ra">Mark all as read</button></span></div>` +
      (list.length ? list.map((n) => `<div class="panel mb-2" style="${n.is_read ? "" : "border-color:var(--gold);background:#fffaf0"}"><div class="d-flex justify-content-between"><strong>${App.esc(n.title)}</strong><span class="meta">${App.timeAgo(n.created_at)}</span></div><div class="meta">${App.esc(n.body)}</div>${n.url ? `<a class="small" href="${App.esc(n.url)}" data-n="${n.id}">Open</a>` : ""}</div>`).join("") : App.empty("bi-bell", "No notifications yet", ""));
  }
  el.addEventListener("click", async (e) => {
    if (e.target.id === "push-on") { await Notifs.enablePush(); return load(); }
    if (e.target.id === "ra") { await App.call("/notifications/read-all", { method: "POST" }); load(); } const n = e.target.closest("[data-n]"); if (n) App.api(`/notifications/${n.dataset.n}/read`, { method: "POST" });
  });
  load();
} }));
