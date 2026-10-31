/* A shared group order at /group/ABC123. Everyone adds their own food; the organizer checks out. */
(async () => {
  const code = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "").toUpperCase();
  const user = await App.mountShell({ requireLogin: true });   // sends guests to log in, then back here
  if (!user) return;
  const root = document.getElementById("page");
  root.innerHTML = App.skeletons(1);
  let g = null, menu = null, timer = null;

  async function join() {
    const r = await App.api(`/groups/${code}/join`, { method: "POST" });
    if (r.ok) return r.data;
    if (r.data?.detail?.code === "group_closed" || r.data?.detail?.code === "group_expired") { const v = await App.api(`/groups/${code}`); if (v.ok) return v.data; }
    root.innerHTML = App.empty("bi-people", App.errorMessage(r), "Ask the organizer for a new link.") + '<div class="text-center"><a class="btn btn-dark" href="/">Go home</a></div>';
    return null;
  }
  const open = () => g.status === "OPEN";

  function render() {
    const url = location.origin + g.share_path;
    const byMember = {};
    g.items.forEach((i) => (byMember[i.member] ||= []).push(i));
    root.innerHTML = `<div class="d-flex justify-content-between flex-wrap gap-2 align-items-start"><div><h1 class="h4 mb-0">${App.esc(g.name)}</h1>
        <div class="meta">Ordering from <a href="/vendor.html?id=${g.vendor.id}">${App.esc(g.vendor.name)}</a> &middot; organised by ${App.esc(g.organizer)}</div></div>
        <span class="status-pill ${open() ? "" : "bad"}">${g.status.replace("_", " ")}</span></div>
      ${open() ? `<div class="panel my-3"><div class="meta mb-1">Share this link so friends can add their food:</div>
        <div class="input-group mb-2"><input class="form-control" readonly value="${App.esc(url)}" id="share-url" aria-label="Group order link"><button class="btn btn-dark" id="copy">Copy</button></div>
        <div class="text-center mb-2"><img src="/api/groups/${code}/qr.svg" width="132" height="132" alt="QR code to join this group order" onerror="this.remove()"></div>
        <div class="d-flex gap-2 flex-wrap"><a class="btn btn-sm btn-success" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(`Join my group order at ${g.vendor.name}: ${url}`)}"><i class="bi bi-whatsapp"></i> WhatsApp</a>
          ${navigator.share ? '<button class="btn btn-sm btn-outline-dark" id="share"><i class="bi bi-share"></i> Share</button>' : ""}</div></div>` : ""}
      <div class="mb-3">${g.members.map((m) => `<span class="status-pill me-1">${App.esc(m.name)}${m.is_organizer ? " (organizer)" : ""}</span>`).join("")}</div>
      <div class="row g-3"><div class="col-lg-7">${open() ? `<div id="gai"></div><h2 class="h6">Add your food</h2><div class="card-grid" id="gmenu" style="grid-template-columns:repeat(auto-fill,minmax(210px,1fr))">${App.skeletons(2)}</div>` : ""}</div>
      <div class="col-lg-5"><div class="panel"><h2 class="h6">Everyone's order</h2>
        ${g.items.length ? Object.entries(byMember).map(([name, lines]) => `<div class="mb-2"><strong>${App.esc(name)}</strong>${lines.map((l) => `
          <div class="d-flex justify-content-between align-items-center small py-1"><span>${l.quantity} x ${App.esc(l.name)}${l.special_instructions ? `<div class="meta">${App.esc(l.special_instructions)}</div>` : ""}</span>
            <span class="text-nowrap">${App.money(l.line_total)} ${open() && (l.mine || g.is_organizer) ? `<button class="btn btn-sm btn-link text-danger p-0 ms-1" data-rm="${l.id}" aria-label="Remove ${App.esc(l.name)}"><i class="bi bi-x-lg"></i></button>` : ""}</span></div>`).join("")}</div>`).join("") : '<p class="meta mb-0">Nobody has added anything yet.</p>'}
        <div class="summary-row total"><span>Food total</span><span>${App.money(g.subtotal)}</span></div>
        <div class="meta">Delivery and any discounts are added at checkout.</div>
        ${g.is_organizer && open() ? `<button class="btn btn-dark w-100 mt-3" id="checkout" ${g.items.length ? "" : "disabled"}>Checkout for everyone</button><button class="btn btn-link text-danger w-100" id="close">Close this group order</button>` : ""}
        ${!g.is_organizer && open() ? `<p class="meta mt-2 mb-0">${App.esc(g.organizer)} will check out and pay for the group.</p>` : ""}
        ${g.status === "CHECKED_OUT" ? `<p class="meta mt-2 mb-0">${g.is_organizer ? '<a href="/checkout.html">Continue to checkout</a>' : "The organizer has checked out."}</p>` : ""}</div></div></div>`;
    if (open()) { renderMenu(); GroupAI.mount(document.getElementById("gai"), g, code, async () => { const r = await App.api(`/groups/${code}`); if (r.ok) { g = r.data; render(); } }); }
  }
  async function renderMenu() {
    menu ||= await App.call(`/vendors/${g.vendor.id}`);
    const el = document.getElementById("gmenu"); if (!el || !menu) return;
    const items = menu.menu.flatMap((s) => s.items).filter((i) => i.is_available);
    el.innerHTML = items.length ? items.map((i) => `<div class="i-card"><div class="card-body-s"><h3>${App.esc(i.name)}</h3><div class="meta">${i.is_spicy ? '<span class="badge-spicy">Spicy</span> ' : ""}${i.prep_minutes} min</div>
      <div class="d-flex justify-content-between align-items-center mt-auto pt-2"><span class="price">${App.money(i.price)}</span><button class="btn-add" data-add="${i.id}">Add</button></div></div></div>`).join("") : App.empty("bi-egg-fried", "Nothing available right now", "");
  }
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const sync = (v) => { if (v) { g = v; render(); } };
    if (t.dataset.add) sync(await App.call(`/groups/${code}/items`, { method: "POST", body: { menu_item_id: Number(t.dataset.add), quantity: 1 } }));
    else if (t.closest("[data-rm]") || t.dataset.rm) sync(await App.call(`/groups/${code}/items/${(t.closest("[data-rm]") || t).dataset.rm}`, { method: "DELETE" }));
    else if (t.id === "copy") { try { await navigator.clipboard.writeText(document.getElementById("share-url").value); UI.toast("Link copied."); } catch (_) { document.getElementById("share-url").select(); } }
    else if (t.id === "share") navigator.share({ title: g.name, text: `Join my group order at ${g.vendor.name}`, url: location.origin + g.share_path }).catch(() => {});
    else if (t.id === "checkout") { const c = await App.call(`/groups/${code}/checkout`, { method: "POST" }); if (c) location.assign("/checkout.html"); }
    else if (t.id === "close" && confirm("Close this group order for everyone?")) { if (await App.call(`/groups/${code}/close`, { method: "POST" })) location.reload(); }
  });

  g = await join(); if (!g) return;
  render();
  timer = setInterval(async () => { if (!open()) return clearInterval(timer); const r = await App.api(`/groups/${code}`); if (r.ok && JSON.stringify(r.data.items) !== JSON.stringify(g.items) || (r.ok && r.data.members.length !== g.members.length)) { g = r.data; render(); } }, 6000);
})();
