(async () => {
  if (!(await App.mountShell({ active: "orders", requireLogin: true }))) return;
  const root = document.getElementById("page");
  root.innerHTML = `<h1 class="h4">My orders</h1><div id="list">${App.skeletons(3)}</div>`;
  async function load() {
    let list = await App.call("/orders"); if (!list) return;
    // Customers who paid but were never redirected back still show as unpaid: re-check with the
    // provider for every payable order, then re-fetch so the list reflects confirmed payments.
    const payable = list.filter((o) => o.can_pay);
    if (payable.length) {
      await Promise.all(payable.map((o) => App.api(`/payments/orders/${o.id}/verify`)));
      const fresh = await App.call("/orders");
      if (fresh) list = fresh;
    }
    document.getElementById("list").innerHTML = list.length ? list.map((o) => `
      <a class="panel d-block text-decoration-none text-reset mb-2" href="/order.html?id=${o.id}"><div class="d-flex justify-content-between flex-wrap gap-1">
        <strong>${App.esc(o.vendor.name)}</strong><span>${App.fulfilPill(o)} ${App.statusPill(o.status)}</span></div>
        <div class="meta">${App.esc(o.code)} &middot; ${App.fmtDate(o.created_at)}</div>
        <div class="meta">${o.items.map((i) => `${i.quantity}x ${App.esc(i.name)}`).join(", ")}</div>
        ${o.status === "RIDER_ARRIVED" && o.rider && o.rider.wait_until ? `<div class="meta mt-1"><i class="bi bi-hourglass-split"></i> <strong>Your rider is waiting for you</strong> — <span class="wait-cd" data-until="${o.rider.wait_until}"></span> left</div>` : ""}
        <div class="d-flex justify-content-between mt-1"><span class="price">${App.money(o.total)}</span>${o.can_pay ? '<span class="status-pill warn">Pay now</span>' : ""}</div></a>`).join("")
      : App.empty("bi-receipt", "No orders yet", "When you order, it shows up here.");
  }
  load();
  setInterval(load, 20000);   // keep statuses fresh
  setInterval(() => document.querySelectorAll(".wait-cd").forEach((s) => {   // live countdown on waiting orders
    const left = Math.max(0, Math.ceil((new Date(s.dataset.until) - Date.now()) / 1000));
    s.textContent = left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "time's up";
  }), 1000);
})();
