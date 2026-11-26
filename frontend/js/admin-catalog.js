Dash.register("admin", { id: "catalog", label: "Categories", icon: "bi-grid", async render(el) {
  const fields = (c = {}) => [{ name: "name", label: "Name", value: c.name, required: true, maxlength: 60 }, { name: "icon", label: "Icon (Bootstrap Icons class)", value: c.icon || "bi-egg-fried", hint: "Browse names at icons.getbootstrap.com, e.g. bi-cup-hot" },
    { name: "sort_order", label: "Sort order", type: "number", min: 0, value: c.sort_order ?? 0 }, { name: "is_active", label: "Visible to customers", type: "checkbox", value: c.is_active ?? true }];
  const body = (f) => ({ name: f.name, icon: f.icon || "bi-egg-fried", sort_order: Number(f.sort_order) || 0, is_active: f.is_active });
  async function load() {
    const list = await App.call("/admin/categories"); if (!list) return; el._l = list;
    el.innerHTML = `<div class="d-flex justify-content-end mb-3"><button class="btn btn-dark" id="add">+ New category</button></div>` + Dash.table(["", "Name", "Items", "Visible", ""], list.map((c) => `<tr><td><i class="bi ${App.esc(c.icon)} fs-5"></i></td><td><strong>${App.esc(c.name)}</strong></td><td>${c.items}</td><td><span class="status-pill ${c.is_active ? "" : "bad"}">${c.is_active ? "Yes" : "Hidden"}</span></td>
      <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-dark" data-e="${c.id}">Edit</button> <button class="btn btn-sm btn-outline-danger" data-d="${c.id}">Delete</button></td></tr>`));
  }
  el.addEventListener("click", async (e) => { const t = e.target.closest("button"); if (!t) return;
    if (t.id === "add") { const f = await Dash.formModal({ title: "New category", fields: fields() }); if (f && (await App.call("/admin/categories", { method: "POST", body: body(f) }))) load(); }
    if (t.dataset.e) { const c = el._l.find((x) => x.id === Number(t.dataset.e)); const f = await Dash.formModal({ title: "Edit category", fields: fields(c) }); if (f && (await App.call(`/admin/categories/${c.id}`, { method: "PUT", body: body(f) }))) load(); }
    if (t.dataset.d && confirm("Delete this category?")) { if (await App.call(`/admin/categories/${t.dataset.d}`, { method: "DELETE" })) load(); } });
  load();
} });
