(async () => {
  if (!(await App.mountShell({ active: "orders", requireLogin: true }))) return;
  const root = document.getElementById("page");
  root.innerHTML = `<h1 class="h4">My orders</h1><div id="list">${App.skeletons(3)}</div>`;
  async function load() {
    const list = await App.call("/orders"); if (!list) return;
    document.getElementById("list").innerHTML = list.length ? list.map((o) => `
      <a class="panel d-block text-decoration-none text-reset mb-2" href="/order.html?id=${o.id}"><div class="d-flex justify-content-between flex-wrap gap-1">
        <strong>${App.esc(o.vendor.name)}</strong><span>${App.fulfilPill(o)} ${App.statusPill(o.status)}</span></div>
        <div class="meta">${App.esc(o.code)} &middot; ${App.fmtDate(o.created_at)}</div>
        <div class="meta">${o.items.map((i) => `${i.quantity}x ${App.esc(i.name)}`).join(", ")}</div>
        <div class="d-flex justify-content-between mt-1"><span class="price">${App.money(o.total)}</span>${o.can_pay ? '<span class="status-pill warn">Pay now</span>' : ""}</div></a>`).join("")
      : App.empty("bi-receipt", "No orders yet", "When you order, it shows up here.");
  }
  load();
  setInterval(load, 20000);   // keep statuses fresh
})();
