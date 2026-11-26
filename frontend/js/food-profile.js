(async () => {
  const user = await App.mountShell({ active: "profile", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  const [tx, p, mem] = await Promise.all([App.call("/taxonomy"), App.call("/me/food-profile"), App.call("/me/food-memory")]);
  if (!tx || !p) return;
  const checks = (name, list, have) => list.map((o) => `<div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" id="${name}-${o.id}" data-grp="${name}" value="${o.id}" ${have.includes(o.id) ? "checked" : ""}><label class="form-check-label" for="${name}-${o.id}">${App.esc(o.label)}</label></div>`).join("");
  root.innerHTML = `<h1 class="h4">My food profile</h1><p class="meta">KORA EATS uses this to rank food for you. Your dietary needs are always respected, and we never assume a dish is safe when the vendor hasn't confirmed it.</p>
    <div class="row g-3"><div class="col-lg-6"><form class="panel" id="pf" novalidate>
      <h2 class="h6">Dietary needs</h2><div class="mb-3">${checks("dietary", tx.dietary, p.dietary)}</div>
      <h2 class="h6">Allergens to avoid</h2><div class="hint mb-1">Only dishes whose vendor confirmed their allergen list are shown.</div><div class="mb-3">${checks("allergens", tx.allergens, p.avoid_allergens)}</div>
      <h2 class="h6">Spice</h2><select class="form-select mb-3" id="spice"><option value="">No preference</option><option value="none">No spice</option><option value="mild">Mild</option><option value="medium">Medium</option><option value="hot">I like it hot</option></select>
      <div class="form-check form-switch mb-3"><input class="form-check-input" type="checkbox" role="switch" id="pers" ${p.personalization_enabled ? "checked" : ""}><label class="form-check-label" for="pers">Learn from my orders to personalise recommendations</label></div>
      <div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark">Save my food profile</button></form></div>
    <div class="col-lg-6"><div class="panel" id="memory"></div></div></div>`;
  document.getElementById("spice").value = p.spice_preference || "";
  const grp = (n) => [...root.querySelectorAll(`[data-grp="${n}"]:checked`)].map((i) => i.value);
  document.getElementById("pf").addEventListener("submit", async (e) => {
    e.preventDefault(); const a = document.getElementById("form-alert"); a.classList.add("d-none");
    const r = await App.api("/me/food-profile", { method: "PUT", body: { dietary: grp("dietary"), avoid_allergens: grp("allergens"), spice_preference: document.getElementById("spice").value || null, personalization_enabled: document.getElementById("pers").checked } });
    if (r.ok) { UI.toast("Food profile saved."); renderMemory(); } else { a.textContent = App.errorMessage(r); a.classList.remove("d-none"); }
  });
  async function renderMemory() {
    const m = await App.call("/me/food-memory"); if (!m) return;
    const box = document.getElementById("memory");
    box.innerHTML = `<h2 class="h6">What KORA EATS has learned from you</h2>` + (m.orders_considered ? `<ul class="small ps-3">
      <li>${m.orders_considered} completed order(s) considered</li>${m.favorite_categories?.length ? `<li>You often order: ${m.favorite_categories.map(App.esc).join(", ")}</li>` : ""}${m.favorite_vendors?.length ? `<li>Vendors you use most: ${m.favorite_vendors.map(App.esc).join(", ")}</li>` : ""}
      ${m.typical_dish_price ? `<li>Typical dish price: ${App.money(m.typical_dish_price)}</li>` : ""}${m.typical_spend_per_order ? `<li>Typical food spend per order: ${App.money(m.typical_spend_per_order)}</li>` : ""}
      ${m.spicy_share_percent !== null && m.spicy_share_percent !== undefined ? `<li>${m.spicy_share_percent}% of what you order is spicy</li>` : ""}${m.usual_order_times?.length ? `<li>You usually order at: ${m.usual_order_times.join(", ")}</li>` : ""}</ul>`
      : `<p class="meta">${App.esc(m.message || "Nothing learned yet. Order a few meals and your recommendations will get better.")}</p>`) +
      `<p class="meta">${App.esc(m.message || "")}${m.forgotten_before ? ` History before ${App.fmtDate(m.forgotten_before)} is ignored.` : ""}</p><button class="btn btn-outline-danger btn-sm" id="forget"><i class="bi bi-eraser"></i> Forget my history</button>`;
    document.getElementById("forget").addEventListener("click", async () => { if (confirm("Forget your order history for recommendations and delete the tracking data? This can't be undone.") && (await App.call("/me/food-memory", { method: "DELETE" }))) { UI.toast("Your history has been forgotten."); renderMemory(); } });
  }
  renderMemory();
})();
