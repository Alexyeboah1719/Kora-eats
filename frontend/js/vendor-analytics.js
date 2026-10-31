Dash.register("vendor", { id: "analytics", label: "Analytics", icon: "bi-graph-up", async render(el) {
  const a = await App.call("/vendor/analytics"); if (!a) return;
  el.innerHTML = `<div class="stat-grid">${Dash.stat(App.money(a.food_sales.today), "Food sales today")}${Dash.stat(App.money(a.food_sales.week), "Last 7 days")}${Dash.stat(App.money(a.food_sales.month), "Last 30 days")}${Dash.stat(App.money(a.food_sales.total), "All time")}
      ${Dash.stat(a.orders.total, "Completed orders")}${Dash.stat(App.money(a.average_order_value), "Average order")}${Dash.stat(a.repeat_customer_percent + "%", "Repeat customers")}${Dash.stat(a.reviews ? a.rating.toFixed(1) + " &#9733;" : "-", "Rating")}</div>
    <div class="row g-3"><div class="col-lg-7"><div class="chart-box"><h2 class="h6">Food sales, last 30 days (GH₵)</h2><div style="height:230px"><canvas id="c1" role="img" aria-label="Food sales per day"></canvas></div></div></div>
      <div class="col-lg-5"><div class="chart-box"><h2 class="h6">Busiest hours (orders, last 30 days)</h2><div style="height:230px"><canvas id="c2" role="img" aria-label="Orders by hour"></canvas></div></div></div></div>
    <h2 class="h6 mt-4">Popular foods</h2>${a.popular_foods.length ? Dash.table(["Food", "Sold", "Sales"], a.popular_foods.map((f) => `<tr><td>${App.esc(f.name)}</td><td>${f.quantity}</td><td>${App.money(f.sales)}</td></tr>`)) : App.empty("bi-egg-fried", "No sales yet", "")}
    <p class="meta mt-2">Food sales are the value of food ordered (before delivery fees and discounts).</p>`;
  if (typeof Chart === "undefined") return;
  const opt = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { ticks: { maxTicksLimit: 8 } } } };
  new Chart(document.getElementById("c1"), { type: "line", data: { labels: a.revenue_by_day.labels, datasets: [{ data: a.revenue_by_day.values, borderColor: "#1b7a46", backgroundColor: "#1b7a4633", fill: true, tension: 0.3, pointRadius: 2 }] }, options: opt });
  new Chart(document.getElementById("c2"), { type: "bar", data: { labels: a.orders_by_hour.labels, datasets: [{ data: a.orders_by_hour.values, backgroundColor: "#0f2e22", borderRadius: 3 }] }, options: opt });
} });
