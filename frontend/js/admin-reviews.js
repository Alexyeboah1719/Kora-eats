Dash.register("admin", { id: "reviews", label: "Reviews", icon: "bi-star", async render(el) {
  let sentiment = "";
  async function load() {
    const list = await App.call("/admin/reviews", { params: { sentiment } }); if (!list) return;
    el.querySelector("#rows").innerHTML = list.length ? list.map((r) => `<div class="panel mb-2" style="${r.is_hidden ? "opacity:.6" : ""}"><div class="d-flex justify-content-between flex-wrap gap-1"><strong>${App.esc(r.vendor)}</strong><span class="meta">${App.fmtDate(r.created_at)} &middot; ${App.esc(r.customer_email)}</span></div>
      <div class="rating">Food ${r.food_rating}/5 &middot; Vendor ${r.vendor_rating}/5 ${r.sentiment ? `<span class="status-pill ${r.sentiment === "NEGATIVE" ? "bad" : r.sentiment === "NEUTRAL" ? "warn" : ""}">${r.sentiment.toLowerCase()}</span>` : ""}</div>
      ${r.aspects && Object.keys(r.aspects).length ? `<div class="meta">${Object.entries(r.aspects).map(([k, v]) => `${k}: ${v.toLowerCase()}`).join(" &middot; ")}</div>` : ""}${r.comment ? `<p class="mb-1">${App.esc(r.comment)}</p>` : ""}
      <button class="btn btn-sm ${r.is_hidden ? "btn-outline-dark" : "btn-outline-danger"}" data-id="${r.id}" data-hide="${!r.is_hidden}">${r.is_hidden ? "Restore" : "Hide"}</button></div>`).join("") : App.empty("bi-star", "No reviews", "");
  }
  el.innerHTML = `<div class="d-flex gap-2 mb-3"><select class="form-select w-auto" id="s" aria-label="Sentiment"><option value="">All sentiments</option><option value="NEGATIVE">Negative</option><option value="NEUTRAL">Neutral</option><option value="POSITIVE">Positive</option></select><span class="meta align-self-center">Sentiment comes from rule-based keyword analysis.</span></div><div id="rows"></div>`;
  el.querySelector("#s").addEventListener("change", (e) => { sentiment = e.target.value; load(); });
  el.addEventListener("click", async (e) => { const t = e.target.closest("[data-hide]"); if (t && (await App.call(`/admin/reviews/${t.dataset.id}/hide`, { method: "POST", params: { hidden: t.dataset.hide } }))) { UI.toast("Review updated."); load(); } });
  load();
} });
