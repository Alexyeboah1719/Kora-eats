/* Leaflet + OpenStreetMap helpers. Pages that use maps load Leaflet first (see the page <head>). */
const MapKit = (() => {
  const ACCRA = [5.6037, -0.187];
  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  const ready = () => typeof L !== "undefined";

  function base(el, center, zoom) {
    const map = L.map(el, { scrollWheelZoom: false }).setView(center, zoom);
    L.tileLayer(TILES, { maxZoom: 19, attribution: ATTR }).addTo(map);
    return map;
  }
  const pin = (icon, color) => L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 34], popupAnchor: [0, -30],
    html: `<div class="map-pin" style="background:${color}"><i class="bi ${icon}"></i></div>` });
  const ICONS = { vendor: pin("bi-shop", "#0f2e22"), customer: pin("bi-house-door-fill", "#b3261e"), rider: pin("bi-bicycle", "#946b00"), pick: pin("bi-geo-alt-fill", "#1b7a46") };

  /** Tap/drag to choose a point. onPick(lat, lng). initial = {lat, lng}. Returns { setView(lat,lng), map }. */
  function picker(container, onPick, initial) {
    if (!ready()) return null;
    const jump = document.createElement("select");
    jump.className = "form-select form-select-sm mb-2 w-auto"; jump.setAttribute("aria-label", "Jump to a city");
    jump.innerHTML = '<option value="">Jump to a city...</option>'; container.append(jump);
    const box = document.createElement("div");
    box.className = "map-box"; container.append(box);
    const map = base(box, initial ? [initial.lat, initial.lng] : ACCRA, initial ? 16 : 12);
    fetch("/api/taxonomy").then((r) => r.json()).then((t) => { t.cities.forEach((c) => jump.insertAdjacentHTML("beforeend", `<option value="${c.lat},${c.lng}">${c.name}</option>`)); }).catch(() => jump.remove());
    jump.addEventListener("change", () => { if (jump.value) { const [la, ln] = jump.value.split(",").map(Number); map.setView([la, ln], 14); } });
    let marker = null;
    const place = (lat, lng, notify = true) => {
      if (marker) marker.setLatLng([lat, lng]);
      else { marker = L.marker([lat, lng], { icon: ICONS.pick, draggable: true }).addTo(map); marker.on("dragend", () => { const p = marker.getLatLng(); onPick(p.lat, p.lng); }); }
      if (notify) onPick(lat, lng);
    };
    if (initial) place(initial.lat, initial.lng, false);
    map.on("click", (e) => place(e.latlng.lat, e.latlng.lng));
    setTimeout(() => map.invalidateSize(), 200);
    return { map, setView: (lat, lng) => { map.setView([lat, lng], 17); place(lat, lng, false); } };
  }

  /** Live map for one delivery. update({ vendor, customer, rider, trail }) can be called repeatedly. */
  function live(container, { height = "tall" } = {}) {
    if (!ready()) return null;
    const box = document.createElement("div");
    box.className = "map-box " + height; container.append(box);
    const map = base(box, ACCRA, 13);
    const marks = {}; let trail = null, fitted = false;
    const set = (key, pt, icon, label) => {
      if (!pt || pt.lat == null) { if (marks[key]) { map.removeLayer(marks[key]); delete marks[key]; } return; }
      if (marks[key]) marks[key].setLatLng([pt.lat, pt.lng]); else marks[key] = L.marker([pt.lat, pt.lng], { icon }).addTo(map).bindPopup(label);
    };
    setTimeout(() => map.invalidateSize(), 200);
    return { map, update(d) {
      set("vendor", d.vendor, ICONS.vendor, d.vendor ? d.vendor.name : "");
      set("customer", d.customer, ICONS.customer, "Delivery address");
      set("rider", d.rider, ICONS.rider, d.rider ? "Your rider: " + d.rider.name : "");
      if (trail) { map.removeLayer(trail); trail = null; }
      if (d.trail && d.trail.length > 1) trail = L.polyline(d.trail, { color: "#946b00", weight: 4, opacity: 0.7 }).addTo(map);
      const pts = Object.values(marks).map((m) => m.getLatLng());
      if (pts.length && (!fitted || d.refit)) { map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 16 }); fitted = true; }
    } };
  }

  /** Static map with a few labelled points (e.g. the vendor on its page). */
  function show(container, points) {
    if (!ready() || !points.length) return null;
    const box = document.createElement("div");
    box.className = "map-box"; container.append(box);
    const map = base(box, [points[0].lat, points[0].lng], 15);
    points.forEach((p) => L.marker([p.lat, p.lng], { icon: ICONS[p.kind || "vendor"] }).addTo(map).bindPopup(p.label || ""));
    if (points.length > 1) map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lng])), { padding: [40, 40] });
    setTimeout(() => map.invalidateSize(), 200);
    return map;
  }
  return { picker, live, show, ready };
})();
