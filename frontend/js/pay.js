/* Starts a payment for an order. A NEW idempotency key per click; the server also reuses a fresh
   pending attempt, so double-clicks and two tabs never create two payments. */
const Pay = (() => {
  async function start(orderId, method) {
    const key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
    const r = await App.api(`/payments/orders/${orderId}/initiate`, { method: "POST", idempotencyKey: key, params: method ? { method } : undefined });
    if (!r.ok) { UI.toast(App.errorMessage(r), "error"); return false; }
    if (!r.data.checkout_url) { UI.toast("This payment can't be continued. Please start a new one.", "error"); return false; }
    location.assign(r.data.checkout_url);     // the provider's secure hosted checkout page (Paystack)
    return true;
  }
  return { start };
})();
