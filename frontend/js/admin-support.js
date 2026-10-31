Dash.register("admin", { id: "support", label: "Support", icon: "bi-life-preserver", async render(el) {
  let status = "OPEN";
  async function load() {
    const list = await App.call("/admin/support", { params: { status } }); if (!list) return; el._l = list;
    el.innerHTML = `<div class="d-flex gap-2 mb-3">${["OPEN", "RESOLVED", ""].map((s) => `<button class="pill-btn ${s === status ? "active" : ""}" data-st="${s}">${s || "All"}</button>`).join("")}</div>` +
      (list.length ? list.map((t) => `<div class="panel mb-2"><div class="d-flex justify-content-between flex-wrap gap-1"><strong>${App.esc(t.subject)}</strong><span class="status-pill ${t.status === "OPEN" ? "warn" : ""}">${t.status}</span></div>
        <div class="meta">${App.esc(t.email)}${t.order_code ? " &middot; order " + App.esc(t.order_code) : ""} &middot; ${App.fmtDate(t.created_at)}</div><p class="my-2">${App.esc(t.message)}</p>
        ${t.admin_reply ? `<div class="panel" style="background:#f5f7f4"><strong class="small">Your reply</strong><div>${App.esc(t.admin_reply)}</div></div>` : ""}
        <button class="btn btn-sm btn-dark mt-1" data-reply="${t.id}">${t.admin_reply ? "Reply again" : "Reply"}</button></div>`).join("") : App.empty("bi-life-preserver", "Nothing here", "No support tickets in this view."));
  }
  el.addEventListener("click", async (e) => { const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.st !== undefined) { status = t.dataset.st; load(); }
    if (t.dataset.reply) { const f = await Dash.formModal({ title: "Reply to customer", submitLabel: "Send reply", fields: [{ name: "reply", label: "Your reply (emailed + shown in their app)", type: "textarea", required: true }, { name: "resolve", label: "Mark as resolved", type: "checkbox", value: true }] });
      if (f && (await App.call(`/admin/support/${t.dataset.reply}/reply`, { method: "POST", body: f }))) { UI.toast("Reply sent."); load(); } } });
  load();
} });
