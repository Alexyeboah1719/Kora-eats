Dash.register("admin", { id: "orders", label: "Orders", icon: "bi-receipt", async render(el) {
  let status = "", q = "";
  async function load() {
    const list = await App.call("/admin/orders", { params: { status, q } }); if (!list) return;
    el.querySelector("#rows").innerHTML = list.length ? Dash.table(["Order", "Status", "Vendor", "Customer", "Total", "Placed", ""], list.map((o) => `<tr><td><strong>${App.esc(o.code)}</strong>${o.scheduled_for ? '<div class="meta">scheduled</div>' : ""}</td><td>${App.fulfilPill({ fulfillment_type: o.fulfillment })} ${App.statusPill(o.status)}</td><td>${App.esc(o.vendor)}</td><td>${App.esc(o.customer)}</td><td>${App.money(o.total)}</td><td class="small">${App.fmtDate(o.created_at)}</td><td class="text-end"><button class="btn btn-sm btn-outline-dark" data-open="${o.id}">Open</button></td></tr>`)) : App.empty("bi-receipt", "No orders found", "");
  }
  el.innerHTML = `<div class="d-flex gap-2 flex-wrap mb-3"><input class="form-control w-auto" id="q" placeholder="Order code or customer email" aria-label="Search orders">
    <select class="form-select w-auto" id="st" aria-label="Status"><option value="">All statuses</option>${Object.keys(App.STATUS_LABEL).map((s) => `<option value="${s}">${App.STATUS_LABEL[s]}</option>`).join("")}</select></div><div id="rows"></div>`;
  el.querySelector("#q").addEventListener("input", (e) => { clearTimeout(el._t); el._t = setTimeout(() => { q = e.target.value.trim(); load(); }, 300); });
  el.querySelector("#st").addEventListener("change", (e) => { status = e.target.value; load(); });
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open]"); if (!t) return;
    const o = await App.call(`/admin/orders/${t.dataset.open}`); if (!o) return;
    const isPickup = o.fulfillment_type === "PICKUP";
    const next = { PAID: ["CONFIRMED", "CANCELLED", "REFUNDED"], CANCELLED: ["REFUNDED"], DELIVERED: ["REFUNDED"], PENDING_PAYMENT: ["CANCELLED"], PAYMENT_FAILED: ["CANCELLED"], CONFIRMED: ["PREPARING", "CANCELLED"], PREPARING: ["READY_FOR_PICKUP", "CANCELLED"], READY_FOR_PICKUP: isPickup ? ["COLLECTED", "CANCELLED"] : ["CANCELLED"], COLLECTED: ["REFUNDED"] }[o.status] || [];
    const f = await Dash.formModal({ title: `Order ${o.code} (${App.STATUS_LABEL[o.status]})`, submitLabel: "Change status", fields: [
      { name: "info", label: `${o.items.map((i) => `${i.quantity}x ${i.name}`).join(", ")} | ${App.money(o.total)} | ${o.vendor.name} | ${o.delivery_address}`, type: "textarea", value: "", required: false },
      { name: "status", label: "New status", type: "select", options: [{ value: "", label: next.length ? "Keep as is" : "No changes possible" }, ...next.map((s) => ({ value: s, label: App.STATUS_LABEL[s] }))] },
      { name: "note", label: "Note (e.g. 'Refunded via MoMo dashboard, ref 123')", type: "text" }] });
    if (f && f.status) { if (f.status === "REFUNDED" && !confirm("REFUNDED only records the refund. Have you returned the money to the customer through your payment provider?")) return;
      if (await App.call(`/admin/orders/${o.id}/status`, { method: "POST", body: { status: f.status, note: f.note || null } })) { UI.toast("Order updated."); load(); } }
  });
  load();
} });
