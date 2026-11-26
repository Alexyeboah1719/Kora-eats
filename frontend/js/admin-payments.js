Dash.register("admin", { id: "payments", label: "Payments", icon: "bi-credit-card", async render(el) {
  const list = await App.call("/admin/payments"); if (!list) return;
  el.innerHTML = `<p class="meta">Every payment attempt. An order is only marked paid after the backend verifies it with the payment provider.</p>` + (list.length ? Dash.table(["When", "Order", "Reference", "Provider", "Amount", "Status", "Note"], list.map((p) => `<tr><td class="small">${App.fmtDate(p.created_at)}</td><td>${App.esc(p.order_code)}</td><td class="small text-break">${App.esc(p.reference)}</td><td>${App.esc(p.provider)} / ${App.esc(p.method.replace("_", " ").toLowerCase())}</td><td>${App.money(p.amount)}</td>
    <td><span class="status-pill ${p.status === "FAILED" ? "bad" : p.status === "PENDING" ? "warn" : ""}">${p.status}</span></td><td class="small">${App.esc(p.failure_reason || "")}</td></tr>`)) : App.empty("bi-credit-card", "No payments yet", ""));
} });
