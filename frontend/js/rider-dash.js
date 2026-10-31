/* Rider dashboard: go online, take deliveries, run them step by step, see earnings. */
(() => {
  const ACTIONS = { RIDER_ASSIGNED: ["pickup", "Picked up the order"], PICKED_UP: ["out-for-delivery", "Start delivery"], OUT_FOR_DELIVERY: ["delivered", "Mark delivered"] };
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
    <div class="d-flex gap-2 mt-2 flex-wrap">${mode === "available" ? `<button class="btn btn-dark" data-accept="${d.order_id}">Accept delivery</button>` : ""}
      ${mode === "active" && ACTIONS[d.status] ? `<button class="btn btn-dark" data-act="${ACTIONS[d.status][0]}" data-id="${d.order_id}">${ACTIONS[d.status][1]}</button>` : ""}
      ${mode === "active" && d.status === "OUT_FOR_DELIVERY" && !d.arrived_at ? `<button class="btn btn-outline-dark" data-act="arrived" data-id="${d.order_id}">I've arrived</button>` : ""}
      ${mode === "active" && d.status === "RIDER_ASSIGNED" ? `<button class="btn btn-outline-danger" data-act="release" data-id="${d.order_id}">Release</button>` : ""}</div></div>`;

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
    }
    el.addEventListener("click", async (e) => {
      const t = e.target.closest("button"); if (!t) return;
      if (t.id === "toggle") { const r = await App.call("/rider/online", { method: "POST", params: { is_online: !me.is_online } }); if (r) { me = r; (typeof RiderTracker !== "undefined" ? RiderTracker : undefined) && (me.is_online ? RiderTracker.start() : RiderTracker.stop()); load(); } }
      else if (t.dataset.accept) { if (await App.call(`/rider/deliveries/${t.dataset.accept}/accept`, { method: "POST" })) { UI.toast("Delivery accepted."); load(); } else load(); }
      else if (t.dataset.act) {
        if (t.dataset.act === "release" && !confirm("Release this delivery back to other riders?")) return;
        if (await App.call(`/rider/deliveries/${t.dataset.id}/${t.dataset.act}`, { method: "POST" })) { UI.toast("Updated."); load(); }
      }
    });
    await load();
    if (me.is_online && (typeof RiderTracker !== "undefined" ? RiderTracker : undefined)) RiderTracker.start();
    timer = setInterval(load, 12000);
    this.teardown = () => clearInterval(timer);
  } });

  Dash.register("rider", { id: "history", label: "History", icon: "bi-clock-history", async render(el) {
    const list = await App.call("/rider/deliveries", { params: { view: "history" } }); if (!list) return;
    el.innerHTML = list.length ? Dash.table(["Order", "Status", "From", "To", "Earned"], list.map((d) => `<tr><td>${App.esc(d.code)}</td><td>${App.statusPill(d.status)}</td><td>${App.esc(d.vendor.name)}</td><td>${App.esc(d.customer.address)}</td><td>${App.money(d.earning)}</td></tr>`)) : App.empty("bi-clock-history", "No completed deliveries yet", "");
  } });

  Dash.register("rider", { id: "earnings", label: "Wallet", icon: "bi-wallet2", async render(el) {
    const wallet = await App.call("/rider/wallet");
    if (!wallet) return;
    
    const cash_in_hand = wallet.cash_in_hand || 0;
    const max_cash = 300; // Configured in backend
    const cash_warning = cash_in_hand > max_cash * 0.8;
    const cash_blocked = cash_in_hand >= max_cash;
    const needs_topup = cash_in_hand < 0;
    const available_balance = wallet.available || 0;
    
    el.innerHTML = `<div class="stat-grid">${Dash.stat(App.money(available_balance), "Available Balance")}${Dash.stat(App.money(wallet.withdrawn || 0), "Withdrawn")}${Dash.stat(App.money(wallet.lifetime_earnings || 0), "Lifetime Earnings")}${Dash.stat(wallet.commission_paid || 0, "Commission Paid")}</div>
      <div class="panel mt-3">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <strong>Cash to hand over</strong>
          <span class="${cash_blocked ? "text-danger" : cash_warning ? "text-warning" : "text-success"}">${App.money(Math.abs(cash_in_hand))}</span>
        </div>
        <div class="meta small">
          ${cash_blocked ? `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> You have reached the cash limit (${App.money(max_cash)}). You cannot take new pay-on-delivery orders until you remit this cash to KORA EATS.</span>` : 
           cash_warning ? `<span class="text-warning"><i class="bi bi-exclamation-circle"></i> Approaching cash limit (${App.money(max_cash)}). Remit cash soon to avoid being blocked from COD orders.</span>` :
           `<span class="text-success"><i class="bi bi-check-circle"></i> Within cash limit (${App.money(max_cash)}). You can take pay-on-delivery orders.</span>`}
        </div>
        ${cash_in_hand < 0 ? `<div class="meta small mt-2 text-info"><i class="bi bi-info-circle"></i> Your earnings from non-COD orders will automatically clear this debt.</div>` : ""}
        ${needs_topup ? `<div class="mt-3">
          <button class="btn btn-warning w-100" id="topup-btn"><i class="bi bi-credit-card"></i> Top up to clear balance</button>
          <div class="meta small mt-2">Pay via Mobile Money to clear your negative balance and start receiving COD orders again.</div>
        </div>` : ""}
      </div>
      <p class="meta mt-2">Your wallet balance and COD cash status. Top up to clear negative balances and continue receiving orders.</p>`;
    
    if (needs_topup) {
      el.querySelector("#topup-btn").addEventListener("click", async () => {
        const amount = Math.abs(cash_in_hand) + 50; // Add buffer
        const confirmAmount = prompt(`Enter top-up amount (GH₵). Minimum: ${App.money(Math.abs(cash_in_hand))}`, amount.toFixed(2));
        if (!confirmAmount) return;
        
        const numAmount = parseFloat(confirmAmount);
        if (isNaN(numAmount) || numAmount < Math.abs(cash_in_hand)) {
          UI.toast("Invalid amount. Must be at least " + App.money(Math.abs(cash_in_hand)), "error");
          return;
        }
        
        const btn = el.querySelector("#topup-btn");
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Processing...';
        
        const r = await App.call("/rider/topup/initiate", { method: "POST", params: { amount: numAmount } });
        if (!r.ok) {
          btn.disabled = false;
          btn.innerHTML = '<i class="bi bi-credit-card"></i> Top up to clear balance';
          return UI.toast(App.errorMessage(r), "error");
        }
        
        // Redirect to Paystack
        location.assign(r.data.checkout_url);
      });
    }
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
