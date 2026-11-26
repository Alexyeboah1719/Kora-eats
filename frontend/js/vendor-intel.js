/* Vendor dashboard: AI Vendor Intelligence (demand forecast, peak periods, popular meals, preparation + waste advice). */
Dash.register("vendor", { id: "intelligence", label: "AI insights", icon: "bi-graph-up-arrow", async render(el) {
  let day = "", charts = [];
  const ICON = { orders: "bi-bag-check", peak: "bi-clock-history", prep: "bi-fire", waste: "bi-recycle", data: "bi-info-circle", booked: "bi-calendar-check" };
  const LEVEL = { insufficient: ["warn", "Not enough data yet"], low: ["warn", "Early estimate"], medium: ["", "Reasonable estimate"], high: ["", "Solid estimate"] };
  const range = (r) => (r.low === r.high ? `${r.low}` : `${r.low}-${r.high}`);

  async function load() {
    charts.forEach((c) => c.destroy()); charts = [];
    const [r, menu] = await Promise.all([App.call("/vendor/intelligence", { params: { date: day || undefined } }), App.call("/vendor/menu")]);
    if (!r) return;
    el._menu = menu || [];
    const lv = LEVEL[r.confidence.level], o = r.orders || {}, t = o.expected_total;
    const days = Array.from({ length: 8 }, (_, i) => { const d = new Date(Date.now() + i * 864e5); return { v: i === 0 ? "" : d.toISOString().slice(0, 10), l: i === 0 ? "Today" : d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" }) }; });
    el.innerHTML = `<div class="d-flex flex-wrap gap-2 align-items-center mb-3"><label class="meta mb-0" for="ai-day">Forecast for</label><select class="form-select form-select-sm w-auto" id="ai-day">${days.map((d) => `<option value="${d.v}" ${d.v === day ? "selected" : ""}>${App.esc(d.l)}</option>`).join("")}</select>
        <span class="status-pill ${lv[0]}" title="${App.esc(r.confidence.message)}">${lv[1]}</span></div>
      <div class="alert alert-${r.confidence.level === "insufficient" ? "warning" : "light border"} py-2 small">${App.esc(r.confidence.message)}</div>
      <div class="stat-grid">${t ? Dash.stat(`${range(t)}`, r.is_today ? "Expected orders today" : `Expected orders ${r.weekday}`) : ""}${o.so_far !== undefined ? Dash.stat(o.so_far, "Orders so far today") : ""}${o.remaining ? Dash.stat(range(o.remaining), "Still expected today") : ""}
        ${r.peak_periods[0] ? Dash.stat(`<span class="fs-6">${App.esc(r.peak_periods[0].label)}</span>`, "Peak period") : ""}${r.booked_scheduled_orders ? Dash.stat(r.booked_scheduled_orders, "Scheduled orders booked") : ""}</div>
      ${r.recommendations.length ? `<div class="panel mb-3"><h2 class="h6">What to do</h2>${r.recommendations.map((x) => `<div class="d-flex gap-2 py-1"><i class="bi ${ICON[x.kind] || "bi-dot"} mt-1" style="color:var(--ok)"></i><span>${App.esc(x.text)}</span></div>`).join("")}</div>` : ""}
      ${r.hourly ? `<div class="row g-3 mb-3"><div class="col-lg-7"><div class="chart-box"><h2 class="h6">Expected orders through the day</h2><div style="height:220px"><canvas id="ai-h" role="img" aria-label="Expected orders by time of day"></canvas></div></div></div>
        <div class="col-lg-5"><div class="chart-box"><h2 class="h6">Next 7 days</h2><div style="height:220px"><canvas id="ai-w" role="img" aria-label="Expected orders for the next seven days"></canvas></div></div></div></div>` : ""}
      ${r.popular_meals.length ? `<h2 class="h6">Meals most likely to sell</h2>${Dash.table(["Meal", "Expected", "Share", "Busiest between"], r.popular_meals.map((m) => `<tr><td><strong>${App.esc(m.name)}</strong></td><td>${range(m.expected)}</td><td>${m.share_percent}%</td><td>${m.peak ? App.esc(m.peak.label || m.peak.start + " - " + m.peak.end) : '<span class="meta">-</span>'}</td></tr>`))}` : ""}
      ${r.benchmark ? `<div class="panel mb-3"><i class="bi bi-people"></i> Similar vendors on KORA EATS average about <strong>${r.benchmark.avg_orders_per_day}</strong> orders a day.</div>` : ""}
      <h2 class="h6 mt-4">Reduce food waste</h2>
      ${r.waste.length ? r.waste.map((w) => `<div class="panel mb-2 d-flex gap-2"><i class="bi bi-recycle" style="color:var(--ok)"></i><div>${App.esc(w.message)}</div></div>`).join("") : '<p class="meta">No suggestions yet. Set how many portions you plan to make below, and log what is left over at the end of the day.</p>'}
      <div class="d-flex justify-content-between align-items-center mt-3 mb-2"><h3 class="h6 mb-0">Portions I plan to make per day</h3><button class="btn btn-sm btn-outline-dark" id="log-waste"><i class="bi bi-journal-plus"></i> Log leftovers</button></div>
      ${Dash.table(["Dish", "Planned portions / day"], el._menu.map((i) => `<tr><td>${App.esc(i.name)}</td><td style="width:180px"><input class="form-control form-control-sm" type="number" min="1" max="2000" data-prep="${i.id}" value="${i.daily_prep_qty ?? ""}" placeholder="not set" aria-label="Planned portions of ${App.esc(i.name)}"></td></tr>`))}
      <p class="meta mt-3">${App.esc(r.basis)}</p>`;
    if (r.hourly && typeof Chart !== "undefined") {
      const keep = (i) => i % 4 === 0;
      charts.push(new Chart(document.getElementById("ai-h"), { type: "bar", data: { labels: r.hourly.labels, datasets: [{ data: r.hourly.values, backgroundColor: "#1b7a46", borderRadius: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { callback: function (v, i) { return keep(i) ? this.getLabelForValue(v) : ""; }, maxRotation: 0 } }, y: { beginAtZero: true } } } }));
      charts.push(new Chart(document.getElementById("ai-w"), { type: "bar", data: { labels: r.week_ahead.map((d) => d.weekday.slice(0, 3)), datasets: [{ data: r.week_ahead.map((d) => d.mean), backgroundColor: "#0f2e22", borderRadius: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${r.week_ahead[c.dataIndex].low}-${r.week_ahead[c.dataIndex].high} orders expected` } } }, scales: { y: { beginAtZero: true } } } }));
    }
  }
  el.addEventListener("change", async (e) => {
    if (e.target.id === "ai-day") { day = e.target.value; return load(); }
    const p = e.target.closest("[data-prep]");
    if (p) { const v = p.value ? Number(p.value) : null; if (await App.call(`/vendor/menu/${p.dataset.prep}/prep-qty`, { method: "PUT", body: { daily_prep_qty: v } })) { UI.toast("Saved."); setTimeout(load, 300); } }
  });
  el.addEventListener("click", async (e) => {
    if (e.target.closest("#log-waste")) {
      const y = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
      const f = await Dash.formModal({ title: "Log leftovers", submitLabel: "Save", fields: [
        { name: "menu_item_id", label: "Dish", type: "select", options: el._menu.map((i) => ({ value: i.id, label: i.name })) },
        { name: "log_date", label: "Day", type: "date", value: y, required: true },
        { name: "prepared_qty", label: "Portions prepared", type: "number", min: 1, required: true },
        { name: "leftover_qty", label: "Portions left over", type: "number", min: 0, required: true, hint: "Logging a few days makes the waste advice much sharper." }] });
      if (f && (await App.call("/vendor/waste", { method: "POST", body: { menu_item_id: Number(f.menu_item_id), log_date: f.log_date, prepared_qty: Number(f.prepared_qty), leftover_qty: Number(f.leftover_qty) } }))) { UI.toast("Logged. Thank you."); load(); }
    }
  });
  this.teardown = () => charts.forEach((c) => c.destroy());
  await load();
} });
