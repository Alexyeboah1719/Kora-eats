/* Rider dashboard: go online, take deliveries, run them step by step, see earnings. */
(() => {
  const ACTIONS = { RIDER_ASSIGNED: ["pickup", "Picked up the order"], PICKED_UP: ["out-for-delivery", "Start delivery"], OUT_FOR_DELIVERY: ["delivered", "Mark delivered"], RIDER_ARRIVED: ["delivered", "Mark delivered"], ACCEPTED_RETURN: ["returned", "Handed back to vendor"] };
  const nav = (lat, lng) => `https://www.openstreetmap.org/directions?route=%3B${lat}%2C${lng}#map=16/${lat}/${lng}`;
  const smartLine = (d) => {
    const s = d.smart; if (!s) return d.priority && d.priority.urgent ? `<span class="status-pill bad me-1">${App.esc(d.priority.reason || "Urgent")}</span>` : "";
    return `${d.recommended ? '<span class="status-pill me-1"><i class="bi bi-stars"></i> Recommended for you</span>' : ""}${d.priority && d.priority.urgent ? `<span class="status-pill bad me-1">${App.esc(d.priority.reason || "Urgent")}</span>` : ""}
      <div class="small mt-1">Pickup in about <strong>${Math.max(1, s.pickup_eta_minutes)} min</strong> &middot; reaches the customer in about <strong>${s.delivery_eta_minutes} min</strong> <span class="meta">(estimates)</span></div>
      ${s.batching ? `<div class="small text-success"><i class="bi bi-signpost-split"></i> Fits your route with order ${App.esc(s.batching.with_order)}: only about +${s.batching.detour_minutes} min</div>` : ""}
      ${s.stale_location ? '<div class="small text-warning"><i class="bi bi-geo-alt"></i> Your location is out of date. Keep this page open so estimates stay accurate.</div>' : ""}`;
  };
  const card = (d, mode) => `<div class="panel mb-2"><div class="d-flex justify-content-between flex-wrap gap-1"><strong>${App.esc(d.code)}</strong>${App.statusPill(d.status)}</div>${mode === "available" ? smartLine(d) : ""}
    <div class="row g-2 my-1"><div class="col-sm-6"><div class="meta">Pick up</div><strong>${App.esc(d.vendor.name)}</strong><div class="meta">${App.esc(d.vendor.address)}${d.vendor.phone ? " &middot; " + App.esc(d.vendor.phone) : ""}</div>
        <a class="small" href="${nav(d.vendor.lat, d.vendor.lng)}" target="_blank" rel="noopener">Directions</a></div>
      <div class="col-sm-6"><div class="meta">Deliver to</div><strong>${App.esc(d.customer.address)}</strong>${mode === "active" ? `<div class="meta"><i class="bi bi-telephone"></i> <a href="tel:${App.esc(d.customer.phone)}">${App.esc(d.customer.phone)}</a>${d.customer.notes ? " &middot; " + App.esc(d.customer.notes) : ""}</div>` : ""}
        <a class="small" href="${nav(d.customer.lat, d.customer.lng)}" target="_blank" rel="noopener">Directions</a></div></div>
    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2"><span class="meta">${d.item_count} item(s) &middot; ${d.trip_km} km trip${d.pickup_km != null ? " &middot; " + d.pickup_km + " km to pickup" : ""}</span><strong class="text-success">Earn ${App.money(d.earning)}</strong></div>
    ${d.status === "RETURNED" ? `<div class="meta mt-1 text-warning"><i class="bi bi-arrow-counterclockwise"></i> Returning to the vendor — waiting for them to accept the return.</div>` : ""}
    ${d.status === "ACCEPTED_RETURN" ? `<div class="meta mt-1"><i class="bi bi-arrow-counterclockwise"></i> The vendor accepted — bring the food back, then tap "Handed back to vendor" once you've given it to them.</div>` : ""}
    ${d.wait_until ? `<div class="meta mt-1"><i class="bi bi-hourglass-split"></i> Waiting for the customer${d.wait_minutes ? ` (up to ${d.wait_minutes} min)` : ""} — <span class="wait-cd" data-until="${d.wait_until}"></span> left</div>` : ""}
    <div class="d-flex gap-2 mt-2 flex-wrap">${mode === "available" ? `<button class="btn btn-dark" data-accept="${d.order_id}">Accept delivery</button>` : ""}
      ${mode === "active" && ACTIONS[d.status] ? `<button class="btn btn-dark" data-act="${ACTIONS[d.status][0]}" data-id="${d.order_id}">${ACTIONS[d.status][1]}</button>` : ""}
      ${mode === "active" && d.status === "OUT_FOR_DELIVERY" && !d.arrived_at ? `<button class="btn btn-outline-dark" data-act="arrived" data-id="${d.order_id}">I've arrived</button>` : ""}
      ${mode === "active" && d.status === "RIDER_ARRIVED" && !d.wait_until ? `<button class="btn btn-outline-dark" data-act="arrived" data-id="${d.order_id}">Wait for customer</button>` : ""}
      ${mode === "active" && d.status === "RIDER_ASSIGNED" ? `<button class="btn btn-outline-danger" data-act="release" data-id="${d.order_id}">Release</button>` : ""}
      ${mode === "active" && ["OUT_FOR_DELIVERY", "RIDER_ASSIGNED", "PICKED_UP", "RIDER_ARRIVED"].includes(d.status) ? `<button class="btn btn-outline-danger" data-report="${d.order_id}">Report a problem</button>` : ""}
      ${mode === "active" && ["OUT_FOR_DELIVERY", "PICKED_UP", "RIDER_ARRIVED"].includes(d.status) ? `<button class="btn btn-outline-danger" data-return="${d.order_id}">Return to vendor</button>` : ""}</div></div>`;

  Dash.register("rider", { id: "deliveries", label: "Deliveries", icon: "bi-bicycle", async render(el) {
    let me = await App.call("/rider/me"); if (!me) return;
    let timer;
    async function load() {
      const [avail, active] = await Promise.all([App.call("/rider/available"), App.call("/rider/deliveries", { params: { view: "active" } })]);
      if (!avail || !active) return;
      el.innerHTML = `<div class="panel mb-3 d-flex justify-content-between align-items-center flex-wrap gap-2"><div><strong>${me.is_online ? "You're online" : "You're offline"}</strong>
          <div class="meta">${me.is_online ? "New deliveries appear below." : "Go online to see and accept deliveries."}</div></div>
        <button class="btn ${me.is_online ? "btn-outline-danger" : "btn-success"}" id="toggle">${me.is_online ? "Go offline" : "Go online"}</button></div>
        <h2 class="h6">My active deliveries</h2>${active.length ? active.map((d) => card(d, "active")).join("") : '<p class="meta">None right now.</p>'}
        <h2 class="h6 mt-3">Available nearby</h2>${me.is_online ? (avail.length ? avail.map((d) => card(d, "available")).join("") : App.empty("bi-bicycle", "No deliveries waiting", "We'll show them here as soon as they're ready.")) : '<p class="meta">Go online to see available deliveries.</p>'}`;
      el._active = active;
    }
    el.addEventListener("click", async (e) => {
      const t = e.target.closest("button"); if (!t) return;
      if (t.id === "toggle") { const r = await App.call("/rider/online", { method: "POST", params: { is_online: !me.is_online } }); if (r) { me = r; (typeof RiderTracker !== "undefined" ? RiderTracker : undefined) && (me.is_online ? RiderTracker.start() : RiderTracker.stop()); load(); } }
      else if (t.dataset.accept) { if (await App.call(`/rider/deliveries/${t.dataset.accept}/accept`, { method: "POST" })) { UI.toast("Delivery accepted."); load(); } else load(); }
      else if (t.dataset.report) {
        const f = await Dash.formModal({ title: `Report a problem — order`, submitLabel: "Send report", fields: [
          { name: "outcome", label: "What happened?", type: "select", required: true, options: [
            { value: "COMPLETED_PAID", label: "Delivered — customer paid the cash" },
            { value: "ARRIVED_NOT_PAID", label: "Arrived — customer did not pay" },
            { value: "CUSTOMER_NO_SHOW", label: "Customer was not there / did not answer" },
            { value: "WRONG_ADDRESS", label: "Address was wrong or unreachable" },
            { value: "OTHER", label: "Other problem" }] },
          { name: "message", label: "Describe what happened (times, amounts, what the customer said)", type: "textarea", required: true, maxlength: 1000 },
        ] });
        if (!f) return;
        const r = await App.call(`/rider/deliveries/${t.dataset.report}/report`, { method: "POST", body: f });
        if (r) {
          UI.toast(r.message || "Report sent.");
          const d = (el._active || []).find((x) => x.order_id === Number(t.dataset.report));
          const reason = { ARRIVED_NOT_PAID: "COD_REFUSED", CUSTOMER_NO_SHOW: "NO_SHOW", WRONG_ADDRESS: "WRONG_ADDRESS", OTHER: "OTHER" }[f.outcome];
          if (reason && d && ["PICKED_UP", "OUT_FOR_DELIVERY", "RIDER_ARRIVED"].includes(d.status)
              && confirm("Take the food back to the vendor now? The vendor must accept the return, then you mark it handed over and they confirm receipt.")) {
            await App.call(`/rider/deliveries/${t.dataset.report}/return`, { method: "POST", body: { reason, note: (f.message || "").slice(0, 300) || null } });
            UI.toast("Marked as returning to vendor.");
          }
          load();
        }
      }
      else if (t.dataset.return) {
        const f = await Dash.formModal({ title: "Return order to vendor", submitLabel: "Start return", fields: [
          { name: "reason", label: "What happened?", type: "select", required: true, options: [
            { value: "COD_REFUSED", label: "Customer refused to pay" },
            { value: "NO_SHOW", label: "Customer was not there / did not answer" },
            { value: "WRONG_ADDRESS", label: "Wrong address or unreachable" },
            { value: "OTHER", label: "Other problem" }] },
          { name: "note", label: "Note for the vendor (optional)", type: "textarea", maxlength: 300 }] });
        if (!f) return;
        if (await App.call(`/rider/deliveries/${t.dataset.return}/return`, { method: "POST", body: { reason: f.reason, note: f.note || null } })) { UI.toast("Marked as returning to vendor."); load(); }
      }
      else if (t.dataset.act) {
        if (t.dataset.act === "release" && !confirm("Release this delivery back to other riders?")) return;
        let body;
        if (t.dataset.act === "arrived") {
          const w = await Dash.formModal({ title: "At the customer's door", submitLabel: "Continue", fields: [
            { name: "wait_minutes", label: "Will you wait for the customer? (optional, up to 5 minutes)", type: "select", options: [
              { value: "0", label: "No — head back to the vendor" },
              { value: "1", label: "Wait 1 minute" }, { value: "2", label: "Wait 2 minutes" },
              { value: "3", label: "Wait 3 minutes" }, { value: "4", label: "Wait 4 minutes" },
              { value: "5", label: "Wait 5 minutes" }] }] });
          if (!w) return;
          body = { wait_minutes: Number(w.wait_minutes) };
        }
        if (await App.call(`/rider/deliveries/${t.dataset.id}/${t.dataset.act}`, { method: "POST", body })) {
          UI.toast(t.dataset.act === "returned" ? "Done — now ask the vendor to confirm receipt. The order only closes once they do." : "Updated."); load();
        }
      }
    });
    await load();
    if (me.is_online && (typeof RiderTracker !== "undefined" ? RiderTracker : undefined)) RiderTracker.start();
    timer = setInterval(load, 12000);
    const tick = () => el.querySelectorAll(".wait-cd").forEach((s) => {
      const left = Math.max(0, Math.ceil((new Date(s.dataset.until) - Date.now()) / 1000));
      s.textContent = left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "time's up";
    });
    tick();
    const waitTimer = setInterval(tick, 1000);
    this.teardown = () => { clearInterval(timer); clearInterval(waitTimer); };
  } });

  Dash.register("rider", { id: "history", label: "History", icon: "bi-clock-history", async render(el) {
    const list = await App.call("/rider/deliveries", { params: { view: "history" } }); if (!list) return;
    el.innerHTML = list.length ? Dash.table(["Order", "Status", "From", "To", "Earned"], list.map((d) => `<tr><td>${App.esc(d.code)}</td><td>${App.statusPill(d.status)}</td><td>${App.esc(d.vendor.name)}</td><td>${App.esc(d.customer.address)}</td><td>${App.money(d.earning)}</td></tr>`)) : App.empty("bi-clock-history", "No completed deliveries yet", "");
  } });

  Dash.register("rider", { id: "earnings", label: "Earnings", icon: "bi-cash-coin", async render(el) {
    const e = await App.call("/rider/earnings"); if (!e) return;
    el.innerHTML = `<div class="stat-grid">${Dash.stat(App.money(e.today), "Today")}${Dash.stat(App.money(e.this_week), "This week")}${Dash.stat(App.money(e.all_time), "All time")}${Dash.stat(e.deliveries, "Deliveries")}</div>
      ${e.recent.length ? Dash.table(["Order #", "Earned", "When"], e.recent.map((r) => `<tr><td>${r.order_id}</td><td>${App.money(r.earning)}</td><td>${App.fmtDate(r.at)}</td></tr>`)) : App.empty("bi-cash-coin", "No earnings yet", "Complete a delivery to start earning.")}
      <p class="meta mt-2">Payouts are handled by KORA EATS. Earnings shown are your share of each delivery fee.</p>`;
  } });

  Dash.register("rider", { id: "profile", label: "Profile", icon: "bi-person", async render(el) {
    const me = await App.call("/rider/me"); if (!me) return;
    el.innerHTML = `<form class="panel" id="rf"><div class="mb-2 text-muted">${App.esc(me.name)} &middot; ${App.esc(me.email)}</div>
      <div class="mb-2"><label class="form-label" for="r-phone">Phone</label><input class="form-control" id="r-phone" value="${App.esc(me.phone)}" required></div>
      <div class="mb-2"><label class="form-label" for="r-veh">Vehicle</label><select class="form-select" id="r-veh">${["MOTORBIKE", "BICYCLE", "CAR", "ON_FOOT"].map((v) => `<option ${me.vehicle_type === v ? "selected" : ""} value="${v}">${v.replace("_", " ")}</option>`).join("")}</select></div>
      <div class="mb-3"><label class="form-label" for="r-plate">Plate number</label><input class="form-control" id="r-plate" value="${App.esc(me.plate_number || "")}"></div><button class="btn btn-dark">Save</button></form>`;
    el.querySelector("#rf").addEventListener("submit", async (e) => { e.preventDefault(); if (await App.call("/rider/me", { method: "PUT", body: { phone: el.querySelector("#r-phone").value.trim(), vehicle_type: el.querySelector("#r-veh").value, plate_number: el.querySelector("#r-plate").value.trim() || null } })) UI.toast("Profile saved."); });
  } });
})();
