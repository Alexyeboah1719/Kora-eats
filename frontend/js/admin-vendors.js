Dash.register("admin", { id: "vendors", label: "Vendors", icon: "bi-shop", async render(el) {
  let status = "PENDING";
  async function load() {
    const list = await App.call("/admin/vendors", { params: { status } }); if (!list) return; el._list = list;
    el.innerHTML = `<div class="d-flex gap-2 mb-3 flex-wrap">${["PENDING", "APPROVED", "REJECTED", ""].map((s) => `<button class="pill-btn ${s === status ? "active" : ""}" data-st="${s}">${s || "All"}</button>`).join("")}</div>` +
      (list.length ? Dash.table(["Business", "Owner", "Contact", "Status", "Documents", ""], list.map((v) => `<tr>
        <td><strong>${App.esc(v.name)}</strong> <span class="chip-mini">${App.esc(v.business_type.replace(/_/g, " "))}</span><div class="meta">${App.esc(v.area ? v.area + ", " : "")}${App.esc(v.address_text)}</div></td><td>${App.esc(v.owner_name || "")}</td>
        <td>${App.esc(v.contact_email || "")}<div class="meta">${App.esc(v.phone || "")}</div></td>
        <td><span class="status-pill ${v.status === "REJECTED" ? "bad" : v.status === "PENDING" ? "warn" : ""}">${v.status}</span>${v.rejection_reason ? `<div class="meta">${App.esc(v.rejection_reason)}</div>` : ""}</td>
        <td>${v.documents.length ? v.documents.map((d) => `<a class="d-block small" href="/api/admin/vendors/${v.id}/documents/${d.id}" target="_blank" rel="noopener">${App.esc(d.doc_type.replace(/_/g, " "))}</a>`).join("") : '<span class="meta">None</span>'}${v.status !== "APPROVED" && v.missing_documents.length ? `<div class="small text-danger mt-1">Missing: ${v.missing_documents.map(App.esc).join("; ")}</div>` : ""}</td>
        <td class="text-end text-nowrap">${v.status !== "APPROVED" ? `<button class="btn btn-sm btn-success" data-approve="${v.id}">Approve</button>` : ""}
          ${v.status !== "REJECTED" ? ` <button class="btn btn-sm btn-outline-danger" data-reject="${v.id}">Reject</button>` : ""}</td></tr>`)) : App.empty("bi-shop", "No vendors in this list", ""));
  }
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.st !== undefined) { status = t.dataset.st; load(); }
    if (t.dataset.approve) {
      const v = (el._list || []).find((x) => x.id === Number(t.dataset.approve)) || { missing_documents: [] };
      const force = v.missing_documents.length > 0;
      const q = force ? `This vendor is missing: ${v.missing_documents.join("; ")}.\n\nApprove anyway? (This will be recorded in the audit log.)` : "Approve this vendor? They'll go live immediately.";
      if (await Dash.confirmBox(q) && (await App.call(`/admin/vendors/${t.dataset.approve}/approve`, { method: "POST", params: force ? { force: true } : undefined }))) { UI.toast("Vendor approved."); load(); }
    }
    if (t.dataset.reject) { const f = await Dash.formModal({ title: "Reject vendor", submitLabel: "Reject", fields: [{ name: "reason", label: "Reason (sent to the vendor)", type: "textarea", required: true }] }); if (f && (await App.call(`/admin/vendors/${t.dataset.reject}/reject`, { method: "POST", body: f }))) { UI.toast("Vendor rejected."); load(); } }
  });
  load();
} });
