(async () => {
  await App.mountShell({ active: "search" });
  const root = document.getElementById("page");
  const state = { q: App.qs("q"), category_id: App.qs("category_id"), tab: App.qs("tab") || "foods", sort: "popular", spicy: "", max_price: "", business_type: "", dietary: "", meal_type: "" };
  const tx = (await App.call("/taxonomy")) || { business_types: [], dietary: [], meal_types: [] };
  root.innerHTML = `
    <form class="searchbox mb-3" id="sform" role="search"><input id="q" type="search" placeholder="Search food or vendors" value="${App.esc(state.q)}" aria-label="Search"><button class="btn-add px-3" type="submit"><i class="bi bi-search"></i></button></form>
    <div class="chip-row mb-2" id="cats"></div>
    <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
      <button class="pill-btn" data-tab="foods">Foods</button><button class="pill-btn" data-tab="vendors">Vendors</button>
      <span class="vr d-none d-sm-block"></span>
      <button class="pill-btn" id="f-spicy" aria-pressed="false">Spicy</button>
      <select class="form-select form-select-sm w-auto" id="f-price" aria-label="Maximum price"><option value="">Any price</option><option value="20">Under GH₵20</option><option value="40">Under GH₵40</option><option value="60">Under GH₵60</option></select>
      <select class="form-select form-select-sm w-auto" id="f-type" aria-label="Type of seller"><option value="">All sellers</option>${tx.business_types.map((t) => `<option value="${t.id}">${App.esc(t.label)}</option>`).join("")}</select>
      <select class="form-select form-select-sm w-auto" id="f-diet" aria-label="Dietary needs"><option value="">Any diet</option>${tx.dietary.map((t) => `<option value="${t.id}">${App.esc(t.label)}</option>`).join("")}</select>
      <select class="form-select form-select-sm w-auto" id="f-meal" aria-label="Meal time"><option value="">Any time</option>${tx.meal_types.map((t) => `<option value="${t.id}">${App.esc(t.label)}</option>`).join("")}</select>
      <select class="form-select form-select-sm w-auto" id="f-sort" aria-label="Sort"><option value="popular">Popular</option><option value="price_asc">Price: low to high</option><option value="price_desc">Price: high to low</option><option value="distance">Nearest</option></select>
    </div>
    <div id="understood" class="mb-2"></div>
    <div id="results" class="card-grid" aria-live="polite"></div>`;
  App.wireCards(root);
  const results = document.getElementById("results");

  const cats = await App.call("/categories");
  const renderCats = () => (document.getElementById("cats").innerHTML = (cats || []).map((c) =>
    `<a class="chip ${String(c.id) === state.category_id ? "active" : ""}" href="#" data-cat="${c.id}"><span class="ic"><i class="bi ${App.esc(c.icon)}"></i></span>${App.esc(c.name)}</a>`).join(""));

  const looksNatural = (q) => q.trim().split(/\s+/).length >= 3 || /\b(under|below|ghs|cedis?|spicy|near|within|filling|cheap|stars?|minutes?|quick)\b/i.test(q);
  /** Plain-English search. Returns true if the sentence was understood and results were shown. */
  async function smart(base) {
    const r = await App.call("/search/natural", { params: { q: state.q, ...App.locParams() } });
    if (!r || !r.understood) return false;
    document.getElementById("understood").innerHTML = `<span class="meta me-2"><i class="bi bi-stars"></i> I understood:</span>${r.filters.map((f) => `<span class="status-pill me-1">${App.esc(f)}</span>`).join("")}`;
    results.innerHTML = r.items.length ? r.items.map((x) => App.itemCard(x)).join("") : `<div class="w-100" style="grid-column:1/-1">${App.empty("bi-egg-fried", "Nothing matches", r.message)}</div>`;
    return true;
  }
  async function run() {
    document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
    results.innerHTML = App.skeletons(4);
    const base = { ...App.locParams(), q: state.q, category_id: state.category_id };
    if (state.tab === "vendors") {
      const v = await App.call("/vendors", { params: { ...base, business_type: state.business_type } });
      results.innerHTML = v && v.length ? v.map(App.vendorCard).join("") : App.empty("bi-shop", "No vendors match your search", "Try a different word or category.");
    } else if (state.q && looksNatural(state.q) && (await smart(base))) {
      /* handled by smart() */
    } else {
      document.getElementById("understood").innerHTML = "";
      const i = await App.call("/menu-items", { params: { ...base, sort: state.sort, spicy: state.spicy, max_price: state.max_price, business_type: state.business_type, dietary: state.dietary, meal_type: state.meal_type } });
      results.innerHTML = i && i.length ? i.map((x) => App.itemCard(x)).join("") : App.empty("bi-egg-fried", "No food matches your search", "Try fewer filters or a different word.");
    }
    renderCats();
  }
  document.getElementById("sform").addEventListener("submit", (e) => { e.preventDefault(); state.q = document.getElementById("q").value.trim(); run(); });
  root.addEventListener("click", (e) => {
    const cat = e.target.closest("[data-cat]"); const tab = e.target.closest("[data-tab]");
    if (cat) { e.preventDefault(); state.category_id = String(cat.dataset.cat) === state.category_id ? "" : cat.dataset.cat; run(); }
    if (tab) { state.tab = tab.dataset.tab; run(); }
  });
  document.getElementById("f-spicy").addEventListener("click", (e) => { state.spicy = state.spicy ? "" : "true"; e.target.classList.toggle("active", !!state.spicy); e.target.setAttribute("aria-pressed", String(!!state.spicy)); run(); });
  document.getElementById("f-price").addEventListener("change", (e) => { state.max_price = e.target.value; run(); });
  [["f-type", "business_type"], ["f-diet", "dietary"], ["f-meal", "meal_type"]].forEach(([id, key]) => document.getElementById(id).addEventListener("change", (e) => { state[key] = e.target.value; run(); }));
  document.getElementById("f-sort").addEventListener("change", (e) => { state.sort = e.target.value; run(); });
  run();
})();
