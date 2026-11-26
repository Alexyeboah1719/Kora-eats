/* Vendor dashboard: promotions (automatic discounts) and coupon codes. */
Dash.register("vendor", { id: "promotions", label: "Promotions", icon: "bi-megaphone", async render(el) {
  const iso = (v) => (v ? new Date(v).toISOString() : null);
  const local = (iso) => { if (!iso) return ""; const d = new Date(iso), p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  async function load() {
    const [promos, coupons, menu] = await Promise.all([App.call("/vendor/promotions"), App.call("/vendor/coupons"), App.call("/vendor/menu")]);
    if (!promos || !coupons) return;
    el._menu = menu || []; el._promos = promos; el._coupons = coupons;
    const now = Date.now();
    el.innerHTML = `<div class="d-flex justify-content-between align-items-center mb-2"><h2 class="h6 mb-0">Automatic discounts</h2><button class="btn btn-dark btn-sm" id="add-promo"><i class="bi bi-plus-lg"></i> New promotion</button></div>
      ${promos.length ? Dash.table(["Promotion", "Applies to", "Discount", "Runs", "Status", ""], promos.map((p) => { const live = p.is_active && new Date(p.starts_at) <= now && new Date(p.ends_at) >= now;
        return `<tr><td><strong>${App.esc(p.title)}</strong></td><td>${p.menu_item_id ? App.esc((el._menu.find((m) => m.id === p.menu_item_id) || {}).name || "Item") : "Whole menu"}</td><td>${p.discount_percent}%</td>
        <td class="small">${App.fmtDate(p.starts_at)}<br>to ${App.fmtDate(p.ends_at)}</td><td><span class="status-pill ${live ? "" : "warn"}">${live ? "Live" : new Date(p.ends_at) < now ? "Ended" : "Scheduled"}</span></td>
        <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-dark" data-pe="${p.id}">Edit</button> <button class="btn btn-sm btn-outline-danger" data-pd="${p.id}">Delete</button></td></tr>`; })) : App.empty("bi-megaphone", "No promotions yet", "Discount a dish or your whole menu for a limited time.")}
      <div class="d-flex justify-content-between align-items-center mt-4 mb-2"><h2 class="h6 mb-0">Coupon codes</h2><button class="btn btn-dark btn-sm" id="add-coupon"><i class="bi bi-plus-lg"></i> New coupon</button></div>
      ${coupons.length ? Dash.table(["Code", "Offer", "Rules", "Used", "Status", ""], coupons.map((c) => `<tr><td><strong>${App.esc(c.code)}</strong></td>
        <td>${c.type === "PERCENT" ? c.value + "% off" : c.type === "FIXED" ? App.money(c.value) + " off" : "Free delivery"}${c.max_discount ? `<div class="meta">max ${App.money(c.max_discount)}</div>` : ""}</td>
        <td class="small">${c.min_order ? "Min " + App.money(c.min_order) + "<br>" : ""}${c.first_order_only ? "First orders only<br>" : ""}${c.expires_at ? "Expires " + App.fmtDate(c.expires_at) : "No expiry"}</td>
        <td>${c.times_used}${c.usage_limit ? " / " + c.usage_limit : ""}</td><td><span class="status-pill ${c.is_active ? "" : "bad"}">${c.is_active ? "Active" : "Off"}</span></td>
        <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-dark" data-ce="${c.id}">Edit</button> ${c.is_active ? `<button class="btn btn-sm btn-outline-danger" data-cd="${c.id}">Turn off</button>` : ""}</td></tr>`)) : App.empty("bi-ticket-perforated", "No coupons yet", "Create a code customers can enter at checkout.")}`;
  }
  const promoFields = (p = {}) => [
    { name: "title", label: "Title", value: p.title, required: true, maxlength: 100 },
    { name: "discount_percent", label: "Discount (%)", type: "number", min: 1, value: p.discount_percent, required: true },
    { name: "menu_item_id", label: "Applies to", type: "select", value: p.menu_item_id ?? "", options: [{ value: "", label: "Whole menu" }, ...el._menu.map((m) => ({ value: m.id, label: m.name }))] },
    { name: "starts_at", label: "Starts", type: "datetime-local", value: local(p.starts_at) || local(new Date().toISOString()), required: true },
    { name: "ends_at", label: "Ends", type: "datetime-local", value: local(p.ends_at), required: true },
  ];
  const promoBody = (f) => ({ title: f.title, discount_percent: Number(f.discount_percent), menu_item_id: f.menu_item_id ? Number(f.menu_item_id) : null, starts_at: iso(f.starts_at), ends_at: iso(f.ends_at) });
  const couponFields = (c = {}) => [
    { name: "code", label: "Code (letters/digits)", value: c.code, required: true, maxlength: 30 },
    { name: "type", label: "Type", type: "select", value: c.type || "PERCENT", options: [{ value: "PERCENT", label: "Percentage off" }, { value: "FIXED", label: "Fixed amount off (GH₵)" }, { value: "FREE_DELIVERY", label: "Free delivery" }] },
    { name: "value", label: "Value (% or GH₵; ignored for free delivery)", type: "number", step: "0.01", min: 0, value: c.value ?? 10 },
    { name: "min_order", label: "Minimum order (GH₵)", type: "number", step: "0.01", min: 0, value: c.min_order ?? 0 },
    { name: "max_discount", label: "Maximum discount (GH₵, optional)", type: "number", step: "0.01", min: 0, value: c.max_discount ?? "" },
    { name: "expires_at", label: "Expires (optional)", type: "datetime-local", value: local(c.expires_at) },
    { name: "usage_limit", label: "Total uses allowed (optional)", type: "number", min: 1, value: c.usage_limit ?? "" },
    { name: "per_user_limit", label: "Uses per customer", type: "number", min: 1, value: c.per_user_limit ?? 1 },
    { name: "first_order_only", label: "First-time customers only", type: "checkbox", value: c.first_order_only },
  ];
  const couponBody = (f) => ({ code: f.code, type: f.type, value: String(f.value || 0), min_order: String(f.min_order || 0), max_discount: f.max_discount ? String(f.max_discount) : null,
    expires_at: iso(f.expires_at), usage_limit: f.usage_limit ? Number(f.usage_limit) : null, per_user_limit: Number(f.per_user_limit) || 1, first_order_only: f.first_order_only });
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const save = async (path, method, body) => { if (await App.call(path, { method, body })) { UI.toast("Saved."); load(); } };
    if (t.id === "add-promo") { const f = await Dash.formModal({ title: "New promotion", fields: promoFields() }); if (f) save("/vendor/promotions", "POST", promoBody(f)); }
    if (t.dataset.pe) { const p = el._promos.find((x) => x.id === Number(t.dataset.pe)); const f = await Dash.formModal({ title: "Edit promotion", fields: promoFields(p) }); if (f) save(`/vendor/promotions/${p.id}`, "PUT", promoBody(f)); }
    if (t.dataset.pd && confirm("Delete this promotion?")) { if (await App.call(`/vendor/promotions/${t.dataset.pd}`, { method: "DELETE" })) load(); }
    if (t.id === "add-coupon") { const f = await Dash.formModal({ title: "New coupon", fields: couponFields() }); if (f) save("/vendor/coupons", "POST", couponBody(f)); }
    if (t.dataset.ce) { const c = el._coupons.find((x) => x.id === Number(t.dataset.ce)); const f = await Dash.formModal({ title: "Edit coupon", fields: couponFields(c) }); if (f) save(`/vendor/coupons/${c.id}`, "PUT", couponBody(f)); }
    if (t.dataset.cd && confirm("Turn this coupon off?")) { if (await App.call(`/vendor/coupons/${t.dataset.cd}`, { method: "DELETE" })) load(); }
  });
  load();
} });
