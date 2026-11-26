(async () => {
  const user = await App.mountShell({ active: "cart", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  const [addrs, profile, debtInfo] = await Promise.all([App.call("/me/addresses"), App.call("/me/profile"), App.call("/payments/debt")]);
  const owed = debtInfo && debtInfo.owed ? Number(debtInfo.owed) : 0;
  if (!addrs) return;
  let selected = Number(localStorage.getItem("foodapp.addressId")) || (addrs.find((a) => a.is_default) || addrs[0] || {}).id;
  if (!addrs.some((a) => a.id === selected)) selected = addrs[0] ? addrs[0].id : null;
  let fulfillment = localStorage.getItem("kora.fulfillment") === "PICKUP" ? "PICKUP" : "DELIVERY";
  const methods = (await App.call("/payments/methods")) || [{ id: "MOBILE_MONEY", label: "Mobile Money" }];
  let payMethod = methods[0].id, cart = null;

  root.innerHTML = `<h1 class="h4">Checkout</h1><div class="row g-3"><div class="col-lg-7">
    <div class="panel mb-3"><h2 class="h6">How do you want it?</h2><div id="ful-slot" class="row g-2">${App.skeletons(1)}</div><div id="ful-tip"></div></div>
    <div class="panel mb-3" id="addr-panel"><h2 class="h6">Delivery address</h2>${addrs.length ? `<div id="addr-list">${addrs.map((a) => `
      <label class="d-flex gap-2 align-items-start p-2 border rounded mb-2" style="cursor:pointer"><input class="form-check-input mt-1" type="radio" name="addr" value="${a.id}" ${a.id === selected ? "checked" : ""}>
        <span><strong>${App.esc(a.label)}</strong><br><span class="meta">${App.esc(a.address_line)}, ${App.esc(a.city)}</span></span></label>`).join("")}</div>` : '<p class="meta">You have no saved address yet.</p>'}
      <a class="small link-dark" href="/addresses.html">Add or edit addresses</a></div>
    <div class="panel mb-3 d-none" id="pickup-panel"></div>
    <div class="panel mb-3"><h2 class="h6">Contact</h2>
      <label class="form-label" for="phone">Phone number (for the vendor/rider and Mobile Money)</label>
      <input class="form-control" id="phone" inputmode="tel" value="${App.esc(profile?.phone || "")}" placeholder="+233 24 000 0000" required>
      <label class="form-label mt-3" for="notes">Note for the vendor (optional)</label>
      <input class="form-control" id="notes" maxlength="300" placeholder="e.g. extra shito please"></div>
    <div id="extra-options"></div></div>
    <div class="col-lg-5"><div class="panel" id="summary">${App.skeletons(1)}</div></div></div>`;

  const money = App.money;
  function renderFulfilment() {
    const o = cart.options;
    const card = (key, icon, title, line1, line2, disabled) => `<div class="col-6"><label class="d-block h-100 p-2 border rounded ${fulfillment === key ? "border-dark" : ""}" style="cursor:${disabled ? "not-allowed" : "pointer"};${fulfillment === key ? "background:#f1f6f3;border-width:2px!important" : ""};${disabled ? "opacity:.55" : ""}">
      <input class="visually-hidden" type="radio" name="ful" value="${key}" ${fulfillment === key ? "checked" : ""} ${disabled ? "disabled" : ""}><i class="bi ${icon}"></i> <strong>${title}</strong><div class="meta">${line1}</div><div class="meta">${line2}</div></label></div>`;
    const d = o && o.delivery, p = o && o.pickup;
    document.getElementById("ful-slot").innerHTML =
      card("DELIVERY", "bi-bicycle", "Delivery", d ? (d.available ? `${money(d.fee)} delivery` : "Not available to this address") : "Choose an address", d && d.available ? `about ${d.eta_minutes} min` : "", d && !d.available) +
      card("PICKUP", "bi-bag-check", "Pickup", "No delivery fee", p ? `ready in about ${p.ready_minutes} min` : "", false);
    const tip = document.getElementById("ful-tip");
    tip.innerHTML = fulfillment === "DELIVERY" && o && o.pickup_message
      ? `<div class="alert alert-success d-flex justify-content-between align-items-center gap-2 mt-2 mb-0 py-2"><span><i class="bi bi-piggy-bank"></i> ${App.esc(o.pickup_message)}</span><button class="btn btn-sm btn-success" type="button" id="switch-pickup">Switch to pickup</button></div>` : "";
    document.getElementById("addr-panel").classList.toggle("d-none", fulfillment === "PICKUP");
    const pp = document.getElementById("pickup-panel");
    pp.classList.toggle("d-none", fulfillment !== "PICKUP");
    pp.innerHTML = fulfillment === "PICKUP" && p ? `<h2 class="h6">Pick up from</h2><strong>${App.esc(cart.vendor.name)}</strong><div class="meta"><i class="bi bi-geo-alt"></i> ${App.esc(p.address)}</div>
      <div class="meta">Ready in about ${p.ready_minutes} minutes after the vendor accepts. You'll get a pickup code on your order page.</div>` : "";
  }

  async function loadSummary() {
    cart = await App.call("/cart", { params: { address_id: selected, fulfillment } });
    if (!cart) return;
    if (!cart.items.length) { root.innerHTML = App.empty("bi-bag", "Your cart is empty", ""); return; }
    renderFulfilment();
    const pick = fulfillment === "PICKUP";
    document.getElementById("summary").innerHTML = `<h2 class="h6">Order from ${App.esc(cart.vendor.name)}</h2>
      ${cart.items.map((i) => `<div class="summary-row"><span>${i.quantity} x ${App.esc(i.name)}</span><span>${money(i.line_total)}</span></div>`).join("")}
      <hr><div class="summary-row"><span>Subtotal</span><span>${money(cart.subtotal)}</span></div>
      <div class="summary-row"><span>${pick ? "Pickup" : "Delivery" + (cart.distance_km != null ? ` (${cart.distance_km.toFixed(1)} km)` : "")}</span><span>${pick ? "Free" : cart.delivery_fee == null ? '<span class="meta">Choose an address</span>' : money(cart.delivery_fee)}</span></div>
      ${cart.discount ? `<div class="summary-row text-success"><span>Discount${cart.coupon_code ? " (" + App.esc(cart.coupon_code) + ")" : ""}</span><span>- ${money(cart.discount)}</span></div>` : ""}
      <div class="summary-row total"><span>Total</span><span>${money(cart.total)}</span></div>
      ${owed ? `<div class="summary-row"><span>Outstanding balance (added to payment)</span><span>${money(owed)}</span></div>` : ""}
      ${cart.errors.map((e) => `<div class="alert alert-warning py-2 mt-2 mb-0">${App.esc(e)}</div>`).join("")}
      ${owed ? `<div class="alert alert-warning py-2 mt-2 mb-0"><i class="bi bi-exclamation-triangle"></i> You owe KORA EATS ${money(owed)} for an earlier order that couldn't be delivered — KORA EATS already paid the restaurant and the rider for it, so it's owed to KORA EATS only. It's added to this online payment; pay on delivery is unavailable until it's settled.</div>` : ""}
      ${methods.length > 1 ? `<div class="mt-3"><div class="form-label mb-1">Pay with</div>${methods.map((m) => `<div class="form-check"><input class="form-check-input" type="radio" name="paym" id="pm-${m.id}" value="${m.id}" ${m.id === payMethod ? "checked" : ""}><label class="form-check-label" for="pm-${m.id}">${App.esc(m.label)}</label></div>`).join("")}</div>` : ""}
      ${cart.cod && cart.cod.available ? `<div class="form-check mt-2"><input class="form-check-input" type="radio" name="paym" id="pm-COD" value="COD" ${payMethod === "COD" ? "checked" : ""}><label class="form-check-label" for="pm-COD"><i class="bi bi-cash"></i> <strong>Pay on delivery</strong> <span class="meta">— pay the rider in cash (orders up to ${money(cart.cod.max_total)})</span></label></div>` : ""}
      <div class="meta mt-3"><i class="bi bi-shield-lock"></i> ${payMethod === "COD" ? "Pay the rider in cash when your order arrives." : "You'll pay on Paystack's secure page" + (methods.length === 1 && payMethod !== "COD" ? " with " + App.esc(methods[0].label) : "") + "."}</div>
      <div id="form-alert" class="alert alert-danger d-none mt-2" role="alert"></div>
      <button class="btn btn-dark w-100 mt-3" id="place" ${cart.errors.length || (!pick && !selected) ? "disabled" : ""}>${payMethod === "COD" ? "Place order" : "Place order &amp; pay"} ${money(payMethod === "COD" ? cart.total : cart.total + owed)}</button>`;
    document.getElementById("place").addEventListener("click", place);
    window.dispatchEvent(new CustomEvent("checkout:summary", { detail: { cart } }));
  }
  const setFulfilment = (f) => { fulfillment = f; localStorage.setItem("kora.fulfillment", f); loadSummary(); };
  root.addEventListener("change", (e) => {
    if (e.target.name === "paym") { payMethod = e.target.value; return; }
    if (e.target.name === "ful") return setFulfilment(e.target.value);
    if (e.target.name === "addr") { selected = Number(e.target.value); localStorage.setItem("foodapp.addressId", selected); loadSummary(); }
  });
  root.addEventListener("click", (e) => { if (e.target.id === "switch-pickup") setFulfilment("PICKUP"); });

  async function place(e) {
    const btn = e.currentTarget, alertEl = document.getElementById("form-alert");
    alertEl.classList.add("d-none"); btn.disabled = true;
    const phone = document.getElementById("phone").value.trim();
    const body = { fulfillment_type: fulfillment, phone: phone || null, notes: document.getElementById("notes").value.trim() || null };
    if (fulfillment === "DELIVERY") body.address_id = selected;
    window.dispatchEvent(new CustomEvent("checkout:body", { detail: { body } }));   // the schedule option adds its field here
    const r = await App.api("/orders", { method: "POST", body: { ...body, payment_mode: payMethod === "COD" ? "COD" : "ONLINE" } });
    if (!r.ok) { alertEl.textContent = App.errorMessage(r); alertEl.classList.remove("d-none"); btn.disabled = false; return; }
    Cart.setBadge(0);
    if (payMethod === "COD") { location.assign(`/order.html?id=${r.data.id}`); return; }   // pay the rider in cash
    if (!(await Pay.start(r.data.id, payMethod))) location.assign(`/order.html?id=${r.data.id}`);   // order exists; pay from its page
  }
  loadSummary();
})();
