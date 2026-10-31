/* Shared wallet dashboard for vendors and riders. */
(() => {
  const money = App.money;
  const esc = App.esc;

  const statusLabel = {
    PENDING_APPROVAL: "Awaiting admin approval",
    PROCESSING: "Processing",
    SUCCESS: "Successful",
    FAILED: "Failed",
    REVERSED: "Reversed",
    REJECTED: "Rejected",
  };

  const statusBadge = (status) => {
    const good = status === "SUCCESS";
    const bad = ["FAILED", "REVERSED", "REJECTED"].includes(status);
    return `<span class="badge ${good ? "text-bg-success" : bad ? "text-bg-danger" : "text-bg-warning"}">${esc(statusLabel[status] || status || "Unknown")}</span>`;
  };

  function registerWallet(area) {
    Dash.register(area, {
      id: "wallet",
      label: "Wallet",
      icon: "bi-wallet2",

      async render(el) {
        const base = `/${area}/wallet`;

        async function load() {
          el.innerHTML = App.skeletons(3);

          const [summary, statement, upcoming, withdrawals, banks] = await Promise.all([
            App.call(base),
            App.call(base + "/statement"),
            App.call(base + "/upcoming"),
            App.call(base + "/withdrawals"),
            App.call(base + "/banks"),
          ]);

          if (!summary) {
            el.innerHTML = App.empty("bi-wallet2", "Wallet unavailable", "Please try again in a moment.");
            return;
          }

          const b = summary.balance || {};
          const eligibility = summary.eligibility || {};
          const payout = summary.payout_account;
          const recent = summary.recent_withdrawals || withdrawals || [];
          const cash_in_hand = b.cash_in_hand || 0;
          const max_cash = 300; // Configured in backend
          const cash_warning = cash_in_hand > max_cash * 0.8;
          const cash_blocked = cash_in_hand >= max_cash;
          const needs_topup = cash_in_hand < 0;

          el.innerHTML = `
            <div class="row g-3 mb-4">
              ${[
                ["Available balance", b.available],
                ["Pending balance", b.pending],
                ["Withdrawable now", b.withdrawable],
                ["Lifetime earnings", b.lifetime_earnings],
                ["Total withdrawn", b.withdrawn],
                ["Cash to hand over", cash_in_hand],
              ].map(([label, value]) => `
                <div class="col-6 col-lg-4">
                  <div class="stat">
                    <div class="n ${label === "Cash to hand over" && value < 0 ? "text-danger" : ""}">${money(value)}</div>
                    <div class="l">${esc(label)}</div>
                  </div>
                </div>`).join("")}
            </div>

            ${cash_in_hand !== 0 ? `
            <div class="alert ${cash_blocked ? "alert-danger" : cash_warning ? "alert-warning" : "alert-success"} mb-4">
              ${cash_blocked ? `<i class="bi bi-exclamation-triangle"></i> You have reached the cash limit (${money(max_cash)}). You cannot take new pay-on-delivery orders until you remit this cash to KORA EATS.` : 
               cash_warning ? `<i class="bi bi-exclamation-circle"></i> Approaching cash limit (${money(max_cash)}). Remit cash soon to avoid being blocked from COD orders.` :
               `<i class="bi bi-check-circle"></i> Within cash limit (${money(max_cash)}). You can take pay-on-delivery orders.`}
              ${cash_in_hand < 0 ? `<div class="mt-2 text-info"><i class="bi bi-info-circle"></i> Your earnings from non-COD orders will automatically clear this debt.</div>` : ""}
            </div>` : ""}

            ${needs_topup ? `
            <div class="alert alert-info mb-4">
              <div class="d-flex flex-wrap gap-2 align-items-center">
                <div><i class="bi bi-credit-card"></i> You have a negative cash balance. Top up to clear your debt and start receiving COD orders again.</div>
                <button class="btn btn-warning btn-sm" id="wallet-topup">Top up now</button>
              </div>
            </div>` : ""}

            <div class="row g-3 mb-4">
              <div class="col-lg-6">
                <section class="card h-100">
                  <div class="card-body">
                    <h2 class="h6">Payout account</h2>
                    ${payout ? `
                      <p class="mb-1"><strong>${esc(payout.provider_name || payout.provider_code)}</strong></p>
                      <p class="mb-1">${esc(payout.account_name)}</p>
                      <p class="text-muted mb-3">${esc(payout.account_number_masked || "")}</p>
                      <p class="small text-muted">Changing your payout account may pause withdrawals during the security cooldown.</p>
                    ` : `<p class="text-muted">No payout account has been added yet.</p>`}
                    <button class="btn btn-outline-dark btn-sm" id="wallet-payout">
                      ${payout ? "Change payout account" : "Add payout account"}
                    </button>
                  </div>
                </section>
              </div>

              <div class="col-lg-6">
                <section class="card h-100">
                  <div class="card-body">
                    <h2 class="h6">Withdraw funds</h2>
                    <p class="mb-1">Minimum: ${money(eligibility.minimum)}</p>
                    <p class="mb-1">Available to request: ${money(eligibility.max_now)}</p>
                    <p class="mb-3">Fee: ${money(eligibility.fee)}</p>
                    ${(eligibility.reasons || []).map(reason =>
                      `<p class="small text-danger mb-1">${esc(reason)}</p>`
                    ).join("")}
                    ${(eligibility.info || []).map(info =>
                      `<p class="small text-muted mb-1">${esc(info)}</p>`
                    ).join("")}
                    <button class="btn btn-dark btn-sm mt-2" id="wallet-withdraw"
                      ${eligibility.can_withdraw ? "" : "disabled"}>
                      Request withdrawal
                    </button>
                  </div>
                </section>
              </div>
            </div>

            <section class="card mb-4">
              <div class="card-body">
                <h2 class="h6">Recent withdrawals</h2>
                ${Array.isArray(recent) && recent.length ? Dash.table(
                  ["Date", "Amount", "You receive", "Status"],
                  recent.map(w => `<tr>
                    <td>${esc(w.requested_at ? App.fmtDate(w.requested_at) : "—")}</td>
                    <td>${money(w.amount)}</td>
                    <td>${money(w.you_receive)}</td>
                    <td>${statusBadge(w.status)}</td>
                  </tr>`)
                ) : App.empty("bi-cash-stack", "No withdrawals yet", "")}
              </div>
            </section>

            <section class="card mb-4">
              <div class="card-body">
                <h2 class="h6">Upcoming earnings</h2>
                ${upcoming && Array.isArray(upcoming.orders) && upcoming.orders.length ? Dash.table(
                  ["Order", "Status", "Expected earnings", "Notes"],
                  upcoming.orders.map(o => `<tr>
                    <td>${esc(o.code || o.order_id)}</td>
                    <td>${esc(o.status || "—")}</td>
                    <td>${money(o.you_will_earn)}</td>
                    <td>${esc((o.notes || []).join(" "))}</td>
                  </tr>`)
                ) : `<p class="text-muted mb-0">No upcoming earnings.</p>`}
              </div>
            </section>

            <section class="card">
              <div class="card-body">
                <h2 class="h6">Earnings statement</h2>
                ${statement && Array.isArray(statement.orders) && statement.orders.length ? Dash.table(
                  ["Order", "Date", "Order status", "Your earnings", "Wallet status"],
                  statement.orders.map(o => `<tr>
                    <td>${esc(o.code || o.order_id)}</td>
                    <td>${esc(o.date ? App.fmtDate(o.date) : "—")}</td>
                    <td>${esc(o.order_status || "—")}</td>
                    <td>${money(o.you_earn)}</td>
                    <td>${esc(o.state || "—")}</td>
                  </tr>`)
                ) : `<p class="text-muted mb-0">No statement entries yet.</p>`}
              </div>
            </section>
          `;

          el.querySelector("#wallet-payout").addEventListener("click", async () => {
            if (!banks) {
              UI.toast("Unable to load payout providers. Please try again.", "error");
              return;
            }

            const typeResult = await Dash.formModal({
              title: "Choose payout account type",
              fields: [{
                name: "type",
                label: "Account type",
                type: "select",
                value: payout?.type || "MOBILE_MONEY",
                options: [
                  { value: "MOBILE_MONEY", label: "Mobile Money" },
                  { value: "BANK", label: "Bank account" },
                ],
                required: true,
              }],
              submitLabel: "Continue",
            });
            if (!typeResult) return;

            const providers = typeResult.type === "MOBILE_MONEY"
              ? (banks.mobile_money || [])
              : (banks.banks || []);

            if (!providers.length) {
              UI.toast("No providers are currently available for that account type.", "error");
              return;
            }

            const providerResult = await Dash.formModal({
              title: "Payout provider",
              fields: [{
                name: "provider_code",
                label: "Provider",
                type: "select",
                options: providers.map(p => ({ value: p.code, label: p.name })),
                required: true,
              }],
              submitLabel: "Continue",
            });
            if (!providerResult) return;

            const accountResult = await Dash.formModal({
              title: "Payout account details",
              fields: [
                { name: "account_name", label: "Account holder name", required: true, maxlength: 100 },
                { name: "account_number", label: typeResult.type === "MOBILE_MONEY" ? "Mobile Money number" : "Bank account number", required: true, maxlength: 32 },
              ],
              submitLabel: "Save payout account",
            });
            if (!accountResult) return;

            const saved = await App.call(base + "/payout-account", {
              method: "PUT",
              body: {
                type: typeResult.type,
                provider_code: providerResult.provider_code,
                account_name: accountResult.account_name,
                account_number: accountResult.account_number,
              },
            });

            if (saved) {
              UI.toast("Payout account saved.", "success");
              await load();
            }
          });

          el.querySelector("#wallet-withdraw").addEventListener("click", async () => {
            const amountResult = await Dash.formModal({
              title: "Request withdrawal",
              fields: [{
                name: "amount",
                label: "Amount (GH₵)",
                type: "number",
                min: eligibility.minimum || 20,
                step: "0.01",
                required: true,
                hint: `Maximum currently available: ${money(eligibility.max_now)}. Larger requests may require admin approval.`,
              }],
              submitLabel: "Submit request",
            });
            if (!amountResult) return;

            const amount = Number(amountResult.amount);
            if (!Number.isFinite(amount) || amount <= 0 || amount > Number(eligibility.max_now || 0)) {
              UI.toast("Enter a valid amount within your currently available withdrawal limit.", "error");
              return;
            }

            const key = globalThis.crypto?.randomUUID
              ? globalThis.crypto.randomUUID()
              : "wallet-" + Date.now() + "-" + Math.random().toString(36).slice(2);

            const result = await App.call(base + "/withdrawals", {
              method: "POST",
              body: { amount: amount.toFixed(2) },
              idempotencyKey: key,
            });

            if (result) {
              UI.toast("Withdrawal request submitted.", "success");
              await load();
            }
          });

          // Top-up button handler
          if (needs_topup) {
            el.querySelector("#wallet-topup").addEventListener("click", async () => {
              const amount = Math.abs(cash_in_hand) + 50; // Add buffer
              const amountResult = await Dash.formModal({
                title: "Top up to clear balance",
                fields: [{
                  name: "amount",
                  label: "Top-up amount (GH₵)",
                  type: "number",
                  min: Math.abs(cash_in_hand),
                  step: "0.01",
                  required: true,
                  hint: `Minimum: ${money(Math.abs(cash_in_hand))}. This will clear your negative COD cash balance.`,
                  value: amount.toFixed(2),
                }],
                submitLabel: "Pay with Mobile Money",
              });
              if (!amountResult) return;

              const numAmount = Number(amountResult.amount);
              if (!Number.isFinite(numAmount) || numAmount < Math.abs(cash_in_hand)) {
                UI.toast("Invalid amount. Must be at least " + money(Math.abs(cash_in_hand)), "error");
                return;
              }

              const btn = el.querySelector("#wallet-topup");
              btn.disabled = true;
              btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Processing...';

              const r = await App.call("/rider/topup/initiate", { method: "POST", params: { amount: numAmount } });
              if (!r.ok) {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-credit-card"></i> Top up now';
                return UI.toast(App.errorMessage(r), "error");
              }

              // Redirect to Paystack
              location.assign(r.data.checkout_url);
            });
          }
        }

        await load();
      },
    });
  }

  registerWallet("vendor");
  // Riders use the wallet tab from rider-dash.js instead (which has COD-specific features)
  // registerWallet("rider");
})();
