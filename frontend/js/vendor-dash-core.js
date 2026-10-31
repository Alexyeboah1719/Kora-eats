/* Vendor dashboard: overview, menu, profile, hours. (Orders/promotions/reviews/analytics register from their own files.) */
(() => {
  const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  let cats = [], TX = null;

  Dash.register("vendor", { id: "overview", label: "Overview", icon: "bi-speedometer2", async render(el) {
    const v = await App.call("/vendor/me"); if (!v) return;
    el.innerHTML = `<div class="panel mb-3 d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div><strong>${App.esc(v.name)}</strong><div class="meta">${v.is_open ? "Accepting orders" : "Not accepting orders"}</div></div>
        <div class="d-flex gap-2">
          <button class="btn ${v.cod_enabled ? "btn-success" : "btn-outline-secondary"}" id="toggle-cod" title="Cash on Delivery">${v.cod_enabled ? "COD On" : "COD Off"}</button>
          <button class="btn ${v.is_open ? "btn-outline-danger" : "btn-success"}" id="toggle-open">${v.is_open ? "Pause orders" : "Start taking orders"}</button>
        </div>
      </div>
      <div id="overview-stats"></div><div id="overview-more"></div>`;
    document.getElementById("toggle-open").addEventListener("click", async () => { if (await App.call("/vendor/open", { method: "POST", params: { is_open: !v.is_open } })) location.reload(); });
    document.getElementById("toggle-cod").addEventListener("click", async () => { if (await App.call("/vendor/cod", { method: "POST", params: { cod_enabled: !v.cod_enabled } })) location.reload(); });
    window.dispatchEvent(new CustomEvent("vendor:overview", { detail: { el } }));   // later phases add stats here
  } });

  Dash.register("vendor", { id: "menu", label: "Menu", icon: "bi-egg-fried", async render(el) {
    cats = cats.length ? cats : ((await App.call("/categories")) || []);
    TX = TX || (await App.call("/taxonomy"));
    const opt = (list) => list.map((o) => ({ value: o.id, label: o.label }));
    const catOpts = [{ value: "", label: "No category" }, ...cats.map((c) => ({ value: c.id, label: c.name }))];
    const fields = (it = {}) => [
      { name: "name", label: "Name", value: it.name, required: true, maxlength: 120 },
      { name: "description", label: "Description", type: "textarea", value: it.description },
      { name: "price", label: "Price (GH₵)", type: "number", step: "0.01", min: 0.5, value: it.price, required: true },
      { name: "category_id", label: "Category", type: "select", options: catOpts, value: it.category_id ?? "" },
      { name: "prep_minutes", label: "Preparation time (minutes)", type: "number", min: 1, value: it.prep_minutes ?? 15 },
      { name: "traits", label: "What is it like?", type: "checks", options: opt(TX.traits), value: it.traits, hint: "Only tick what is true. KORA EATS shows these to customers." },
      { name: "meal_types", label: "Best for", type: "checks", options: opt(TX.meal_types), value: it.meal_types },
      { name: "spice_level", label: "Spice level", type: "select", options: TX.spice_levels.map((s) => ({ value: s.id, label: s.label })), value: it.spice_level ?? 0 },
      { name: "dietary", label: "Dietary labels", type: "checks", options: opt(TX.dietary), value: it.dietary, hint: "Only tick labels you can guarantee (e.g. Halal)." },
      { name: "allergens", label: "Contains these allergens", type: "checks", options: opt(TX.allergens), value: it.allergens },
      { name: "dietary_declared", label: "I confirm the allergen list above is complete", type: "checkbox", value: it.dietary_declared, hint: "Customers who avoid an allergen only see dishes where this is confirmed." },
      { name: "tags", label: "Extra keywords (optional)", value: it.tags, hint: "Comma separated, e.g. chicken,rice. Helps search." },
      { name: "is_available", label: "Available now", type: "checkbox", value: it.is_available ?? true },
    ];
    const body = (f) => ({ ...f, price: String(f.price), category_id: f.category_id ? Number(f.category_id) : null, prep_minutes: Number(f.prep_minutes) || 15, spice_level: Number(f.spice_level) || 0, is_spicy: Number(f.spice_level) > 0 });
    async function load() {
      const items = await App.call("/vendor/menu"); if (!items) return;
      el.innerHTML = `<div class="d-flex justify-content-between mb-3"><span class="meta">${items.length} item(s)</span><button class="btn btn-dark" id="add-item"><i class="bi bi-plus-lg"></i> Add food</button></div>` +
        (items.length ? Dash.table(["", "Name", "Price", "Prep", "Available", ""], items.map((i) => `<tr>
          <td style="width:56px">${i.image_url ? `<img src="${App.esc(i.image_url)}" alt="" width="44" height="44" style="object-fit:cover;border-radius:8px">` : '<i class="bi bi-image text-muted fs-4"></i>'}</td>
          <td><strong>${App.esc(i.name)}</strong><div class="meta">${[...i.dietary, ...i.traits].map((x) => x.replace(/_/g, " ")).join(", ") || App.esc(i.tags)}${i.allergens.length && !i.dietary_declared ? ' <span class="text-warning">(allergens not confirmed)</span>' : ""}</div></td><td>${App.money(i.price)}</td><td>${i.prep_minutes} min</td>
          <td><div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" data-avail="${i.id}" ${i.is_available ? "checked" : ""} aria-label="Available"></div></td>
          <td class="text-end text-nowrap"><label class="btn btn-sm btn-outline-dark mb-0">Photo<input type="file" hidden accept="image/jpeg,image/png,image/webp" capture="environment" data-img="${i.id}"></label>
            <button class="btn btn-sm btn-outline-dark" data-edit="${i.id}">Edit</button> <button class="btn btn-sm btn-outline-danger" data-del="${i.id}">Delete</button></td></tr>`)) : App.empty("bi-egg-fried", "Your menu is empty", "Add your first dish so customers can order."));
      el._items = items;
    }
    el.addEventListener("click", async (e) => {
      const t = e.target.closest("button"); if (!t) return;
      if (t.id === "add-item") { const f = await Dash.formModal({ title: "Add food", fields: fields() }); if (f && (await App.call("/vendor/menu", { method: "POST", body: body(f) }))) { UI.toast("Food added."); load(); } }
      if (t.dataset.edit) { const it = el._items.find((x) => x.id === Number(t.dataset.edit)); const f = await Dash.formModal({ title: "Edit food", fields: fields(it) }); if (f && (await App.call(`/vendor/menu/${it.id}`, { method: "PUT", body: body(f) }))) { UI.toast("Saved."); load(); } }
      if (t.dataset.del && (await Dash.confirmBox("Delete this item?"))) { if (await App.call(`/vendor/menu/${t.dataset.del}`, { method: "DELETE" })) { UI.toast("Deleted."); load(); } }
    });
    el.addEventListener("change", async (e) => {
      const a = e.target.closest("[data-avail]"), im = e.target.closest("[data-img]");
      if (a) { if (!(await App.call(`/vendor/menu/${a.dataset.avail}/availability`, { method: "PATCH", params: { is_available: a.checked } }))) a.checked = !a.checked; else UI.toast(a.checked ? "Marked available." : "Marked unavailable."); }
      if (im && im.files[0]) { const fd = new FormData(); fd.append("file", await Camera.compress(im.files[0])); if (await App.call(`/vendor/menu/${im.dataset.img}/image`, { method: "POST", form: fd })) { UI.toast("Photo updated."); load(); } }
    });
    load();
  } });

  Dash.register("vendor", { id: "profile", label: "Restaurant profile", icon: "bi-shop", async render(el) {
    const [v, cs, tx] = await Promise.all([App.call("/vendor/me"), App.call("/categories"), App.call("/taxonomy")]); if (!v || !tx) return;
    const typeLabel = (tx.business_types.find((t) => t.id === v.business_type) || {}).label || v.business_type;
    el.innerHTML = `<form class="panel" id="pf" novalidate><div class="row g-3">
      <div class="col-12"><div class="panel d-flex gap-3 align-items-center flex-wrap"><img src="/api/vendors/${v.id}/qr.svg" width="110" height="110" alt="Your menu QR code" onerror="this.parentElement.remove()">
        <div><strong>Your menu QR code</strong><div class="meta mb-2">Print it for your counter or window. Customers scan it to open your menu.</div>
        <button type="button" class="btn btn-sm btn-outline-dark" id="vqr-share"><i class="bi bi-printer"></i> Print or share</button></div></div></div>
      <div class="col-12"><span class="status-pill">${App.esc(typeLabel)}</span> <span class="meta">To change your seller type, contact support.</span></div>
      <div class="col-md-6"><label class="form-label" for="p-name">Business name</label><input class="form-control" id="p-name" value="${App.esc(v.name)}" required></div>
      <div class="col-md-6"><label class="form-label" for="p-owner">Owner name</label><input class="form-control" id="p-owner" value="${App.esc(v.owner_name || "")}" required></div>
      <div class="col-md-6"><label class="form-label" for="p-email">Business email</label><input class="form-control" id="p-email" type="email" value="${App.esc(v.contact_email || "")}"></div>
      <div class="col-md-6"><label class="form-label" for="p-phone">Phone</label><input class="form-control" id="p-phone" value="${App.esc(v.phone || "")}" required></div>
      <div class="col-md-6"><label class="form-label" for="p-cat">Category</label><select class="form-select" id="p-cat"><option value="">None</option>${(cs || []).map((c) => `<option value="${c.id}" ${v.category_id === c.id ? "selected" : ""}>${App.esc(c.name)}</option>`).join("")}</select></div>
      <div class="col-md-6"><label class="form-label" for="p-prep">Average prep time (min)</label><input class="form-control" id="p-prep" type="number" min="1" max="240" value="${v.avg_prep_minutes}"></div>
      <div class="col-md-6"><label class="form-label" for="p-radius">Delivery zone (km)</label><input class="form-control" id="p-radius" type="number" min="1" max="50" step="0.5" value="${v.delivery_radius_km ?? ""}" placeholder="Platform default"><div class="hint">Customers farther than this can't order from you.</div></div>
      <div class="col-12"><label class="form-label" for="p-desc">Description</label><textarea class="form-control" id="p-desc" rows="2">${App.esc(v.description)}</textarea></div>
      <div class="col-md-4"><label class="form-label" for="p-region">Region</label><select class="form-select" id="p-region">${tx.regions.map((r) => `<option ${v.region === r ? "selected" : ""}>${App.esc(r)}</option>`).join("")}</select></div>
      <div class="col-md-4"><label class="form-label" for="p-area">Area / neighbourhood</label><input class="form-control" id="p-area" value="${App.esc(v.area || "")}" maxlength="80"></div>
      <div class="col-md-4"><label class="form-label" for="p-digital">Ghana Post GPS</label><input class="form-control" id="p-digital" value="${App.esc(v.digital_address || "")}" placeholder="GA-543-4531"></div>
      <div class="col-12"><label class="form-label" for="p-addr">Address</label><input class="form-control" id="p-addr" value="${App.esc(v.address_text)}" required></div>
      <div class="col-12"><div id="map-slot"></div><span class="meta" id="p-coords">Location: ${v.latitude.toFixed(5)}, ${v.longitude.toFixed(5)}</span></div>
      <div class="col-md-6"><label class="form-label">Logo</label><div class="d-flex gap-2 align-items-center">${v.logo_url ? `<img src="${App.esc(v.logo_url)}" alt="" width="48" height="48" style="border-radius:10px;object-fit:cover">` : ""}<input class="form-control" type="file" id="p-logo" accept="image/jpeg,image/png,image/webp"></div></div>
      <div class="col-md-6"><label class="form-label">Cover image</label><div class="d-flex gap-2 align-items-center">${v.cover_url ? `<img src="${App.esc(v.cover_url)}" alt="" width="80" height="48" style="border-radius:10px;object-fit:cover">` : ""}<input class="form-control" type="file" id="p-cover" accept="image/jpeg,image/png,image/webp"></div></div>
      <div class="col-12"><div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark">Save profile</button></div></div></form>`;
    let c = { lat: v.latitude, lng: v.longitude };
    if ((typeof MapKit !== "undefined" ? MapKit : undefined)) MapKit.picker(document.getElementById("map-slot"), (lat, lng) => { c = { lat, lng }; document.getElementById("p-coords").textContent = `Location: ${lat.toFixed(5)}, ${lng.toFixed(5)}`; }, c);
    el.querySelector("#pf").addEventListener("submit", async (e) => {
      e.preventDefault(); const a = el.querySelector("#form-alert"); a.classList.add("d-none");
      const r = await App.api("/vendor/me", { method: "PUT", body: { business_type: v.business_type, region: $("p-region") || null, area: $("p-area") || null, digital_address: $("p-digital") || null, name: $("p-name"), owner_name: $("p-owner"), contact_email: $("p-email") || null, phone: $("p-phone"), address_text: $("p-addr"), latitude: c.lat, longitude: c.lng, description: $("p-desc"), category_id: $("p-cat") ? Number($("p-cat")) : null, avg_prep_minutes: Number($("p-prep")) || 20, delivery_radius_km: $("p-radius") ? Number($("p-radius")) : null } });
      if (!r.ok) { a.textContent = App.errorMessage(r); return a.classList.remove("d-none"); }
      for (const k of ["logo", "cover"]) { const f = document.getElementById("p-" + k).files[0]; if (f) { const fd = new FormData(); fd.append("file", f); const u = await App.api(`/vendor/me/${k}`, { method: "POST", form: fd }); if (!u.ok) { a.textContent = App.errorMessage(u); return a.classList.remove("d-none"); } } }
      UI.toast("Profile saved.");
    });
    el.addEventListener("click", (e) => { if (e.target.closest("#vqr-share")) Share.open({ title: v.name, path: v.share_path || ("/v/" + v.id), qr: `/api/vendors/${v.id}/qr.svg`, text: `${v.name} on KORA EATS` }); });
    const $ = (id) => document.getElementById(id).value.trim();
  } });

  Dash.register("vendor", { id: "hours", label: "Opening hours", icon: "bi-clock", async render(el) {
    const hours = await App.call("/vendor/hours"); if (!hours) return;
    const by = Object.fromEntries(hours.map((h) => [h.weekday, h]));
    el.innerHTML = `<form class="panel" id="hf"><p class="meta">Customers can only order while you're open. If closing is earlier than opening, you close after midnight.</p>
      ${DAYS.map((d, i) => { const h = by[i] || { opens_at: "08:00", closes_at: "20:00", is_closed: true };
        return `<div class="row g-2 align-items-center mb-2"><div class="col-12 col-md-3 fw-semibold">${d}</div>
          <div class="col-4 col-md-2"><input class="form-control" type="time" data-o="${i}" value="${h.opens_at.slice(0, 5)}" aria-label="${d} opens"></div>
          <div class="col-4 col-md-2"><input class="form-control" type="time" data-c="${i}" value="${h.closes_at.slice(0, 5)}" aria-label="${d} closes"></div>
          <div class="col-4 col-md-3"><div class="form-check"><input class="form-check-input" type="checkbox" data-x="${i}" id="x${i}" ${h.is_closed ? "checked" : ""}><label class="form-check-label" for="x${i}">Closed all day</label></div></div></div>`; }).join("")}
      <button class="btn btn-dark mt-2">Save hours</button></form>`;
    el.querySelector("#hf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = DAYS.map((_, i) => ({ weekday: i, opens_at: el.querySelector(`[data-o="${i}"]`).value || "08:00", closes_at: el.querySelector(`[data-c="${i}"]`).value || "20:00", is_closed: el.querySelector(`[data-x="${i}"]`).checked }));
      if (await App.call("/vendor/hours", { method: "PUT", body })) UI.toast("Opening hours saved.");
    });
  } });
})();
