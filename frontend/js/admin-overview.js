Dash.register("admin", { id: "overview", label: "Overview", icon: "bi-speedometer2", async render(el) {
  const d = await App.call("/admin/overview"); if (!d) return;
  const c = d.cards, ch = d.charts;
  const todo = [c.pending_vendor_approvals && `<a href="#vendors">${c.pending_vendor_approvals} vendor application(s)</a>`, c.pending_rider_approvals && `<a href="#riders">${c.pending_rider_approvals} rider application(s)</a>`].filter(Boolean);
  el.innerHTML = `${todo.length ? `<div class="alert alert-warning py-2">Waiting for your review: ${todo.join(" &middot; ")}</div>` : ""}
    <div class="stat-grid">${Dash.stat(App.money(c.total_revenue), "Total revenue")}${Dash.stat(App.money(c.today_revenue), "Today's revenue")}${Dash.stat(c.total_orders, "Total orders")}
      ${Dash.stat(c.pending_orders, "Orders in progress")}${Dash.stat(c.active_vendors, "Active vendors")}${Dash.stat(`${c.active_riders} <span class="meta">(${c.online_riders} online)</span>`, "Active riders")}
      ${Dash.stat(c.customers, "Customers")}${Dash.stat(c.cancelled_orders, "Cancelled orders")}</div>
    <div class="row g-3">${[["revenue", "Revenue (last 30 days, GH₵)", "line"], ["orders", "Orders (last 30 days)", "bar"], ["customer_growth", "New customers", "line"], ["vendor_growth", "New vendors", "bar"], ["popular_foods", "Popular foods", "bar"], ["popular_vendors", "Popular vendors (orders)", "bar"]]
      .map(([k, t]) => `<div class="col-lg-6"><div class="chart-box"><h2 class="h6">${t}</h2><div style="height:230px"><canvas id="ch-${k}" role="img" aria-label="${t}"></canvas></div></div></div>`).join("")}</div>
    <p class="meta mt-2">Revenue counts paid orders that weren't cancelled or refunded.</p>`;
  if (typeof Chart === "undefined") return;
  const ink = "#0f2e22", gold = "#f2b431", green = "#1b7a46";
  const draw = (k, type, color, horizontal) => new Chart(document.getElementById("ch-" + k), { type, data: { labels: ch[k].labels, datasets: [{ data: ch[k].values, borderColor: color, backgroundColor: type === "line" ? color + "33" : color, fill: type === "line", tension: 0.3, borderRadius: 4, pointRadius: type === "line" ? 2 : 0 }] },
    options: { responsive: true, maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x", plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: horizontal ? 6 : 8 } }, y: { beginAtZero: true, ticks: { precision: 0 } } } } });
  draw("revenue", "line", green); draw("orders", "bar", ink); draw("customer_growth", "line", gold); draw("vendor_growth", "bar", gold);
  draw("popular_foods", "bar", green, true); draw("popular_vendors", "bar", ink, true);
} });
