Dash.register("admin", { id: "reports", label: "Reports & audit", icon: "bi-file-earmark-bar-graph", async render(el) {
  const audit = await App.call("/admin/audit", { params: { limit: 60 } }); if (!audit) return;
  el.innerHTML = `<h2 class="h6">Download reports (CSV)</h2><div class="d-flex gap-2 flex-wrap mb-4">
      <a class="btn btn-outline-dark" href="/api/admin/reports/sales.csv"><i class="bi bi-download"></i> Sales</a><a class="btn btn-outline-dark" href="/api/admin/reports/payments.csv"><i class="bi bi-download"></i> Payments</a><a class="btn btn-outline-dark" href="/api/admin/reports/vendors.csv"><i class="bi bi-download"></i> Vendors</a></div>
    <h2 class="h6">Recent security &amp; admin activity</h2>` + Dash.table(["When", "Action", "User", "IP", "Detail"], audit.map((a) => `<tr><td class="small text-nowrap">${App.fmtDate(a.at)}</td><td>${App.esc(a.action)}</td><td>${App.esc(a.user || "")}</td><td class="small">${App.esc(a.ip || "")}</td><td class="small">${App.esc(a.detail || "")}</td></tr>`));
} });
