/* Admin: smart dispatch board. Orders waiting for a rider, the AI's recommended riders, and manual assignment. */
Dash.register("admin", { id: "dispatch", label: "Dispatch", icon: "bi-bicycle", async render(el) {
  let timer = null;
  async function load() {
    const d = await App.call("/admin/dispatch"); if (!d) return;
    el.innerHTML = `<p class="meta">Orders that are ready and waiting for a rider. ${d.online_riders.length} rider(s) online. Estimates use straight-line distance and an average speed.</p>` +
      (d.orders.length ? d.orders.map((x) => { const o = x.order, p = x.priority;
        return `<div class="panel mb-3" style="${p.urgent ? "border-color:var(--bad)" : ""}"><div class="d-flex justify-content-between flex-wrap gap-2"><div><strong>${App.esc(o.code)}</strong> <span class="meta">${App.esc(o.vendor)} &rarr; ${App.esc(o.address)}</span>
            <div class="meta">${o.trip_km} km trip &middot; rider earns ${App.money(o.earning)}</div></div>
            <div>${p.urgent ? `<span class="status-pill bad">${App.esc(p.reason || "Urgent")}</span>` : `<span class="status-pill">Waiting ${p.waiting_minutes} min</span>`}</div></div>
          <div class="mt-2 small fw-semibold">Recommended riders</div>
          ${x.recommended.length ? x.recommended.map((r, i) => `<div class="d-flex justify-content-between align-items-start gap-2 py-1 border-top"><div><strong>${i + 1}. ${App.esc(r.name)}</strong> <span class="chip-mini">${App.esc(r.vehicle.toLowerCase())}</span> <span class="status-pill">${r.score}% fit</span>
              <div class="small">Pickup ${r.pickup_km} km away, about ${Math.max(1, r.pickup_eta_minutes)} min &middot; at customer in about ${r.delivery_eta_minutes} min &middot; ${r.active_deliveries} active</div>
              <div class="small text-muted">${r.reasons.map(App.esc).join(" &middot; ")}</div></div>
              <button class="btn btn-sm btn-dark" data-assign="${o.id}" data-rider="${r.rider_id}">Assign</button></div>`).join("")
            : '<div class="small text-warning">No rider has a recent location, so no recommendation yet. You can still assign someone manually.</div>'}
          <div class="d-flex gap-2 mt-2"><select class="form-select form-select-sm w-auto" data-pick="${o.id}" aria-label="Choose any online rider"><option value="">Assign someone else...</option>${d.online_riders.map((r) => `<option value="${r.id}">${App.esc(r.name)}${r.has_location ? "" : " (no location)"}</option>`).join("")}</select>
            <button class="btn btn-sm btn-outline-dark" data-assign-pick="${o.id}">Assign</button></div></div>`; }).join("")
        : App.empty("bi-bicycle", "Nothing waiting for a rider", "Ready orders appear here automatically."));
  }
  async function assign(orderId, riderId, force) {
    const r = await App.api(`/admin/dispatch/${orderId}/assign`, { method: "POST", body: { rider_id: Number(riderId), force } });
    if (r.ok) { UI.toast(`Assigned to ${r.data.rider}.`); return load(); }
    if (r.data?.detail?.code === "rider_busy" && confirm(r.data.detail.message + "\n\nAssign anyway?")) return assign(orderId, riderId, true);
    UI.toast(App.errorMessage(r), "error"); load();
  }
  el.addEventListener("click", (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.assign) assign(t.dataset.assign, t.dataset.rider, false);
    if (t.dataset.assignPick) { const v = el.querySelector(`[data-pick="${t.dataset.assignPick}"]`).value; if (v) assign(t.dataset.assignPick, v, false); else UI.toast("Choose a rider first.", "error"); }
  });
  await load();
  timer = setInterval(load, 15000);
  this.teardown = () => clearInterval(timer);
} });
