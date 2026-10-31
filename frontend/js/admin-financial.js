/* Admin dashboard: Financial Management section with platform revenue overview. */
Dash.register("admin", { id: "financial", label: "Financial Management", icon: "bi-currency-dollar", async render(el) {
  let data = null;
  let days = 30;

  async function load() {
    el.innerHTML = '<div class="text-center py-5"><div class="spinner-border"></div></div>';
    data = await App.call("/admin/financial-overview", { params: { days } });
    if (!data) return;

    el.innerHTML = `
      <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
        <h2 class="h6 mb-0">Financial Overview</h2>
        <select class="form-select form-select-sm w-auto" id="period-select">
          <option value="7" ${days === 7 ? "selected" : ""}>Last 7 days</option>
          <option value="30" ${days === 30 ? "selected" : ""}>Last 30 days</option>
          <option value="90" ${days === 90 ? "selected" : ""}>Last 90 days</option>
          <option value="365" ${days === 365 ? "selected" : ""}>Last year</option>
        </select>
      </div>

      <h2 class="h6 mb-3">Platform Revenue Overview</h2>
      <div class="stat-grid mb-4">
        ${Dash.stat(App.money(data.total_orders_revenue), "Total orders revenue")}
        ${Dash.stat(App.money(data.vendor_commission_revenue), "Vendor commissions")}
        ${Dash.stat(App.money(data.rider_commission_revenue), "Rider commissions")}
        ${Dash.stat(App.money(data.total_platform_revenue), "Total platform revenue", "success")}
      </div>

      <div class="row g-3 mb-4">
        <div class="col-md-6">
          <div class="panel h-100">
            <h2 class="h6">Commission Rates</h2>
            <div class="mb-2">Vendor commission: <strong>${data.vendor_commission_rate}%</strong></div>
            <div>Rider commission: <strong>${data.rider_commission_rate}%</strong></div>
          </div>
        </div>
        <div class="col-md-6">
          <div class="panel h-100">
            <h2 class="h6">Platform Wallet</h2>
            <div class="mb-2">Total balance: <strong>${App.money(data.platform_balance)}</strong></div>
            <div>Withdrawable: <strong>${App.money(data.platform_withdrawable)}</strong></div>
          </div>
        </div>
      </div>

      <div class="row g-3 mb-4">
        <div class="col-md-6">
          <div class="panel h-100">
            <h2 class="h6">Revenue Breakdown</h2>
            <div class="mb-2">Vendor commissions: <strong>${App.money(data.vendor_commission_revenue)}</strong></div>
            <div class="mb-2">Rider commissions: <strong>${App.money(data.rider_commission_revenue)}</strong></div>
            <div class="mb-2">Platform discount costs: <strong class="text-danger">-${App.money(data.platform_discount_cost)}</strong></div>
            <div class="mb-2">Withdrawal fees: <strong>${App.money(data.withdrawal_fees)}</strong></div>
            <div class="mt-2 pt-2 border-top">Net platform revenue: <strong class="text-success">${App.money(data.total_platform_revenue)}</strong></div>
          </div>
        </div>
        <div class="col-md-6">
          <div class="panel h-100">
            <h2 class="h6">Withdrawal Statistics</h2>
            <div class="mb-2">Total withdrawals: <strong>${data.total_withdrawals}</strong></div>
            <div class="mb-2">Pending: <strong class="text-warning">${data.pending_withdrawals}</strong></div>
            <div class="mb-2">Failed: <strong class="text-danger">${data.failed_withdrawals}</strong></div>
            <div>Successful: <strong class="text-success">${data.successful_withdrawals}</strong></div>
          </div>
        </div>
      </div>

      <h2 class="h6 mb-3">Recent Withdrawal Requests</h2>
      ${data.recent_withdrawals.length ? `
        <div class="table-responsive">
          <table class="table table-sm">
            <thead><tr><th>Date</th><th>Amount</th><th>Owner Type</th><th>Status</th><th>Reference</th></tr></thead>
            <tbody>
              ${data.recent_withdrawals.map((w) => `
                <tr>
                  <td>${new Date(w.requested_at).toLocaleDateString()}</td>
                  <td>${App.money(w.amount)}</td>
                  <td><span class="status-pill">${w.owner_type}</span></td>
                  <td><span class="status-pill ${w.status === "SUCCESS" ? "ok" : w.status === "FAILED" ? "bad" : "warn"}">${w.status}</span></td>
                  <td class="small font-monospace">${w.reference.slice(0, 12)}...</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : '<p class="meta">No withdrawal requests yet.</p>'}
    `;
  }

  el.addEventListener("change", (e) => {
    if (e.target.id === "period-select") {
      days = Number(e.target.value);
      load();
    }
  });

  await load();
} });
