(async () => {
  const user = await App.mountShell({ active: "cart", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  const addrId = localStorage.getItem("foodapp.addressId");

  async function load() {
    const cart = await App.call("/cart", { params: { address_id: addrId } });
    if (!cart) return;
    Cart.setBadge(cart.item_count);
    if (!cart.items.length) { root.innerHTML = `<h1 class="h4">Your cart</h1>${App.empty("bi-bag", "Your cart is empty", "Add something tasty to get started.")}<div class="text-center"><a class="btn btn-dark" href="/">Browse food</a></div>`; return; }
    root.innerHTML = `<h1 class="h4">Your cart</h1><p class="meta">From <a href="/vendor.html?id=${cart.vendor.id}">${App.esc(cart.vendor.name)}</a></p>
      <div class="row g-3"><div class="col-lg-7">${cart.items.map((i) => `
        <div class="panel mb-2"><div class="d-flex justify-content-between gap-2"><div><strong>${App.esc(i.name)}</strong>${i.available ? "" : ' <span class="badge-closed">Unavailable</span>'}
          <div class="meta">${i.original_price ? `<s>${App.money(i.original_price)}</s> ` : ""}${App.money(i.unit_price)} each</div></div><strong>${App.money(i.line_total)}</strong></div>
          <input class="form-control form-control-sm my-2" data-note="${i.id}" value="${App.esc(i.special_instructions || "")}" placeholder="Special instructions (e.g. no pepper)" maxlength="200" aria-label="Special instructions for ${App.esc(i.name)}">
          <div class="d-flex justify-content-between align-items-center"><div class="qty"><button data-dec="${i.id}" data-q="${i.quantity}" aria-label="Decrease">-</button><span>${i.quantity}</span><button data-inc="${i.id}" data-q="${i.quantity}" aria-label="Increase">+</button></div>
          <button class="btn btn-sm btn-link text-danger" data-rm="${i.id}">Remove</button></div></div>`).join("")}
        <button class="btn btn-link text-muted btn-sm" id="clear">Clear cart</button></div>
      <div class="col-lg-5"><div class="panel"><h2 class="h6">Order summary</h2>
        <form class="d-flex gap-2 mb-3" id="coupon-form"><input class="form-control form-control-sm" id="coupon" placeholder="Promo code" value="${App.esc(cart.coupon_code || "")}" maxlength="40" aria-label="Promo code"><button class="btn btn-sm btn-outline-dark">${cart.coupon_code ? "Update" : "Apply"}</button></form>
        ${cart.coupon_message ? `<div class="meta mb-2">${App.esc(cart.coupon_message)}</div>` : ""}
        <div class="summary-row"><span>Subtotal</span><span>${App.money(cart.subtotal)}</span></div>
        <div class="summary-row"><span>Delivery</span><span>${cart.delivery_fee === null ? '<span class="meta">Calculated at checkout</span>' : App.money(cart.delivery_fee)}</span></div>
        ${cart.discount ? `<div class="summary-row text-success"><span>Discount</span><span>- ${App.money(cart.discount)}</span></div>` : ""}
        <div class="summary-row total"><span>Total</span><span>${App.money(cart.total)}${cart.delivery_fee === null ? "+" : ""}</span></div>
        ${cart.errors.map((e) => `<div class="alert alert-warning py-2 mt-2 mb-0">${App.esc(e)}</div>`).join("")}
        ${cart.options && cart.options.pickup_message ? `<div class="alert alert-success py-2 mt-2 mb-0"><i class="bi bi-piggy-bank"></i> ${App.esc(cart.options.pickup_message)}</div>` : ""}
        <a class="btn btn-dark w-100 mt-3 ${cart.errors.length ? "disabled" : ""}" href="/checkout.html">Continue to checkout</a></div></div></div>`;
  }
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const patch = (id, q) => App.call(`/cart/items/${id}`, { method: "PATCH", body: { quantity: q } });
    if (t.dataset.inc) await patch(t.dataset.inc, Number(t.dataset.q) + 1);
    else if (t.dataset.dec) await patch(t.dataset.dec, Number(t.dataset.q) - 1);
    else if (t.dataset.rm) await App.call(`/cart/items/${t.dataset.rm}`, { method: "DELETE" });
    else if (t.id === "clear") { if (!confirm("Clear your cart?")) return; await App.call("/cart", { method: "DELETE" }); }
    else return;
    load();
  });
  root.addEventListener("change", async (e) => {
    const n = e.target.closest("[data-note]"); if (!n) return;
    await App.call(`/cart/items/${n.dataset.note}`, { method: "PATCH", body: { special_instructions: n.value.trim() } });
  });
  root.addEventListener("submit", async (e) => {
    if (e.target.id !== "coupon-form") return;
    e.preventDefault();
    const code = document.getElementById("coupon").value.trim();
    const r = await App.api("/cart/coupon", code ? { method: "POST", body: { code } } : { method: "DELETE" });
    if (!r.ok) UI.toast(App.errorMessage(r), "error"); else UI.toast(code ? "Promo code applied." : "Promo code removed.");
    load();
  });
  load();
})();
