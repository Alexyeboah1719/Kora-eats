(async () => {
  const user = await App.mountShell({ active: "search" });
  const root = document.getElementById("page");
  const id = App.qs("id");
  root.innerHTML = `<div class="skeleton-card" style="height:240px"></div>`;
  const v = await App.call(`/vendors/${encodeURIComponent(id)}`, { params: App.locParams() });
  if (!v) { root.innerHTML = App.empty("bi-shop", "Vendor not found", "It may have been removed."); return; }
  document.title = v.name + " | KORA EATS";
  root.innerHTML = `
    <div class="panel p-0 overflow-hidden mb-3">
      <div class="pos-rel">${App.imageBlock(v.cover_url || v.logo_url)}
        <button class="fav-btn" data-fav-vendor="${v.id}" aria-pressed="${v.is_favorite}" aria-label="Save vendor"><i class="bi ${v.is_favorite ? "bi-heart-fill" : "bi-heart"}"></i></button></div>
      <div class="p-3">
        <div class="d-flex justify-content-between flex-wrap gap-2"><h1 class="h4 mb-0">${App.esc(v.name)} <button class="btn btn-sm btn-outline-dark align-middle" id="share-vendor" aria-label="Share ${App.esc(v.name)}"><i class="bi bi-share"></i></button></h1>
          ${v.is_open ? '<span class="status-pill">Open</span>' : '<span class="status-pill bad">Closed</span>'}</div>
        <p class="meta my-2">${App.esc(v.description)}</p>
        <div class="meta"><span class="rating"><i class="bi bi-star-fill"></i> ${v.rating_count ? v.rating_avg.toFixed(1) + " (" + v.rating_count + ")" : "New"}</span>
          &middot; ${v.avg_prep_minutes} min prep${v.distance_km != null ? " &middot; " + v.distance_km.toFixed(1) + " km away" : ""}${v.delivery_fee != null ? " &middot; " + App.money(v.delivery_fee) + " delivery" : ""}</div>
        <div class="meta mt-1"><i class="bi bi-geo-alt"></i> ${App.esc(v.address_text)}</div>
        <div class="meta mt-1">${v.in_zone === false ? '<span class="text-danger"><i class="bi bi-exclamation-circle"></i> Outside this vendor\'s delivery area (up to ' + v.delivery_radius_km + " km)</span>" : "Delivers within " + v.delivery_radius_km + " km"}</div>
        <div id="vendor-actions" class="mt-2"></div>
      </div></div>
    <div id="menu"></div><div id="reviews"></div>`;
  document.getElementById("menu").innerHTML = v.menu.length
    ? v.menu.map((s) => `<div class="section-title"><span>${App.esc(s.category)}</span></div><div class="card-grid">${s.items.map((i) => App.itemCard(i, { showVendor: false })).join("")}</div>`).join("")
    : App.empty("bi-egg-fried", "No menu yet", "This vendor hasn't added any food.");
  App.wireCards(root);
  if ((typeof MapKit !== "undefined" ? MapKit : undefined) && MapKit.ready()) { const slot = document.createElement("div"); slot.className = "mt-3"; root.append(slot); MapKit.show(slot, [{ lat: v.latitude, lng: v.longitude, label: v.name, kind: "vendor" }]); }
  window.dispatchEvent(new CustomEvent("vendor:loaded", { detail: v }));  // later phases hook in here
  document.addEventListener("click", (e) => { if (e.target.closest("#share-vendor") && (typeof Share !== "undefined" ? Share : undefined)) Share.open({ title: v.name, path: v.share_path, qr: `/api/vendors/${v.id}/qr.svg`, text: `${v.name} on KORA EATS` }); });
})();
