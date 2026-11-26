Dash.register("admin", { id: "coupons", label: "Coupons & promos", icon: "bi-ticket-perforated", async render(el) {
  const iso = (v) => (v ? new Date(v).toISOString() : null);
  async function load() {
    const [coupons, promos] = await Promise.all([App.call("/admin/coupons"), App.call("/admin/promotions")]); if (!coupons || !promos) return;
    el._c = coupons;
    el.innerHTML = `<div class="d-flex justify-content-between align-items-center mb-2"><h2 class="h6 mb-0">Coupon codes</h2><button class="btn btn-dark btn-sm" id="add">+ Platform coupon</button></div>` +
      (coupons.length ? Dash.table(["Code", "Offer", "Scope", "Used", "Status", ""], coupons.map((c) => `<tr><td><strong>${App.esc(c.code)}</strong><div class="meta">${App.esc(c.description)}</div></td>
        <td>${c.type === "PERCENT" ? c.value + "%" : c.type === "FIXED" ? App.money(c.value) : "Free delivery"}${c.first_order_only ? '<div class="meta">first order</div>' : ""}</td><td>${c.vendor_id ? "Vendor #" + c.vendor_id : "Platform"}</td>
        <td>${c.times_used}${c.usage_limit ? "/" + c.usage_limit : ""}</td><td><span class="status-pill ${c.is_active ? "" : "bad"}">${c.is_active ? "Active" : "Off"}</span></td>
        <td class="text-end">${c.is_active ? `<button class="btn btn-sm btn-outline-danger" data-off="${c.id}">Turn off</button>` : ""}</td></tr>`)) : App.empty("bi-ticket-perforated", "No coupons", "")) +
      `<h2 class="h6 mt-4">Vendor promotions</h2>` + (promos.length ? Dash.table(["Vendor", "Promotion", "Discount", "Ends", "Active"], promos.map((p) => `<tr><td>${App.esc(p.vendor_name)}</td><td>${App.esc(p.title)}</td><td>${p.discount_percent}%</td><td>${App.fmtDate(p.ends_at)}</td>
        <td><div class="form-check form-switch"><input class="form-check-input" type="checkbox" data-pt="${p.id}" ${p.is_active ? "checked" : ""} aria-label="Promotion active"></div></td></tr>`)) : App.empty("bi-megaphone", "No promotions", ""));
  }
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.off && (await App.call(`/admin/coupons/${t.dataset.off}`, { method: "DELETE" }))) load();
    if (t.id === "add") {
      const f = await Dash.formModal({ title: "New platform coupon", fields: [
        { name: "code", label: "Code", required: true }, { name: "description", label: "Description" },
        { name: "type", label: "Type", type: "select", options: [{ value: "PERCENT", label: "Percentage off" }, { value: "FIXED", label: "Fixed GH₵ off" }, { value: "FREE_DELIVERY", label: "Free delivery" }] },
        { name: "value", label: "Value", type: "number", step: "0.01", value: 10 }, { name: "min_order", label: "Minimum order (GH₵)", type: "number", step: "0.01", value: 0 },
        { name: "max_discount", label: "Max discount (GH₵, optional)", type: "number", step: "0.01" }, { name: "expires_at", label: "Expires (optional)", type: "datetime-local" },
        { name: "usage_limit", label: "Total uses (optional)", type: "number" }, { name: "per_user_limit", label: "Uses per customer", type: "number", value: 1 },
        { name: "first_order_only", label: "First orders only", type: "checkbox" }] });
      if (f && (await App.call("/admin/coupons", { method: "POST", body: { code: f.code, description: f.description, type: f.type, value: String(f.value || 0), min_order: String(f.min_order || 0), max_discount: f.max_discount ? String(f.max_discount) : null, expires_at: iso(f.expires_at), usage_limit: f.usage_limit ? Number(f.usage_limit) : null, per_user_limit: Number(f.per_user_limit) || 1, first_order_only: f.first_order_only } }))) { UI.toast("Coupon created."); load(); }
    }
  });
  el.addEventListener("change", async (e) => { const t = e.target.closest("[data-pt]"); if (t && !(await App.call(`/admin/promotions/${t.dataset.pt}/active`, { method: "PATCH", params: { is_active: t.checked } }))) t.checked = !t.checked; });
  load();
} });
