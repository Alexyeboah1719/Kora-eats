/* Wallet section shared by the vendor and rider dashboards. Same API shape under
   /vendor/wallet and /rider/wallet: balance, payout account, withdrawal rules, statement. */
(() => {
  const KIND_LABEL = {
    ORDER_SALE: "Food sales", ORDER_COMMISSION: "Commission", DISCOUNT_FUNDING: "Coupon you funded",
    DELIVERY_PAY: "Delivery fee", DELIVERY_COMMISSION: "Platform deduction", PLATFORM_DISCOUNT: "Platform coupon cost",
    COD_CASH_COLLECTED: "COD cash collected", COD_REMITTANCE: "COD remitted", COD_REFUSED_LOSS: "COD refused loss",
    WITHDRAWAL: "Withdrawal", WITHDRAWAL_FEE: "Withdrawal fee", WITHDRAWAL_REVERSAL: "Withdrawal returned",
    REFUND_REVERSAL: "Refund reversal", ADJUSTMENT: "Adjustment",
  };
  const WD_LABEL = { PENDING_APPROVAL: "Checking", PROCESSING: "Sending", SUCCESS: "Paid out", FAILED: "Failed", REVERSED: "Returned", REJECTED: "Rejected" };
  const wdClass = (s) => (s === "FAILED" || s === "REVERSED" || s === "REJECTED" ? "bad" : s === "PENDING_APPROVAL" ? "warn" : "");
  const pretty = (s) => KIND_LABEL[s] || String(s || "").replace(/_/g, " ").toLowerCase();

  async function payoutSetup(prefix, el, paint) {
    const kind = await Dash.formModal({ title: "Where should we send your money?", submitLabel: "Continue", fields: [
      { name: "type", label: "Payout method", type: "select", required: true, options: [{ value: "MOBILE_MONEY", label: "Mobile Money" }, { value: "BANK", label: "Bank account" }] },
    ] });
    if (!kind) return;
    const banks = await App.call(`${prefix}/wallet/banks`); if (!banks) return;
    const options = kind.type === "MOBILE_MONEY" ? banks.mobile_money : banks.banks;
    if (!options.length) { UI.toast("The payment provider's bank list is unavailable. Try again later.", "error"); return; }
    const vals = await Dash.formModal({ title: kind.type === "MOBILE_MONEY" ? "Mobile Money details" : "Bank details", submitLabel: "Save payout account", fields: [
      { name: "provider_code", label: kind.type === "MOBILE_MONEY" ? "Network" : "Bank", type: "select", required: true, options: options.map((o) => ({ value: o.code, label: o.name })) },
      { name: "account_number", label: kind.type === "MOBILE_MONEY" ? "Mobile number (e.g. 024 123 4567)" : "Account number", type: "text", required: true, maxlength: 32 },
      { name: "account_name", label: "Account holder name", type: "text", required: true, maxlength: 100 },
    ] });
    if (!vals) return;
    const r = await App.call(`${prefix}/wallet/payout-account`, { method: "PUT", body: { type: kind.type, provider_code: vals.provider_code, account_number: vals.account_number, account_name: vals.account_name } });
    if (r) { UI.toast("Payout account saved. Withdrawals pause briefly for security."); paint(); }
  }

  function section(area, prefix) {
    Dash.register(area, { id: "wallet", label: "Wallet", icon: "bi-wallet2", async render(el) {
      const paint = async () => {
        const [w, st, up] = await Promise.all([App.call(`${prefix}/wallet`), App.call(`${prefix}/wallet/statement`, { params: { limit: 50 } }), App.call(`${prefix}/wallet/upcoming`)]);
        if (!w) return;
        const b = w.balance, e = w.eligibility, pa = w.payout_account;
        const statCells = [
          Dash.stat(App.money(b.available), "Available now"),
          Dash.stat(App.money(b.pending), "Held (pending release)"),
          Dash.stat(App.money(b.withdrawable), "Can withdraw"),
          prefix === "/rider" ? Dash.stat(App.money(b.cash_in_hand), "COD cash to remit") : Dash.stat(App.money(b.total), "Lifetime balance"),
        ];
        const reasons = e.reasons.length ? `<div class="alert alert-warning py-2 small mb-2">${e.reasons.map((r) => `<div><i class="bi bi-exclamation-triangle"></i> ${App.esc(r)}</div>`).join("")}</div>` : "";
        const infos = e.info.length ? `<ul class="meta mb-2 ps-3">${e.info.map((i) => `<li>${App.esc(i)}</li>`).join("")}</ul>` : "";
        const paHtml = pa
          ? `<div class="d-flex align-items-center justify-content-between flex-wrap gap-2"><div><strong>${App.esc(pa.provider_name || pa.provider_code)}</strong> ${App.esc(pa.account_number_masked)}<div class="meta">${App.esc(pa.account_name)} &middot; ${pa.type === "MOBILE_MONEY" ? "Mobile Money" : "Bank"}</div></div>
             <button class="btn btn-sm btn-outline-dark" id="wd-payout">Change</button></div>`
          : `<div class="d-flex align-items-center justify-content-between flex-wrap gap-2"><div class="meta">No payout account yet — add a Mobile Money number or bank account to withdraw.</div>
             <button class="btn btn-sm btn-dark" id="wd-payout">Add payout account</button></div>`;
        const wds = w.recent_withdrawals || [];
        const groups = (st && st.orders ? st.orders : []).map((g) => {
          const when = g.entries && g.entries.length ? g.entries[g.entries.length - 1].created_at : null;
          const held = g.state === "HELD" ? '<span class="status-pill warn">Held</span>' : g.state === "REFUNDED" ? '<span class="status-pill bad">Refunded</span>' : '<span class="status-pill">Available</span>';
          const detail = (g.parts || []).map((p) => `${App.esc(pretty(p.kind))} ${App.money(p.amount)}`).join(" &middot; ");
          return `<tr><td class="small">${when ? App.fmtDate(when) : ""}</td><td>${App.esc(g.code || "—")}</td><td>${held}</td>
            <td class="small">${detail}</td><td class="text-end fw-semibold ${g.you_earn < 0 ? "text-danger" : ""}">${App.money(g.you_earn)}</td>
            <td class="small meta">${(g.warnings || []).map(App.esc).join(" ")}</td></tr>`;
        });
        const upRows = (up && up.orders ? up.orders : []).map((o) => `<tr><td>${App.esc(o.code)}</td><td>${App.esc(App.STATUS_LABEL[o.order_status] || o.order_status)}</td>
          <td class="text-end fw-semibold">${App.money(o.you_will_earn)}</td><td class="small meta">after ${App.esc(o.available_after || "completion")}</td></tr>`);
        el.innerHTML = `
          <div class="stat-grid">${statCells.join("")}</div>
          <p class="meta mb-2">${App.esc(w.explain || "")}${w.rates ? ` &middot; ${w.rates.commission_percent !== undefined ? w.rates.commission_percent + "% commission" : w.rates.deduction_percent + "% deduction"}` : ""}</p>
          <div class="chart-box mb-3"><h2 class="h6">Payout account</h2>${paHtml}</div>
          <div class="chart-box mb-3"><h2 class="h6">Withdraw</h2>
            ${reasons}${infos}
            <div class="d-flex align-items-center gap-2 flex-wrap">
              <span class="meta">Available to withdraw: <strong>${App.money(e.max_now)}</strong> (min ${App.money(e.minimum)}${e.fee ? `, fee ${App.money(e.fee)}` : ""}${e.daily_limit ? `, daily limit ${App.money(e.daily_limit)}` : ""})</span>
              <button class="btn btn-dark btn-sm ms-auto" id="wd-go" ${e.can_withdraw ? "" : "disabled"}>Withdraw</button>
            </div></div>
          <div class="chart-box mb-3"><h2 class="h6">Withdrawal history</h2>
            ${wds.length ? Dash.table(["Requested", "Amount", "Fee", "You receive", "Status", "Note"], wds.map((x) => `<tr><td class="small">${App.fmtDate(x.requested_at)}</td><td>${App.money(x.amount)}</td><td>${App.money(x.fee)}</td><td>${App.money(x.you_receive)}</td>
              <td><span class="status-pill ${wdClass(x.status)}">${WD_LABEL[x.status] || x.status}</span>${x.failure_reason ? `<div class="meta small">${App.esc(x.failure_reason)}</div>` : ""}</td><td class="small">${App.esc(x.reference || "")}</td></tr>`))
            : App.empty("bi-wallet2", "No withdrawals yet", "")}</div>
          ${upRows.length ? `<div class="chart-box mb-3"><h2 class="h6">In progress — what you'll earn</h2>
            ${Dash.table(["Order", "Status", "You'll earn", ""], upRows)}</div>` : ""}
          <div class="chart-box"><h2 class="h6">Statement</h2>
            ${groups.length ? Dash.table(["When", "Order", "Status", "Breakdown", "Net", "Note"], groups) : App.empty("bi-journal-text", "Nothing here yet", "Your sales, deliveries and payouts will appear here.")}</div>`;

        const po = el.querySelector("#wd-payout"); if (po) po.addEventListener("click", () => payoutSetup(prefix, el, paint));
        const go = el.querySelector("#wd-go");
        if (go) go.addEventListener("click", async () => {
          const vals = await Dash.formModal({ title: "Withdraw to your payout account", submitLabel: "Withdraw", fields: [
            { name: "amount", label: `Amount (GH₵) — up to ${Number(e.max_now).toFixed(2)}`, type: "number", required: true, step: "0.01", min: e.minimum, max: e.max_now },
          ] });
          if (!vals) return;
          const r = await App.call(`${prefix}/wallet/withdrawals`, { method: "POST", body: { amount: Number(vals.amount) }, idempotencyKey: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) });
          if (r) { UI.toast(`Withdrawal of ${App.money(r.amount)} requested.`); paint(); }
        });
      };
      await paint();
    } });
  }

  section("vendor", "/vendor");
  section("rider", "/rider");
})();
