Dash.register("admin", { id: "users", label: "Users", icon: "bi-people", async render(el) {
  let role = "", q = "";
  async function load() {
    const list = await App.call("/admin/users", { params: { role, q } }); if (!list) return;
    el.querySelector("#rows").innerHTML = list.length ? Dash.table(["User", "Role", "Phone", "Spent", "Joined", "Status", ""], list.map((u) => `<tr><td><strong>${App.esc(u.name)}</strong><div class="meta">${App.esc(u.email)}${u.verified ? "" : " (unverified)"}</div></td><td>${App.esc(u.role)}</td><td>${App.esc(u.phone || "")}</td><td>${App.money(u.total_spent)}</td><td class="small">${App.fmtDate(u.created_at)}</td>
      <td><span class="status-pill ${u.is_active ? "" : "bad"}">${u.is_active ? "Active" : "Disabled"}</span></td><td class="text-end">${u.role === "ADMIN" ? "" : `<button class="btn btn-sm ${u.is_active ? "btn-outline-danger" : "btn-outline-dark"}" data-id="${u.id}" data-to="${!u.is_active}">${u.is_active ? "Disable" : "Enable"}</button>`}</td></tr>`)) : App.empty("bi-people", "No users found", "");
  }
  el.innerHTML = `<div class="d-flex gap-2 flex-wrap mb-3"><input class="form-control w-auto" id="q" placeholder="Search by email" aria-label="Search users"><select class="form-select w-auto" id="role" aria-label="Role"><option value="">All roles</option>${["CUSTOMER", "VENDOR", "RIDER", "ADMIN"].map((r) => `<option>${r}</option>`).join("")}</select></div><div id="rows"></div>`;
  el.querySelector("#q").addEventListener("input", (e) => { clearTimeout(el._t); el._t = setTimeout(() => { q = e.target.value.trim(); load(); }, 300); });
  el.querySelector("#role").addEventListener("change", (e) => { role = e.target.value; load(); });
  el.addEventListener("click", async (e) => { const t = e.target.closest("[data-id]"); if (!t) return;
    if (t.dataset.to === "false" && !confirm("Disable this account? They'll be logged out everywhere immediately.")) return;
    if (await App.call(`/admin/users/${t.dataset.id}/active`, { method: "PATCH", params: { is_active: t.dataset.to } })) { UI.toast("User updated."); load(); } });
  load();
} });
