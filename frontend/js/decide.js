/* KORA EATS "Find my meal": What can I afford? / Craving diagnosis / For you now. Everything shown comes from /api/ai/*. */
(async () => {
  const user = await App.mountShell({ active: "search" });
  const root = document.getElementById("page");
  const opts = await App.call("/ai/options"); const tx = await App.call("/taxonomy");
  if (!opts || !tx) return;
  let tab = App.qs("tab") || "afford", cravings = new Set(), lastSurface = "feed", lastResults = [];
  const loc = () => App.locParams();

  root.innerHTML = `<h1 class="h4 mb-1">Find my meal</h1><p class="meta">Tell KORA EATS your budget or your craving. It ranks real, available dishes and tells you exactly why.</p>
    <div class="d-flex gap-2 flex-wrap mb-3" role="tablist">${[["afford", "What can I afford?"], ["craving", "Craving"], ["feed", "For you now"]].map(([k, l]) => `<button class="pill-btn" role="tab" data-tab="${k}">${l}</button>`).join("")}<a class="pill-btn text-decoration-none" href="/split.html"><i class="bi bi-people"></i> Split the budget</a></div>
    <div id="loc-note"></div><div id="controls" class="panel mb-3"></div><div id="headline" class="fw-semibold mb-2"></div><div id="note"></div><div id="results" class="card-grid" style="grid-template-columns:repeat(auto-fill,minmax(290px,1fr))" aria-live="polite"></div><div id="combos"></div>`;

  const dietOptions = `<option value="">Any diet</option>${tx.dietary.map((d) => `<option value="${d.id}">${App.esc(d.label)}</option>`).join("")}`;
  const ffOptions = `<option value="BEST">Best option (delivery or pickup)</option><option value="DELIVERY">Delivery</option><option value="PICKUP">Pickup</option>`;
  const common = `<div class="row g-2 mt-1"><div class="col-sm-6"><label class="form-label small mb-0" for="ff">How do you want it?</label><select class="form-select form-select-sm" id="ff">${ffOptions}</select></div>
      <div class="col-sm-6"><label class="form-label small mb-0" for="diet">Dietary need</label><select class="form-select form-select-sm" id="diet">${dietOptions}</select></div></div>`;

  function controls() {
    const c = document.getElementById("controls");
    if (tab === "afford") {
      c.innerHTML = `<label class="form-label fw-semibold" for="budget">My total budget (food + delivery) in GH₵</label>
        <div class="input-group mb-2"><span class="input-group-text">GH₵</span><input class="form-control" id="budget" type="number" inputmode="decimal" min="1" max="5000" step="0.5" placeholder="e.g. 30"><button class="btn btn-dark" id="go">Find meals</button></div>
        <div class="d-flex gap-2 flex-wrap">${[20, 30, 50, 80, 120].map((n) => `<button class="pill-btn" data-quick="${n}">GH₵${n}</button>`).join("")}</div>${common}`;
    } else if (tab === "craving") {
      c.innerHTML = `<div class="fw-semibold mb-2">What are you craving? <span class="meta">(pick one or more)</span></div>
        <div class="d-flex gap-2 flex-wrap mb-2" id="craves">${opts.cravings.map((x) => `<button class="pill-btn" data-crave="${x.id}" aria-pressed="false">${App.esc(x.label)}</button>`).join("")}</div>
        <label class="form-label small mb-0" for="budget">Budget (optional)</label><div class="input-group input-group-sm mb-2" style="max-width:260px"><span class="input-group-text">GH₵</span><input class="form-control" id="budget" type="number" min="1" max="5000" step="0.5"></div>
        ${common}<button class="btn btn-dark mt-2" id="go">Diagnose my craving</button>`;
    } else {
      c.innerHTML = `<div class="meta">Ranked for you using your location, the time of day${user ? ", your orders and your food profile" : ""}. ${user ? '<a href="/food-profile.html">Edit my food profile</a>' : '<a href="/auth/login.html?next=/decide.html">Log in</a> for personalised picks.'}</div>
        <div class="d-flex gap-2 mt-2"><select class="form-select form-select-sm w-auto" id="meal"><option value="">Right now</option>${tx.meal_types.map((m) => `<option value="${m.id}">${App.esc(m.label)}</option>`).join("")}</select><button class="btn btn-dark btn-sm" id="go">Refresh</button></div>`;
    }
    document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    const l = App.getLocation();
    document.getElementById("loc-note").innerHTML = l ? "" : `<div class="alert alert-warning py-2 d-flex justify-content-between align-items-center gap-2"><span>Set your location so I can include delivery fees and distance.</span><button class="btn btn-sm btn-dark" id="set-loc">Set location</button></div>`;
  }

  const card = (r, i) => {
    const it = r.item, price = r.fulfillment === "PICKUP" ? "pickup" : "delivery";
    return `<div class="i-card" data-rank="${i + 1}">
      <div class="pos-rel">${App.imageBlock(it.image_url)}<span class="status-pill position-absolute" style="top:.6rem;left:.6rem" title="How well this matches you">${r.match_score}% match</span></div>
      <div class="card-body-s"><h3>${App.esc(it.name)}</h3>
        <div class="meta"><a href="/vendor.html?id=${r.vendor.id}">${App.esc(r.vendor.name)}</a> &middot; ${App.esc(r.vendor.business_label)}${r.vendor.area ? " &middot; " + App.esc(r.vendor.area) : ""}</div>
        <div class="d-flex justify-content-between align-items-center"><span class="price">${App.money(r.total_cost)}</span><span class="status-pill ${r.fulfillment === "PICKUP" ? "warn" : ""}"><i class="bi ${r.fulfillment === "PICKUP" ? "bi-bag-check" : "bi-bicycle"}"></i> ${r.fulfillment === "PICKUP" ? "Pickup" : "Delivery"}${r.fee_unknown ? " (fee depends on location)" : ""}</span></div>
        ${r.pickup_message && r.fulfillment === "DELIVERY" ? `<div class="small text-success"><i class="bi bi-piggy-bank"></i> ${App.esc(r.pickup_message)}</div>` : ""}
        <div class="small fw-semibold mt-1">Why this?</div><ul class="small ps-3 mb-1">${r.why.map((w) => `<li>${App.esc(w)}</li>`).join("")}</ul>
        ${r.warnings.map((w) => `<div class="small text-warning"><i class="bi bi-exclamation-triangle"></i> ${App.esc(w)}</div>`).join("")}
        <div class="d-flex justify-content-end mt-auto pt-2"><button class="btn-add" data-add-item="${it.id}" ${it.is_available ? "" : "disabled"}>Add to cart</button></div></div></div>`;
  };
  const comboCard = (c) => `<div class="panel mb-2"><div class="d-flex justify-content-between flex-wrap gap-1"><strong>${App.esc(c.vendor.name)}</strong><span class="price">${App.money(c.total)} <span class="meta">${c.fulfillment === "PICKUP" ? "pickup" : "with delivery"}</span></span></div>
    <div>${c.items.map((i) => `<span class="chip-mini">${App.esc(i.name)} ${App.money(i.price)}</span>`).join("")}</div>
    <ul class="small ps-3 mb-1 mt-1">${c.why.map((w) => `<li>${App.esc(w)}</li>`).join("")}</ul><div class="meta">${App.money(c.left_over)} left from your budget</div>
    <div class="mt-1 d-flex gap-2 flex-wrap">${c.items.map((i) => `<button class="btn btn-sm btn-outline-dark" data-add-item="${i.menu_item_id}">+ ${App.esc(i.name)}</button>`).join("")}</div></div>`;

  function show(data, surface) {
    lastSurface = surface; lastResults = data.results;
    document.getElementById("headline").textContent = data.headline || "";
    document.getElementById("note").innerHTML = (data.note ? `<div class="alert alert-info py-2">${App.esc(data.note)}</div>` : "") + (data.hint ? `<div class="alert alert-secondary py-2"><i class="bi bi-lightbulb"></i> ${App.esc(data.hint)}</div>` : "");
    document.getElementById("results").innerHTML = data.results.length ? data.results.map(card).join("") : `<div style="grid-column:1/-1">${App.empty("bi-search", "Nothing matches yet", "Try a higher budget, a different craving, or turn off a dietary filter.")}</div>`;
    document.getElementById("combos").innerHTML = data.combos && data.combos.length ? `<div class="section-title"><span>Meal combos within your budget</span></div>${data.combos.map(comboCard).join("")}` : "";
  }

  async function run() {
    const btn = document.getElementById("go"); if (btn) btn.disabled = true;
    document.getElementById("results").innerHTML = App.skeletons(3);
    const p = loc(), diet = document.getElementById("diet")?.value, ff = document.getElementById("ff")?.value || "BEST";
    const base = { lat: p.lat ?? null, lng: p.lng ?? null, fulfillment: ff, dietary: diet ? [diet] : [] };
    let data = null;
    if (tab === "afford") {
      const b = Number(document.getElementById("budget").value);
      if (!(b > 0)) { UI.toast("Enter your total budget in GH₵.", "error"); if (btn) btn.disabled = false; return show({ results: [] }, "afford"); }
      data = await App.call("/ai/afford", { method: "POST", body: { ...base, budget: String(b) } }); if (data) show(data, "afford");
    } else if (tab === "craving") {
      if (!cravings.size) { UI.toast("Pick at least one craving.", "error"); if (btn) btn.disabled = false; return show({ results: [] }, "craving"); }
      const b = Number(document.getElementById("budget").value);
      data = await App.call("/ai/craving", { method: "POST", body: { ...base, cravings: [...cravings], budget: b > 0 ? String(b) : null } }); if (data) show(data, "craving");
    } else {
      data = await App.call("/ai/feed", { params: { ...p, meal_type: document.getElementById("meal")?.value || "" } }); if (data) show(data, "feed");
    }
    if (btn) btn.disabled = false;
  }

  root.addEventListener("click", (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.tab) { tab = t.dataset.tab; history.replaceState(null, "", "?tab=" + tab); controls(); document.getElementById("results").innerHTML = ""; document.getElementById("headline").textContent = ""; document.getElementById("combos").innerHTML = ""; document.getElementById("note").innerHTML = ""; if (tab === "feed") run(); }
    else if (t.dataset.quick) { document.getElementById("budget").value = t.dataset.quick; run(); }
    else if (t.dataset.crave) { const k = t.dataset.crave; cravings.has(k) ? cravings.delete(k) : cravings.add(k); t.classList.toggle("active", cravings.has(k)); t.setAttribute("aria-pressed", String(cravings.has(k))); }
    else if (t.id === "go") run();
    else if (t.id === "set-loc") LocationPicker.open(user, () => { controls(); run(); });
    else if (t.dataset.addItem && user) {            // tell the engine this recommendation led to a cart add (the Add itself is handled by App.wireCards)
      const rank = Number(t.closest("[data-rank]")?.dataset.rank) || null;
      App.api("/ai/events", { method: "POST", body: { surface: lastSurface, kind: "add_to_cart", menu_item_id: Number(t.dataset.addItem), rank } });
    }
  });
  root.addEventListener("click", (e) => { const a = e.target.closest("a[href^='/vendor.html']"); const card = e.target.closest("[data-rank]"); if (a && card && user) App.api("/ai/events", { method: "POST", body: { surface: lastSurface, kind: "click", menu_item_id: Number((lastResults[Number(card.dataset.rank) - 1] || {}).item?.id), rank: Number(card.dataset.rank) } }); });
  App.wireCards(root);
  controls();
  if (tab === "feed") run();
})();
