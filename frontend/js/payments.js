(async () => {
  if (!(await App.mountShell({ active: "profile", requireLogin: true }))) return;
  const root = document.getElementById("page");
  root.innerHTML = `<h1 class="h4">Payment history</h1><div id="list">${App.skeletons(2)}</div>`;
  const list = await App.call("/payments"); if (!list) return;
  const cls = { SUCCESS: "", PENDING: "warn", FAILED: "bad" };
  document.getElementById("list").innerHTML = list.length ? list.map((p) => `<a class="panel d-block text-decoration-none text-reset mb-2" href="/order.html?id=${p.order_id}">
    <div class="d-flex justify-content-between"><strong>${App.esc(p.order_code)}</strong><span class="status-pill ${cls[p.status]}">${p.status}</span></div>
    <div class="meta">${App.fmtDate(p.created_at)} &middot; ${p.method.replace("_", " ")}</div><div class="price">${App.money(p.amount)}</div></a>`).join("")
    : App.empty("bi-credit-card", "No payments yet", "Your payments will appear here.");
})();
