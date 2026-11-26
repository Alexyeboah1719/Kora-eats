Dash.register("admin", { id: "riders", label: "Riders", icon: "bi-bicycle", async render(el) {
  let status = "PENDING";
  async function load() {
    const list = await App.call("/admin/riders", { params: { status } }); if (!list) return;
    el.innerHTML = `<div class="d-flex gap-2 mb-3 flex-wrap">${["PENDING", "APPROVED", "REJECTED", ""].map((s) => `<button class="pill-btn ${s === status ? "active" : ""}" data-st="${s}">${s || "All"}</button>`).join("")}</div>` +
      (list.length ? Dash.table(["Rider", "Contact", "Vehicle", "Status", "ID document", ""], list.map((r) => `<tr><td><strong>${App.esc(r.name)}</strong><div class="meta">${App.esc(r.email)}</div></td><td>${App.esc(r.phone)}</td>
        <td>${App.esc(r.vehicle_type)}<div class="meta">${App.esc(r.plate_number || "")}</div></td>
        <td><span class="status-pill ${r.approval_status === "REJECTED" ? "bad" : r.approval_status === "PENDING" ? "warn" : ""}">${r.approval_status}</span>${r.approval_status === "APPROVED" ? `<div class="meta">${r.is_online ? "Online" : "Offline"}</div>` : ""}</td>
        <td>${r.has_document ? `<a href="/api/admin/riders/${r.id}/document" target="_blank" rel="noopener">View</a>` : '<span class="meta">None</span>'}</td>
        <td class="text-end text-nowrap">${r.approval_status !== "APPROVED" ? `<button class="btn btn-sm btn-success" data-approve="${r.id}">Approve</button>` : ""}${r.approval_status !== "REJECTED" ? ` <button class="btn btn-sm btn-outline-danger" data-reject="${r.id}">${r.approval_status === "APPROVED" ? "Revoke" : "Reject"}</button>` : ""}</td></tr>`)) : App.empty("bi-bicycle", "No riders in this list", ""));
  }
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.st !== undefined) { status = t.dataset.st; load(); }
    if (t.dataset.approve && (await App.call(`/admin/riders/${t.dataset.approve}/approve`, { method: "POST" }))) { UI.toast("Rider approved."); load(); }
    if (t.dataset.reject) { const f = await Dash.formModal({ title: "Reject rider", submitLabel: "Reject", fields: [{ name: "reason", label: "Reason (sent to the rider)", type: "textarea", required: true }] }); if (f && (await App.call(`/admin/riders/${t.dataset.reject}/reject`, { method: "POST", body: f }))) { UI.toast("Done."); load(); } }
  });
  load();
} });
