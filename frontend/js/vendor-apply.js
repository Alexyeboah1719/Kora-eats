(async () => {
  const user = await App.mountShell({ active: "profile", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  if (user.role === "VENDOR") return location.replace("/vendor/dashboard.html");
  if (user.role !== "CUSTOMER") { root.innerHTML = App.empty("bi-lock", "Vendor applications are for customer accounts", ""); return; }

  const [cats, tx, req] = await Promise.all([App.call("/categories"), App.call("/taxonomy"), App.call("/vendor-requirements")]);
  if (!tx || !req) return;
  const DOC = req.document_labels, TYPES = Object.fromEntries(req.types.map((t) => [t.id, t]));
  const r = await App.api("/vendor/application");
  const app = r.ok ? r.data : null;
  const locked = app && app.approval_status === "PENDING";

  const statusCard = () => !app ? "" : app.approval_status === "PENDING"
    ? `<div class="panel mb-3" style="border-color:var(--gold)"><span class="status-pill warn">Under review</span><h2 class="h6 mt-2">Application received</h2><p class="meta mb-0">Our team is reviewing <strong>${App.esc(app.name)}</strong> (${App.esc((TYPES[app.business_type] || {}).label || "")}). We'll email you. Documents uploaded: ${app.documents.length}.</p></div>`
    : app.approval_status === "REJECTED" ? `<div class="panel mb-3" style="border-color:var(--bad)"><span class="status-pill bad">Not approved</span><p class="mt-2 mb-0"><strong>Reason:</strong> ${App.esc(app.rejection_reason)}</p><p class="meta mb-0">Fix the issue below and submit again.</p></div>` : "";

  const opts = (list, sel) => list.map((o) => `<option value="${App.esc(o.id ?? o)}" ${(o.id ?? o) === sel ? "selected" : ""}>${App.esc(o.label ?? o)}</option>`).join("");
  root.innerHTML = `<h1 class="h4">Sell on KORA EATS</h1><p class="meta">Restaurants, chop bars, home cooks, bakers, street-food sellers, caterers and small food businesses are all welcome. Tell us about yours; our team verifies every seller before they go live.</p>${statusCard()}
    ${locked ? "" : `<form class="panel" id="vform" novalidate><div class="row g-3">
      <div class="col-12"><label class="form-label" for="btype">What kind of food business is it?</label><select class="form-select" id="btype">${opts(tx.business_types, app?.business_type || "HOME_COOK")}</select>
        <div class="meta mt-1" id="btype-blurb"></div></div>
      <div class="col-md-6"><label class="form-label" for="name">Business / kitchen name</label><input class="form-control" id="name" required maxlength="120" value="${App.esc(app?.name || "")}"></div>
      <div class="col-md-6"><label class="form-label" for="owner">Owner name</label><input class="form-control" id="owner" required value="${App.esc(app?.owner_name || user.full_name)}"></div>
      <div class="col-md-6"><label class="form-label" for="email">Contact email</label><input class="form-control" id="email" type="email" value="${App.esc(app?.contact_email || user.email)}"></div>
      <div class="col-md-6"><label class="form-label" for="phone">Phone (Ghana)</label><input class="form-control" id="phone" inputmode="tel" required placeholder="024 123 4567" value="${App.esc(app?.phone || "")}"></div>
      <div class="col-md-6"><label class="form-label" for="category">Main food category</label><select class="form-select" id="category"><option value="">Choose...</option>${(cats || []).map((c) => `<option value="${c.id}" ${app?.category_id === c.id ? "selected" : ""}>${App.esc(c.name)}</option>`).join("")}</select></div>
      <div class="col-md-6"><label class="form-label" for="prep">Typical preparation time (minutes)</label><input class="form-control" id="prep" type="number" min="1" max="240" value="${app?.avg_prep_minutes || 20}"></div>
      <div class="col-12"><label class="form-label" for="desc">Tell customers about your food</label><textarea class="form-control" id="desc" rows="2" maxlength="1000">${App.esc(app?.description || "")}</textarea></div>
      <div class="col-md-4"><label class="form-label" for="region">Region</label><select class="form-select" id="region">${opts(tx.regions, app?.region || "Greater Accra")}</select></div>
      <div class="col-md-4"><label class="form-label" for="area">Area / neighbourhood</label><input class="form-control" id="area" maxlength="80" placeholder="e.g. East Legon" value="${App.esc(app?.area || "")}"></div>
      <div class="col-md-4"><label class="form-label" for="digital">Ghana Post GPS (optional)</label><input class="form-control" id="digital" placeholder="GA-543-4531" value="${App.esc(app?.digital_address || "")}"></div>
      <div class="col-12"><label class="form-label" for="addr">Street address / landmark</label><input class="form-control" id="addr" required value="${App.esc(app?.address_text || "")}"></div>
      <div class="col-12"><div id="map-slot"></div><button class="btn btn-outline-dark btn-sm" type="button" id="gps"><i class="bi bi-crosshair"></i> Use my current location</button> <span class="meta" id="coords">${app ? "Location saved." : "Pin where customers collect or where you cook from."}</span></div>
      <div class="col-6 col-md-3"><label class="form-label" for="opens">Opens</label><input class="form-control" id="opens" type="time" value="08:00"></div>
      <div class="col-6 col-md-3"><label class="form-label" for="closes">Closes</label><input class="form-control" id="closes" type="time" value="20:00"></div>
      <div class="col-md-6"><label class="form-label" for="logo">Logo or profile picture</label><input class="form-control" id="logo" type="file" accept="image/jpeg,image/png,image/webp"></div>
      <div class="col-md-6"><label class="form-label" for="cover">Cover photo</label><input class="form-control" id="cover" type="file" accept="image/jpeg,image/png,image/webp" capture="environment"></div>
      <div class="col-12"><h2 class="h6 mb-1">Documents for verification</h2><div class="meta mb-2" id="doc-help"></div><div id="doc-rows"></div>
        <button type="button" class="btn btn-sm btn-outline-dark" id="add-doc">+ Add another document</button></div>
      <div class="col-12"><div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark" id="submit" type="submit">Submit application</button></div></div></form>`}`;
  const docsPanel = document.createElement("div");
  if (locked) { root.append(docsPanel); }
  let coords = app ? { lat: app.latitude, lng: app.longitude } : null;
  if (locked) return;

  // ---- document checklist depends on the business type
  const rows = document.getElementById("doc-rows");
  const docRow = (types, fixed) => { const d = document.createElement("div"); d.className = "row g-2 mb-2 doc-row";
    d.innerHTML = `<div class="col-5">${fixed ? `<div class="form-control-plaintext small">${App.esc(DOC[types[0]])}</div><input type="hidden" value="${types[0]}">` : `<select class="form-select form-select-sm">${types.map((t) => `<option value="${t}">${App.esc(DOC[t])}</option>`).join("")}</select>`}</div>
      <div class="col-7"><input class="form-control form-control-sm" type="file" accept="application/pdf,image/jpeg,image/png,image/webp" ${fixed ? "required" : ""} capture="environment"></div>`; return d; };
  function renderDocs() {
    const t = TYPES[document.getElementById("btype").value];
    document.getElementById("btype-blurb").textContent = t.blurb;
    rows.innerHTML = "";
    t.required.forEach((k) => rows.append(docRow([k], true)));
    t.any_of.forEach((g) => rows.append(docRow(g, false)));
    document.getElementById("doc-help").innerHTML = `Required: ${[...t.required.map((k) => DOC[k]), ...t.any_of.map((g) => g.map((k) => DOC[k]).join(" or "))].map(App.esc).join("; ")}. Photos or PDFs, up to 3 MB each. On a phone you can take the photo with your camera.`;
  }
  document.getElementById("btype").addEventListener("change", renderDocs);
  document.getElementById("add-doc").addEventListener("click", () => rows.append(docRow(["FOOD_HANDLER_CERT", "OTHER", "BUSINESS_REGISTRATION", "ID_CARD"], false)));
  renderDocs();

  const setC = (lat, lng) => { coords = { lat, lng }; document.getElementById("coords").textContent = `Pinned at ${lat.toFixed(5)}, ${lng.toFixed(5)}`; };
  const picker = (typeof MapKit !== "undefined" ? MapKit : undefined) ? MapKit.picker(document.getElementById("map-slot"), setC, coords) : null;
  document.getElementById("gps").addEventListener("click", async () => { try { const l = await App.detectLocation(); setC(l.lat, l.lng); picker && picker.setView(l.lat, l.lng); } catch (e) { UI.toast(e.message, "error"); } });

  document.getElementById("vform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const alertEl = document.getElementById("form-alert"), btn = document.getElementById("submit"); alertEl.classList.add("d-none");
    const fail = (m) => { alertEl.textContent = m; alertEl.classList.remove("d-none"); btn.disabled = false; };
    if (!coords) return fail("Pin your location on the map first.");
    const t = TYPES[document.getElementById("btype").value];
    const files = [...rows.querySelectorAll(".doc-row")].map((d) => ({ type: (d.querySelector("select") || d.querySelector("input[type=hidden]")).value, file: d.querySelector("input[type=file]").files[0] })).filter((x) => x.file);
    const have = new Set(files.map((x) => x.type));
    const missing = [...t.required.filter((k) => !have.has(k)).map((k) => DOC[k]), ...t.any_of.filter((g) => !g.some((k) => have.has(k))).map((g) => g.map((k) => DOC[k]).join(" or "))];
    if (missing.length) return fail("Please upload: " + missing.join("; ") + ".");
    btn.disabled = true;
    const val = (id) => document.getElementById(id).value.trim();
    const res = await App.api("/vendor/apply", { method: "POST", body: { business_type: val("btype"), name: val("name"), owner_name: val("owner"), contact_email: val("email") || null, phone: val("phone"), address_text: val("addr"),
      region: val("region") || null, area: val("area") || null, digital_address: val("digital") || null, latitude: coords.lat, longitude: coords.lng, description: val("desc"),
      category_id: val("category") ? Number(val("category")) : null, avg_prep_minutes: Number(val("prep")) || 20, opens_at: val("opens") || "08:00", closes_at: val("closes") || "20:00" } });
    if (!res.ok) return fail(App.errorMessage(res));
    for (const k of ["logo", "cover"]) { const f = document.getElementById(k).files[0]; if (f) { const fd = new FormData(); fd.append("file", await Camera.compress(f)); const u = await App.api(`/vendor/me/${k}`, { method: "POST", form: fd }); if (!u.ok) return fail(`${k}: ${App.errorMessage(u)}`); } }
    for (const x of files) { const fd = new FormData(); fd.append("doc_type", x.type); fd.append("file", await Camera.compress(x.file)); const u = await App.api("/vendor/documents", { method: "POST", form: fd }); if (!u.ok) return fail(`${x.file.name}: ${App.errorMessage(u)}`); }
    UI.toast("Application submitted. We'll email you once it's reviewed."); setTimeout(() => location.reload(), 900);
  });
})();
