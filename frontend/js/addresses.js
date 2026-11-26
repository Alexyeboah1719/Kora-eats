(async () => {
  if (!(await App.mountShell({ active: "profile", requireLogin: true }))) return;
  const root = document.getElementById("page");
  const tx = (await App.call("/taxonomy")) || { regions: ["Greater Accra"] };
  root.innerHTML = `<h1 class="h4">My addresses</h1>
    <div class="row g-3"><div class="col-lg-6"><div id="list">${App.skeletons(2)}</div></div>
    <div class="col-lg-6"><form class="panel" id="aform" novalidate><h2 class="h6 mb-3" id="form-title">Add an address</h2>
      <div id="map-slot"></div>
      <button class="btn btn-outline-dark btn-sm mb-3" type="button" id="gps"><i class="bi bi-crosshair"></i> Use my current location</button>
      <div class="mb-2"><label class="form-label" for="label">Label</label><input class="form-control" id="label" placeholder="Home, Work..." required maxlength="80"></div>
      <div class="row g-2 mb-2"><div class="col-md-6"><label class="form-label" for="region">Region</label><select class="form-select" id="region">${tx.regions.map((r) => `<option ${r === "Greater Accra" ? "selected" : ""}>${App.esc(r)}</option>`).join("")}</select></div>
        <div class="col-md-6"><label class="form-label" for="area">Area / neighbourhood</label><input class="form-control" id="area" maxlength="80" placeholder="e.g. East Legon"></div></div>
      <div class="mb-2"><label class="form-label" for="digital">Ghana Post GPS (optional)</label><input class="form-control" id="digital" placeholder="GA-543-4531"></div>
      <div class="mb-2"><label class="form-label" for="line">Address</label><input class="form-control" id="line" placeholder="Street, area" required maxlength="255"></div>
      <div class="row g-2"><div class="col"><label class="form-label" for="city">City</label><input class="form-control" id="city" value="Accra"></div>
        <div class="col"><label class="form-label" for="notes">Delivery notes</label><input class="form-control" id="notes" placeholder="Gate colour, landmark" maxlength="255"></div></div>
      <div class="meta my-2" id="coords">No location chosen yet.</div>
      <div class="form-check mb-3"><input class="form-check-input" type="checkbox" id="isdef"><label class="form-check-label" for="isdef">Make this my default address</label></div>
      <div id="form-alert" class="alert alert-danger d-none" role="alert"></div>
      <div class="d-flex gap-2"><button class="btn btn-dark" type="submit" id="save">Save address</button><button class="btn btn-link text-dark d-none" type="button" id="cancel">Cancel</button></div>
    </form></div></div>`;
  let coords = null, editing = null, picker = null;
  const setCoords = (lat, lng) => { coords = { lat, lng }; document.getElementById("coords").textContent = `Pinned at ${lat.toFixed(5)}, ${lng.toFixed(5)}`; };
  if ((typeof MapKit !== "undefined" ? MapKit : undefined)) picker = MapKit.picker(document.getElementById("map-slot"), (lat, lng) => setCoords(lat, lng));
  document.getElementById("gps").addEventListener("click", async () => {
    try { const l = await App.detectLocation(); setCoords(l.lat, l.lng); picker && picker.setView(l.lat, l.lng); } catch (e) { UI.toast(e.message, "error"); }
  });

  async function load() {
    const list = await App.call("/me/addresses");
    document.getElementById("list").innerHTML = list && list.length ? list.map((a) => `
      <div class="panel mb-2"><div class="d-flex justify-content-between"><strong>${App.esc(a.label)}</strong>${a.is_default ? '<span class="status-pill">Default</span>' : ""}</div>
        <div class="meta">${App.esc(a.address_line)}${a.area ? ", " + App.esc(a.area) : ""}, ${App.esc(a.city)}${a.digital_address ? " &middot; " + App.esc(a.digital_address) : ""}</div>
        <div class="mt-2 d-flex gap-2"><button class="btn btn-sm btn-outline-dark" data-edit="${a.id}">Edit</button>
          ${a.is_default ? "" : `<button class="btn btn-sm btn-outline-dark" data-default="${a.id}">Make default</button>`}
          <button class="btn btn-sm btn-outline-danger" data-del="${a.id}">Delete</button></div></div>`).join("")
      : App.empty("bi-geo-alt", "No saved addresses", "Add one so checkout is quick.");
    root._list = list || [];
  }
  const body = (a) => ({ label: a.label, address_line: a.address_line, city: a.city, region: a.region, area: a.area, digital_address: a.digital_address, latitude: a.latitude, longitude: a.longitude, delivery_notes: a.delivery_notes, is_default: a.is_default });
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const find = (k) => root._list.find((a) => a.id === Number(t.dataset[k]));
    if (t.dataset.del && confirm("Delete this address?")) { if (await App.call(`/me/addresses/${t.dataset.del}`, { method: "DELETE" })) { UI.toast("Address deleted."); load(); } }
    if (t.dataset.default) { const a = find("default"); if (await App.call(`/me/addresses/${a.id}`, { method: "PUT", body: { ...body(a), is_default: true } })) load(); }
    if (t.dataset.edit) {
      const a = find("edit"); editing = a.id;
      label.value = a.label; line.value = a.address_line; city.value = a.city; region.value = a.region || "Greater Accra"; area.value = a.area || ""; digital.value = a.digital_address || ""; notes.value = a.delivery_notes || ""; isdef.checked = a.is_default;
      setCoords(a.latitude, a.longitude); picker && picker.setView(a.latitude, a.longitude);
      document.getElementById("form-title").textContent = "Edit address"; document.getElementById("cancel").classList.remove("d-none");
    }
  });
  document.getElementById("cancel").addEventListener("click", () => { editing = null; document.getElementById("aform").reset(); coords = null; document.getElementById("coords").textContent = "No location chosen yet."; document.getElementById("cancel").classList.add("d-none"); document.getElementById("form-title").textContent = "Add an address"; });
  document.getElementById("aform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const alertEl = document.getElementById("form-alert"); alertEl.classList.add("d-none");
    if (!coords) { alertEl.textContent = "Pin your location first: use the map or the location button."; return alertEl.classList.remove("d-none"); }
    const payload = { label: label.value.trim(), address_line: line.value.trim(), city: city.value.trim() || "Accra", region: region.value || null, area: area.value.trim() || null, digital_address: digital.value.trim() || null, latitude: coords.lat, longitude: coords.lng, delivery_notes: notes.value.trim() || null, is_default: isdef.checked };
    const r = await App.api(editing ? `/me/addresses/${editing}` : "/me/addresses", { method: editing ? "PUT" : "POST", body: payload });
    if (!r.ok) { alertEl.textContent = App.errorMessage(r); return alertEl.classList.remove("d-none"); }
    UI.toast("Address saved."); document.getElementById("cancel").click(); load();
  });
  load();
})();
