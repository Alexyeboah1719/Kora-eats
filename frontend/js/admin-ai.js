/* Admin: AI performance and health. Aggregated, anonymous numbers only. */
Dash.register("admin", { id: "ai", label: "AI performance", icon: "bi-cpu", async render(el) {
  let days = 30;
  const pct = (v) => (v === null || v === undefined ? '<span class="meta">no data</span>' : v + "%");
  const LABEL = { feed: "For you feed", afford: "What can I afford?", craving: "Craving", assistant: "Assistant", group: "Group AI" };
  async function load() {
    const d = await App.call("/admin/ai/overview", { params: { days } }); if (!d) return;
    const c = d.customer_controls, f = d.forecast_coverage, p = d.dispatch;
    el.innerHTML = `<div class="d-flex gap-2 align-items-center mb-3"><label class="meta mb-0" for="ai-days">Period</label><select class="form-select form-select-sm w-auto" id="ai-days">${[7, 30, 90].map((n) => `<option value="${n}" ${n === days ? "selected" : ""}>Last ${n} days</option>`).join("")}</select></div>
      <div class="stat-grid">${Dash.stat(d.totals.impressions, "Recommendations shown")}${Dash.stat(d.totals.orders_from_recommendations, "Orders from recommendations")}${Dash.stat(d.totals.conversion_rate === null ? "-" : d.totals.conversion_rate + "%", "Conversion")}</div>
      <h2 class="h6 mt-3">By feature</h2>${Dash.table(["Feature", "Shown", "Clicked", "Added to cart", "Ordered", "Click rate", "Conversion"], d.surfaces.map((s) => `<tr><td>${LABEL[s.surface] || s.surface}</td><td>${s.impressions}</td><td>${s.clicks}</td><td>${s.add_to_cart}</td><td>${s.orders}</td><td>${pct(s.click_rate)}</td><td>${pct(s.conversion_rate)}</td></tr>`))}
      <div class="row g-3 mt-1"><div class="col-lg-6"><div class="panel h-100"><h2 class="h6">Most recommended dishes</h2>${d.top_dishes.length ? `<ol class="small ps-3 mb-0">${d.top_dishes.map((t) => `<li><strong>${App.esc(t.dish)}</strong> <span class="meta">${App.esc(t.vendor)}: shown ${t.impressions}, ordered ${t.orders}</span></li>`).join("")}</ol>` : '<p class="meta mb-0">Nothing recorded yet.</p>'}</div></div>
        <div class="col-lg-6"><div class="panel h-100"><h2 class="h6">Customer controls in use</h2><ul class="small ps-3 mb-0"><li>${c.customers_with_a_food_profile} food profiles saved</li><li>${c.with_dietary_needs} with dietary needs, ${c.avoiding_allergens} avoiding allergens</li><li>${c.personalisation_switched_off} switched personalisation off</li><li>${c.who_forgot_their_history} erased their history</li></ul></div></div>
        <div class="col-lg-6"><div class="panel h-100"><h2 class="h6">Vendor forecast coverage</h2><p class="small mb-1">${f.approved_vendors} approved vendors</p><ul class="small ps-3 mb-0"><li>${f.high} solid &middot; ${f.medium} reasonable &middot; ${f.low} early estimate</li><li>${f.insufficient} don't have enough history yet</li></ul></div></div>
        <div class="col-lg-6"><div class="panel h-100"><h2 class="h6">Dispatch right now</h2><ul class="small ps-3 mb-0"><li>${p.orders_waiting_for_a_rider} order(s) waiting, ${p.urgent} urgent, average wait ${p.average_wait_minutes} min</li><li>${p.riders_online} of ${p.riders_approved} riders online (${p.riders_online_with_location} sharing location)</li></ul></div></div></div>
      <h2 class="h6 mt-3">Ranking weights</h2><p class="meta">How much each factor matters when KORA EATS ranks food. Change them under <strong>Settings</strong>.</p>
      <div class="d-flex flex-wrap gap-2">${Object.entries(d.ranking_weights).map(([k, v]) => `<span class="chip-mini">${k.replace(/_/g, " ")}: ${v}</span>`).join("")}</div><p class="meta mt-3">${App.esc(d.note)}</p>`;
  }
  el.addEventListener("change", (e) => { if (e.target.id === "ai-days") { days = Number(e.target.value); load(); } });
  await load();
} });
