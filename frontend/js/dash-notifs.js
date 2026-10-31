/* A Notifications section for every dashboard (vendor / rider / admin). */
["vendor", "rider", "admin"].forEach((area) => Dash.register(area, { id: "notifications", label: "Notifications", icon: "bi-bell", async render(el) {
  async function load() {
    const list = await App.call("/notifications"); if (!list) return;
    el.innerHTML = `<div class="d-flex justify-content-between mb-3"><span class="meta">${list.filter((n) => !n.is_read).length} unread</span><button class="btn btn-sm btn-outline-dark" id="ra">Mark all as read</button></div>` +
      (list.length ? list.map((n) => `<div class="panel mb-2" style="${n.is_read ? "" : "border-color:var(--gold);background:#fffaf0"}"><div class="d-flex justify-content-between"><strong>${App.esc(n.title)}</strong><span class="meta">${App.timeAgo(n.created_at)}</span></div><div class="meta">${App.esc(n.body)}</div>${n.url ? `<a class="small" href="${App.esc(n.url)}" data-n="${n.id}">Open</a>` : ""}</div>`).join("") : App.empty("bi-bell", "No notifications yet", ""));
  }
  el.addEventListener("click", async (e) => { if (e.target.id === "ra") { await App.call("/notifications/read-all", { method: "POST" }); load(); } const n = e.target.closest("[data-n]"); if (n) App.api(`/notifications/${n.dataset.n}/read`, { method: "POST" }); });
  load();
} }));
