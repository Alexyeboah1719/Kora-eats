Dash.register("vendor", { id: "reviews", label: "Reviews", icon: "bi-star", async render(el) {
  const d = await App.call("/vendor/reviews"); if (!d) return;
  const max = Math.max(1, ...Object.values(d.distribution));
  const chip = (s) => s ? `<span class="status-pill ${s === "NEGATIVE" ? "bad" : s === "NEUTRAL" ? "warn" : ""}">${s.toLowerCase()}</span>` : "";
  el.innerHTML = `<div class="row g-3 mb-3"><div class="col-md-4"><div class="stat"><div class="n">${d.count ? d.average.toFixed(1) : "-"} <span style="color:var(--gold)">&#9733;</span></div><div class="l">${d.count} review(s)</div></div></div>
    <div class="col-md-8"><div class="panel">${[5, 4, 3, 2, 1].map((n) => `<div class="d-flex align-items-center gap-2 small"><span style="width:1.2rem">${n}</span><div class="progress flex-grow-1" style="height:8px"><div class="progress-bar bg-warning" style="width:${(d.distribution[n] / max) * 100}%"></div></div><span style="width:2rem">${d.distribution[n]}</span></div>`).join("")}</div></div></div>
    <div id="sentiment-slot"></div>` +
    (d.reviews.length ? d.reviews.map((r) => `<div class="panel mb-2"><div class="d-flex justify-content-between"><strong>${App.esc(r.customer)}</strong><span class="meta">${App.fmtDate(r.created_at)}</span></div>
      <div class="rating">Food ${r.food_rating}/5 &middot; Service ${r.vendor_rating}/5 ${chip(r.sentiment)}</div>
      ${r.aspects && Object.keys(r.aspects).length ? `<div class="meta">${Object.entries(r.aspects).map(([k, v]) => `${k}: ${v.toLowerCase()}`).join(" &middot; ")}</div>` : ""}
      ${r.comment ? `<p class="mb-0 mt-1">${App.esc(r.comment)}</p>` : ""}${r.image_url ? `<img class="rounded mt-2" src="${App.esc(r.image_url)}" alt="Review photo" style="max-height:140px">` : ""}</div>`).join("") : App.empty("bi-star", "No reviews yet", "Reviews appear after customers receive their orders."));
  const ins = await App.call("/vendor/reviews/insights");
  if (ins && ins.reviews_analysed) {
    const slot = el.querySelector("#sentiment-slot");
    slot.innerHTML = `<div class="panel mb-3"><h2 class="h6">What customers say <span class="meta">(${App.esc(ins.method)})</span></h2>
      <div class="mb-2">${["POSITIVE", "NEUTRAL", "NEGATIVE"].map((k) => `<span class="status-pill ${k === "NEGATIVE" ? "bad" : k === "NEUTRAL" ? "warn" : ""} me-1">${ins.sentiment[k]} ${k.toLowerCase()}</span>`).join("")}</div>
      ${ins.top_issues.length ? `<div class="meta">Most mentioned problems: ${ins.top_issues.map((i) => `<strong>${App.esc(i.aspect)}</strong> (${i.mentions})`).join(", ")}</div>` : '<div class="meta">No recurring problems found.</div>'}</div>`;
  }
} });
