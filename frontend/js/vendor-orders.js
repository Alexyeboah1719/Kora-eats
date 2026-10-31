/* Vendor dashboard: live orders board with accept / reject / status buttons. */
Dash.register("vendor", { id: "orders", label: "Orders", icon: "bi-receipt", async render(el) {
  let view = "active", timer = null, lastCount = -1;
  const NEXT = { PAID: [["CONFIRMED", "Accept", "btn-success"]], CONFIRMED: [["PREPARING", "Start preparing", "btn-dark"]], PREPARING: [["READY_FOR_PICKUP", "Mark ready", "btn-dark"]] };
  async function load(quiet) {
    const list = await App.call(`/vendor/orders`, { params: { view } }); if (!list) return; el._orders = list;
    if (view === "active" && lastCount >= 0 && list.filter((o) => o.status === "PAID").length > lastCount && quiet) UI.toast("New order received!");
    lastCount = list.filter((o) => o.status === "PAID").length;
    el.innerHTML = `<div class="d-flex gap-2 mb-3"><button class="pill-btn ${view === "active" ? "active" : ""}" data-view="active">Active</button><button class="pill-btn ${view === "history" ? "active" : ""}" data-view="history">History</button></div>` +
      (list.length ? list.map((o) => `<div class="panel mb-2"><div class="d-flex justify-content-between flex-wrap gap-1"><strong>${App.esc(o.code)}</strong><span>${App.fulfilPill(o)} ${App.statusPill(o.status)}</span></div>
        <div class="meta">${App.fmtDate(o.created_at)}${o.scheduled_for ? ` &middot; <strong>Scheduled for ${App.fmtDate(o.scheduled_for)}</strong>` : ""}</div>
        <ul class="my-2 ps-3">${o.items.map((i) => `<li><strong>${i.quantity}x</strong> ${App.esc(i.name)}${i.special_instructions ? ` <em class="meta">(${App.esc(i.special_instructions)})</em>` : ""}</li>`).join("")}</ul>
        ${o.notes ? `<div class="meta">Note: ${App.esc(o.notes)}</div>` : ""}
        <div class="meta"><i class="bi bi-telephone"></i> ${App.esc(o.customer_phone)}${o.fulfillment_type === "PICKUP" ? " &middot; <strong>Customer collects from you</strong>" : " &middot; " + App.esc(o.delivery_address)}</div>
        ${o.rider ? `<div class="meta"><i class="bi bi-bicycle"></i> Rider: ${App.esc(o.rider.name)} ${App.esc(o.rider.phone || "")}</div>` : ""}
        <div class="d-flex justify-content-between align-items-center mt-2"><strong>${App.money(o.total)}</strong>
          <div class="d-flex gap-2">${(NEXT[o.status] || []).map(([s, l, c]) => `<button class="btn btn-sm ${c}" data-id="${o.id}" data-s="${s}">${l}</button>`).join("")}
          ${o.fulfillment_type === "PICKUP" && o.status === "READY_FOR_PICKUP" ? `<button class="btn btn-sm btn-success" data-id="${o.id}" data-collect="1">Hand over (enter code)</button>` : ""}
          ${["PAID", "CONFIRMED"].includes(o.status) ? `<button class="btn btn-sm btn-outline-danger" data-id="${o.id}" data-reject="1">Reject</button>` : ""}</div></div></div>`).join("")
        : App.empty("bi-receipt", view === "active" ? "No active orders" : "No past orders", "New orders appear here automatically."));
  }
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.view) { view = t.dataset.view; return load(); }
    if (t.dataset.collect) {
      const o = (el._orders || []).find((x) => x.id === Number(t.dataset.id));
      const code = await Scanner.promptCode({ orderCode: o ? o.code : "this order" });
      if (code && (await App.call(`/vendor/orders/${t.dataset.id}/collect`, { method: "POST", body: { code } }))) { UI.toast("Order handed over."); load(); }
    } else if (t.dataset.reject) {
      const f = await Dash.formModal({ title: "Reject order", submitLabel: "Reject order", fields: [{ name: "note", label: "Reason (shown to the customer)", type: "textarea", required: true }] });
      if (f && (await App.call(`/vendor/orders/${t.dataset.id}/status`, { method: "POST", body: { status: "CANCELLED", note: f.note } }))) { UI.toast("Order rejected. The customer will be refunded."); load(); }
    } else if (t.dataset.s && (await App.call(`/vendor/orders/${t.dataset.id}/status`, { method: "POST", body: { status: t.dataset.s } }))) { UI.toast("Order updated."); load(); }
  });
  await load();
  timer = setInterval(() => load(true), 15000);
  this.teardown = () => clearInterval(timer);   // dash.js calls this when you leave the section
} });
