/* The provider sends the customer back here. This page proves nothing by itself: it asks OUR server,
   which asks the provider, and only then shows success. */
(async () => {
  if (!(await App.mountShell({ active: "orders", requireLogin: true }))) return;
  const root = document.getElementById("page");
  const id = App.qs("order"), ref = App.qs("reference") || App.qs("trxref");   // Paystack returns ?reference=&trxref=
  root.innerHTML = `<div class="text-center py-5" role="status"><div class="spinner-border mb-3" aria-hidden="true"></div><h1 class="h5">Confirming your payment...</h1><p class="meta">Please don't close this page.</p></div>`;
  let order = null;
  for (let i = 0; i < 8; i++) {
    order = await App.call(ref ? `/payments/by-reference/${encodeURIComponent(ref)}/verify` : `/payments/orders/${encodeURIComponent(id)}/verify`);
    if (!order || order.status !== "PENDING_PAYMENT") break;
    await new Promise((r) => setTimeout(r, 2500));
  }
  if (!order) { root.innerHTML = App.empty("bi-exclamation-circle", "We couldn't check this payment", "Open your orders to see its status."); return; }
  const done = !["PENDING_PAYMENT", "PAYMENT_FAILED", "CANCELLED"].includes(order.status);
  const view = done ? { icon: "bi-check-lg", color: "var(--ok)", title: "Payment received!", text: `Order ${order.code} is confirmed with ${order.vendor.name}.` }
    : order.status === "PAYMENT_FAILED" ? { icon: "bi-x-lg", color: "var(--bad)", title: "Payment didn't go through", text: "You haven't been charged. You can try again." }
    : order.status === "CANCELLED" ? { icon: "bi-x-lg", color: "var(--bad)", title: "This order was cancelled", text: "If you were charged, contact support and we'll refund you." }
    : { icon: "bi-hourglass-split", color: "#946b00", title: "Still waiting for confirmation", text: "Mobile Money can take a minute. We'll update your order as soon as the payment is confirmed." };
  root.innerHTML = `<div class="text-center py-4"><div class="success-badge mx-auto" style="background:${view.color};width:72px;height:72px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:2rem"><i class="bi ${view.icon}"></i></div>
    <h1 class="h4 mt-3">${view.title}</h1><p class="meta">${App.esc(view.text)}</p>
    <div class="d-grid gap-2 col-sm-6 mx-auto"><a class="btn btn-dark" href="/order.html?id=${order.id}">View order</a>
    ${order.status === "PAYMENT_FAILED" ? '<button class="btn btn-outline-dark" id="retry">Try again</button>' : ""}</div></div>`;
  document.getElementById("retry")?.addEventListener("click", () => Pay.start(order.id));
})();
