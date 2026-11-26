(async () => {
  if (!(await App.mountShell({ active: "profile", requireLogin: true }))) return;
  const root = document.getElementById("page");
  root.innerHTML = `<h1 class="h4">Payments</h1><div id="debt-slot"></div><h2 class="h6 mt-4">Payment history</h2><div id="list">${App.skeletons(2)}</div>`;

  // Came back from paying a balance: ask the server to confirm with the provider, then show the result.
  const params = new URLSearchParams(location.search);
  const debtPayId = params.get("debt");
  if (debtPayId) {
    history.replaceState(null, "", "/payments.html");
    const v = await App.call(`/payments/debt/${debtPayId}/verify`);
    if (v && v.payment_status === "SUCCESS") UI.toast("Balance payment confirmed — thank you!");
    else if (v && v.payment_status === "FAILED") UI.toast("That payment didn't go through.", "error");
  }

  const [debt, list] = await Promise.all([App.call("/payments/debt"), App.call("/payments")]);
  if (!debt || !list) return;

  const slot = document.getElementById("debt-slot");
  if (debt.owed > 0) {
    slot.innerHTML = `<div class="panel border-warning">
      <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div><strong>You owe KORA EATS ${App.money(debt.owed)}</strong>
          <div class="meta">${App.esc(debt.explanation)}</div></div>
        <button class="btn btn-dark" id="pay-debt">Pay ${App.money(debt.owed)} now</button></div>
      ${debt.debts.length ? `<hr><div class="small fw-semibold mb-1">What it's for</div>${debt.debts.map((d) =>
        `<div class="summary-row"><span>Order ${App.esc(d.order_code)} — ${App.esc((d.reason || "").replace(/_/g, " ").toLowerCase())}</span><span>${App.money(d.outstanding)}</span></div>`).join("")}` : ""}
      ${debt.credit > 0 ? `<div class="meta mt-2">You also have ${App.money(debt.credit)} credit towards your balance.</div>` : ""}
      ${debt.no_shows > 0 ? `<hr><div class="meta">${debt.no_shows} order${debt.no_shows === 1 ? "" : "s"} couldn't be delivered because you weren't available. Repeated no-shows pause pay on delivery and may be reviewed by our team.</div>` : ""}
    </div>`;
    document.getElementById("pay-debt").addEventListener("click", async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      const methods = (await App.call("/payments/methods")) || [{ id: "MOBILE_MONEY" }];
      const method = methods.length === 1 ? methods[0].id : window.prompt("Pay with (" + methods.map((m) => m.id).join(", ") + ")", methods[0].id);
      if (!method || !methods.some((m) => m.id === method)) { btn.disabled = false; return; }
      const key = "debt-" + crypto.randomUUID();
      const res = await fetch("/api/payments/debt/initiate?method=" + encodeURIComponent(method), {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: "{}" });
      const data = await res.json().catch(() => null);
      if (!res.ok) { UI.toast(App.errorMessage({ ok: false, data }), "error"); btn.disabled = false; return; }
      if (data.checkout_url) location.href = data.checkout_url;
      else { UI.toast("Payment started."); btn.disabled = false; }
    });
  } else {
    slot.innerHTML = debt.no_shows > 0
      ? `<div class="panel"><div class="meta">${debt.no_shows} order${debt.no_shows === 1 ? "" : "s"} couldn't be delivered because you weren't available. Repeated no-shows pause pay on delivery and may be reviewed by our team.</div></div>`
      : "";
  }

  const cls = { SUCCESS: "", PENDING: "warn", FAILED: "bad" };
  document.getElementById("list").innerHTML = list.length ? list.map((p) => `<a class="panel d-block text-decoration-none text-reset mb-2" href="${p.order_id ? "/order.html?id=" + p.order_id : "#"}">
    <div class="d-flex justify-content-between"><strong>${App.esc(p.order_code || "Balance payment")}</strong><span class="status-pill ${cls[p.status]}">${p.status}</span></div>
    <div class="meta">${App.fmtDate(p.created_at)} &middot; ${p.method.replace("_", " ")}</div><div class="price">${App.money(p.amount)}</div></a>`).join("")
    : App.empty("bi-credit-card", "No payments yet", "Your payments will appear here.");
})();
