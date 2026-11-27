/* Admin: where the platform's money is - revenue, what vendors/riders are owed,
   cash held by riders, payout withdrawals to approve, and a Paystack reconciliation. */
Dash.register("admin", { id: "wallets", label: "Wallets", icon: "bi-wallet2", async render(el) {
  let days = 30;
  const WD_LABEL = { PENDING_APPROVAL: "Awaiting approval", PROCESSING: "Processing", SUCCESS: "Paid out", FAILED: "Failed", REVERSED: "Returned", REJECTED: "Rejected" };
  const wdClass = (s) => (s === "FAILED" || s === "REVERSED" || s === "REJECTED" ? "bad" : s === "PENDING_APPROVAL" ? "warn" : "");

  const paint = async () => {
    const [ov, wds] = await Promise.all([App.call(`/admin/wallets/overview?days=${days}`), App.call("/admin/wallets/withdrawals")]);
    if (!ov) return;
    const r = ov.revenue, owed = ov.owed, recon = ov.reconciliation;
    const owedRows = Object.entries(owed).map(([k, o]) => `<tr><td class="text-capitalize">${k}</td><td>${o.accounts}</td><td>${App.money(o.available)}</td><td>${App.money(o.pending)}</td><td>${App.money(o.owed_to_platform)}</td></tr>`).join("");
    const revRows = [["Vendor commission", r.vendor_commission], ["Rider delivery deduction", r.rider_deduction], ["Withdrawal fees", r.withdrawal_fees],
      ["Platform-funded coupons", -Math.abs(r.coupons_funded_by_platform)], ["Cash refusal losses", -Math.abs(r.cash_refusal_losses)], ["Refund clawbacks", -Math.abs(r.refund_clawbacks)],
      ["Adjustments", r.adjustments]].map(([l, v]) => `<tr><td>${l}</td><td class="text-end ${v < 0 ? "text-danger" : ""}">${App.money(v)}</td></tr>`).join("");
    const wdRows = (wds || []).map((w) => `<tr><td class="small">${App.fmtDate(w.requested_at)}</td><td>${App.esc(w.owner)} <span class="meta">(${App.esc(w.owner_type.toLowerCase())})</span></td>
      <td class="text-break small">${App.esc(w.sends_to)}</td><td>${App.money(w.amount)}<div class="meta small">fee ${App.money(w.fee)} &middot; sends ${App.money(w.you_receive)}</div></td>
      <td><span class="status-pill ${wdClass(w.status)}">${WD_LABEL[w.status] || w.status}</span>${w.failure_reason ? `<div class="meta small">${App.esc(w.failure_reason)}</div>` : ""}</td>
      <td class="small">${App.money(w.balance_after)}</td>
      <td>${w.status === "PENDING_APPROVAL" ? `<div class="d-flex gap-1"><button class="btn btn-sm btn-dark" data-wd-ok="${w.id}">Approve</button><button class="btn btn-sm btn-outline-danger" data-wd-no="${w.id}">Reject</button></div>` : ""}</td></tr>`);
    const cashRows = (ov.cash_with_riders.riders || []).map((c) => `<tr><td>${App.esc(c.name)}</td><td class="text-end">${App.money(c.cash_in_hand)}</td></tr>`).join("");
    const recentRows = (ov.recent_orders || []).map((o) => `<tr><td>${App.esc(o.code)}</td><td>#${o.order_id}</td><td class="text-end">${App.money(o.platform_net)}</td></tr>`);
    const wdCounts = Object.entries(ov.withdrawals).map(([k, v]) => `${v.count} ${WD_LABEL[k] || k.toLowerCase()} (${App.money(v.amount)})`).join(" &middot; ");

    el.innerHTML = `
      <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
        <span class="meta">Period:</span>
        ${[7, 30, 90].map((d) => `<button class="btn btn-sm ${d === days ? "btn-dark" : "btn-outline-dark"}" data-days="${d}">${d} days</button>`).join("")}
        <span class="meta ms-auto">${wdCounts || "No withdrawals yet"}</span>
      </div>
      <div class="stat-grid">
        ${Dash.stat(App.money(r.net_revenue), `Net platform revenue (${days} days)`)}
        ${Dash.stat(App.money(owed.vendors?.available || 0), "Owed to vendors (available)")}
        ${Dash.stat(App.money(owed.riders?.available || 0), "Owed to riders (available)")}
        ${Dash.stat(App.money(ov.cash_with_riders.total), "Cash in riders' hands")}
        ${Dash.stat(App.money(recon.expected_paystack_balance), "Expected Paystack balance")}
      </div>
      <div class="row g-3">
        <div class="col-lg-6"><div class="chart-box"><h2 class="h6">Revenue breakdown (${days} days)</h2>
          <div class="table-wrap"><table class="table table-sm align-middle mb-0"><tbody>${revRows}<tr class="fw-bold"><td>Net revenue</td><td class="text-end">${App.money(r.net_revenue)}</td></tr></tbody></table></div></div></div>
        <div class="col-lg-6"><div class="chart-box"><h2 class="h6">Owed to vendors &amp; riders</h2>
          <div class="table-wrap"><table class="table table-sm align-middle mb-0"><thead><tr><th>Owner</th><th>Accounts</th><th>Available</th><th>Pending</th><th>Owes platform</th></tr></thead><tbody>${owedRows}</tbody></table></div></div></div>
      </div>
      <div class="chart-box mt-3"><h2 class="h6">Reconciliation</h2>
        <p class="small mb-1">Ledger balance: <strong>${App.money(recon.ledger_balance)}</strong> &middot; Paid orders in progress: <strong>${App.money(recon.paid_orders_in_progress)}</strong>${recon.debt_payments_received ? ` &middot; Debt payments received: <strong>${App.money(recon.debt_payments_received)}</strong>` : ""} &middot; Expected Paystack balance: <strong>${App.money(recon.expected_paystack_balance)}</strong></p>
        <p class="meta mb-0">${App.esc(recon.note)}</p></div>
      ${cashRows ? `<div class="chart-box mt-3"><h2 class="h6">Cash collected by riders (COD not yet remitted)</h2>
        <div class="table-wrap"><table class="table table-sm align-middle mb-0"><thead><tr><th>Rider</th><th class="text-end">Cash in hand</th></tr></thead><tbody>${cashRows}</tbody></table></div></div>` : ""}
      <div class="chart-box mt-3"><h2 class="h6">Withdrawals</h2>
        ${wds && wds.length ? Dash.table(["Requested", "Who", "Sends to", "Amount", "Status", "Balance after", ""], wdRows)
          : App.empty("bi-wallet2", "No withdrawals yet", "When vendors or riders request a payout it will appear here.")}</div>
      <div class="chart-box mt-3"><h2 class="h6">Recent order earnings (platform share)</h2>
        ${recentRows.length ? Dash.table(["Order", "ID", "Platform net"], recentRows) : App.empty("bi-receipt", "No earnings yet", "")}</div>`;

    el.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => { days = Number(b.dataset.days); paint(); }));
    el.querySelectorAll("[data-wd-ok]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await Dash.confirmBox("Approve and send this withdrawal?"))) return;
      const r2 = await App.call(`/admin/wallets/withdrawals/${b.dataset.wdOk}/approve`, { method: "POST" });
      if (r2) { UI.toast("Withdrawal approved and sent."); paint(); }
    }));
    el.querySelectorAll("[data-wd-no]").forEach((b) => b.addEventListener("click", async () => {
      const vals = await Dash.formModal({ title: "Reject withdrawal", submitLabel: "Reject", fields: [{ name: "reason", label: "Reason (shown to the owner, money returns to their wallet)", type: "textarea", required: true, maxlength: 300 }] });
      if (!vals) return;
      const r2 = await App.call(`/admin/wallets/withdrawals/${b.dataset.wdNo}/reject`, { method: "POST", body: { reason: vals.reason } });
      if (r2) { UI.toast("Withdrawal rejected; funds returned."); paint(); }
    }));
  };
  await paint();
} });
