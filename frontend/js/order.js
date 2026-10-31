(async () => {
  const user = await App.mountShell({ active: "orders", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  const id = App.qs("id");
  const LIVE = ["RIDER_ASSIGNED", "PICKED_UP", "OUT_FOR_DELIVERY"];
  const DONE = ["DELIVERED", "COLLECTED", "CANCELLED", "REFUNDED"];
  let liveMap = null, order = null, rating = { food: 0, vendor: 0 };

  root.innerHTML = `<div id="order-box">${App.skeletons(1)}</div>`;
  const box = document.getElementById("order-box");

  function render() {
    const o = order;
    const pickup = o.fulfillment_type === "PICKUP";
    const live = !pickup && LIVE.includes(o.status);
    box.innerHTML = `<div class="d-flex justify-content-between flex-wrap gap-2 mb-1"><h1 class="h4 mb-0">Order ${App.esc(o.code)}</h1><span>${App.fulfilPill(o)} ${App.statusPill(o.status)}</span></div>
      <p class="meta"><a href="/vendor.html?id=${o.vendor.id}">${App.esc(o.vendor.name)}</a> &middot; ${App.fmtDate(o.created_at)}${o.scheduled_for ? ` &middot; <strong>Scheduled for ${App.fmtDate(o.scheduled_for)}</strong>` : ""}</p>
      ${o.can_pay ? `<div class="panel mb-3" style="border-color:var(--gold)"><strong>${o.status === "PAYMENT_FAILED" ? "Your payment didn't go through." : "Waiting for payment."}</strong>
        <div class="d-flex gap-2 mt-2"><button class="btn btn-dark" id="pay">Pay ${App.money(o.total)}</button><button class="btn btn-outline-dark" id="cancel">Cancel order</button></div></div>` : ""}
      ${o.status === "CANCELLED" ? `<div class="alert alert-danger">This order was cancelled${o.cancelled_reason ? ": " + App.esc(o.cancelled_reason) : "."}${o.refund_required ? " Your payment will be refunded; contact support if it hasn't arrived in 2 business days." : ""}</div>` : ""}
      ${pickup && o.pickup_code && ["PAID", "CONFIRMED", "PREPARING", "READY_FOR_PICKUP"].includes(o.status) ? `<div class="panel mb-3" style="border:2px solid var(--ok)">
        <div class="d-flex justify-content-between flex-wrap gap-2"><div><div class="meta">Your pickup code</div><div class="pickup-code" aria-label="Pickup code ${App.esc(o.pickup_code.split("").join(" "))}">${App.esc(o.pickup_code)}</div></div><div id="qr-slot"><img src="/api/orders/${o.id}/pickup-qr.svg" width="112" height="112" alt="QR code for your pickup code" onerror="this.remove()"></div></div>
        <div class="mt-2"><strong>${o.status === "READY_FOR_PICKUP" ? "Your order is ready!" : "We'll notify you when it's ready."}</strong> Show this code to ${App.esc(o.vendor.name)} when you collect.</div>
        <div class="meta mt-1"><i class="bi bi-geo-alt"></i> ${App.esc(o.vendor.address)} &middot; <a href="https://www.openstreetmap.org/?mlat=${o.vendor.lat}&mlon=${o.vendor.lng}#map=17/${o.vendor.lat}/${o.vendor.lng}" target="_blank" rel="noopener">Directions</a>${o.vendor.phone ? ` &middot; <a href="tel:${App.esc(o.vendor.phone)}">Call vendor</a>` : ""}</div></div>` : ""}
      ${live ? `<div class="panel mb-3"><div class="d-flex justify-content-between flex-wrap gap-1 mb-2"><strong><i class="bi bi-geo-alt"></i> Live tracking</strong><span class="meta" id="eta"></span></div><div id="map-slot"></div><div id="rider-card" class="meta"></div></div>` : ""}
      <div class="row g-3"><div class="col-lg-7">
        <div class="panel mb-3"><h2 class="h6">Progress</h2><ul class="timeline mb-0">${o.history.map((h) => `<li><strong>${App.esc(App.STATUS_LABEL[h.status] || h.status)}</strong><div class="meta">${App.fmtDate(h.at)}${h.note ? " &middot; " + App.esc(h.note) : ""}</div></li>`).join("")}</ul></div>
        <div id="review-slot"></div></div>
      <div class="col-lg-5"><div class="panel mb-3"><h2 class="h6">Items</h2>${o.items.map((i) => `<div class="summary-row"><span>${i.quantity} x ${App.esc(i.name)}${i.special_instructions ? `<div class="meta">${App.esc(i.special_instructions)}</div>` : ""}</span><span>${App.money(i.line_total)}</span></div>`).join("")}<hr>
        <div class="summary-row"><span>Subtotal</span><span>${App.money(o.subtotal)}</span></div><div class="summary-row"><span>${pickup ? "Pickup" : "Delivery"}</span><span>${pickup ? "Free" : App.money(o.delivery_fee)}</span></div>
        ${o.discount ? `<div class="summary-row text-success"><span>Discount${o.coupon_code ? " (" + App.esc(o.coupon_code) + ")" : ""}</span><span>- ${App.money(o.discount)}</span></div>` : ""}
        <div class="summary-row total"><span>Total</span><span>${App.money(o.total)}</span></div>
        ${o.payment ? `<div class="meta mt-2">Payment: ${o.payment.status.toLowerCase()} (${o.payment.method.replace("_", " ").toLowerCase()})</div>` : ""}</div>
        <div class="panel"><h2 class="h6">${pickup ? "Pickup" : "Delivery"}</h2><div>${App.esc(o.delivery_address)}</div>${o.delivery_notes ? `<div class="meta">${App.esc(o.delivery_notes)}</div>` : ""}
          ${o.rider ? `<hr><div><i class="bi bi-bicycle"></i> ${App.esc(o.rider.name)} <span class="meta">(${App.esc(o.rider.vehicle.toLowerCase())})</span></div><a class="btn btn-sm btn-outline-dark mt-1" href="tel:${App.esc(o.rider.phone)}"><i class="bi bi-telephone"></i> Call rider</a>` : ""}
          <a class="btn btn-sm btn-outline-dark mt-1" href="/support.html?order=${o.id}"><i class="bi bi-life-preserver"></i> Get help</a></div></div></div>`;
    document.getElementById("pay")?.addEventListener("click", () => Pay.start(o.id));
    document.getElementById("cancel")?.addEventListener("click", async () => { if (confirm("Cancel this order?") && (await App.call(`/orders/${o.id}/cancel`, { method: "POST", body: {} }))) load(); });
    if (live && (typeof MapKit !== "undefined" ? MapKit : undefined) && MapKit.ready()) { liveMap = MapKit.live(document.getElementById("map-slot")); refreshTracking(true); } else liveMap = null;
    renderReview();
  }

  async function refreshTracking(first) {
    if (!liveMap) return;
    const t = await App.call(`/orders/${order.id}/tracking`); if (!t) return;
    liveMap.update({ vendor: t.vendor, customer: t.customer, rider: t.rider, trail: t.trail, refit: first });
    const eta = document.getElementById("eta"), card = document.getElementById("rider-card");
    if (eta) eta.textContent = t.eta_minutes ? `Estimated arrival in about ${t.eta_minutes} min` : "";
    if (card) card.textContent = !t.rider ? "Your rider will appear on the map once they start sharing their location."
      : t.arrived ? "Your rider has arrived!" : t.rider.stale ? "Waiting for the rider's latest location..." : `${t.rider.name} is ${t.distance_km} km away (estimate).`;
  }

  function renderReview() {
    const slot = document.getElementById("review-slot"); if (!slot) return;
    if (order.review) { slot.innerHTML = `<div class="panel"><h2 class="h6">Your review</h2><div class="rating">Food ${"&#9733;".repeat(order.review.food_rating)} &middot; Vendor ${"&#9733;".repeat(order.review.vendor_rating)}</div>${order.review.comment ? `<p class="mb-0">${App.esc(order.review.comment)}</p>` : ""}${order.review.image_url ? `<img class="mt-2 rounded" src="${App.esc(order.review.image_url)}" alt="Review photo" style="max-width:100%;max-height:200px">` : ""}</div>`; return; }
    if (!order.can_review) { slot.innerHTML = ""; return; }
    const stars = (k) => `<div class="stars" data-k="${k}" role="radiogroup" aria-label="${k} rating">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-n="${n}" aria-label="${n} star${n > 1 ? "s" : ""}">&#9733;</button>`).join("")}</div>`;
    slot.innerHTML = `<form class="panel" id="rv" novalidate><h2 class="h6">How was your order?</h2>
      <div class="meta">Food</div>${stars("food")}<div class="meta">Vendor &amp; service</div>${stars("vendor")}
      <textarea class="form-control my-2" id="rv-text" rows="3" maxlength="1000" placeholder="Tell others about the food, packaging, delivery or price (optional)"></textarea>
      <input class="form-control mb-2" id="rv-img" type="file" accept="image/jpeg,image/png,image/webp" aria-label="Optional photo">
      <div id="rv-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark">Submit review</button></form>`;
    slot.querySelectorAll(".stars").forEach((s) => s.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return; rating[s.dataset.k] = Number(b.dataset.n);
      s.querySelectorAll("button").forEach((x) => x.classList.toggle("on", Number(x.dataset.n) <= rating[s.dataset.k]));
    }));
    slot.querySelector("#rv").addEventListener("submit", async (e) => {
      e.preventDefault(); const a = document.getElementById("rv-alert"); a.classList.add("d-none");
      if (!rating.food || !rating.vendor) { a.textContent = "Please rate both the food and the vendor."; return a.classList.remove("d-none"); }
      const r = await App.api("/reviews", { method: "POST", body: { order_id: order.id, food_rating: rating.food, vendor_rating: rating.vendor, comment: document.getElementById("rv-text").value.trim() || null } });
      if (!r.ok) { a.textContent = App.errorMessage(r); return a.classList.remove("d-none"); }
      const f = document.getElementById("rv-img").files[0];
      if (f) { const fd = new FormData(); fd.append("file", f); await App.api(`/reviews/${r.data.id}/image`, { method: "POST", form: fd }); }
      UI.toast("Thanks for your review!"); load();
    });
  }

  async function load() {
    const o = await App.call(`/orders/${encodeURIComponent(id)}`);
    if (!o) { box.innerHTML = App.empty("bi-receipt", "Order not found", ""); return; }
    const changed = !order || order.status !== o.status || JSON.stringify(order.review) !== JSON.stringify(o.review);
    order = o;
    if (changed) render();
    return o;
  }
  await load();
  if (!order) return;
  setInterval(async () => { if (DONE.includes(order.status) && order.review) return; await load(); if (liveMap) refreshTracking(false); }, 8000);
})();
