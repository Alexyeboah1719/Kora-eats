/* Split the Budget: N people, one budget. No account needed to try it. */
(async () => {
  const user = await App.mountShell({ active: "search" });
  const root = document.getElementById("page");
  const [opts, tx] = await Promise.all([App.call("/ai/options"), App.call("/taxonomy")]);
  if (!opts || !tx) return;
  root.innerHTML = `<h1 class="h4 mb-1">Split the budget</h1><p class="meta">Eating with friends? Tell KORA EATS how many people and how much you have in total. It finds one vendor and one dish each that fits the budget (delivery included) and suits everyone as much as possible.</p>
    <div id="loc-note"></div>
    <div class="panel mb-3"><div class="row g-2 align-items-end">
      <div class="col-6 col-md-3"><label class="form-label small mb-0" for="people">How many people?</label><input class="form-control" id="people" type="number" min="2" max="20" value="5"></div>
      <div class="col-6 col-md-3"><label class="form-label small mb-0" for="budget">Total budget (GH₵)</label><input class="form-control" id="budget" type="number" min="1" step="0.5" value="150"></div>
      <div class="col-6 col-md-3"><label class="form-label small mb-0" for="ff">Delivery or pickup</label><select class="form-select" id="ff"><option value="BEST">Best option</option><option value="DELIVERY">Delivery</option><option value="PICKUP">Pickup</option></select></div>
      <div class="col-6 col-md-3"><button class="btn btn-dark w-100" id="go">Find plans</button></div></div>
      <details class="mt-2"><summary class="small fw-semibold">Add what each person wants (optional)</summary><div id="prefs" class="mt-2"></div></details></div>
    <div id="headline" class="fw-semibold mb-2"></div><div id="out"></div>`;
  const prefsBox = document.getElementById("prefs");
  const renderPrefs = () => {
    const n = Math.min(20, Math.max(2, Number(document.getElementById("people").value) || 2));
    const old = [...prefsBox.querySelectorAll(".pref-row")].map((r) => ({ label: r.querySelector("[data-f=label]").value, terms: r.querySelector("[data-f=terms]").value, nospice: r.querySelector("[data-f=nospice]").checked, max: r.querySelector("[data-f=max]").value, diet: r.querySelector("[data-f=diet]").value, crave: r.querySelector("[data-f=crave]").value }));
    prefsBox.innerHTML = Array.from({ length: n }, (_, i) => { const o = old[i] || {}; return `<div class="row g-1 mb-2 pref-row"><div class="col-6 col-md-2"><input class="form-control form-control-sm" data-f="label" placeholder="Person ${i + 1}" value="${App.esc(o.label || "")}" aria-label="Name of person ${i + 1}"></div>
      <div class="col-6 col-md-3"><input class="form-control form-control-sm" data-f="terms" placeholder="wants: chicken, jollof" value="${App.esc(o.terms || "")}" aria-label="What person ${i + 1} wants"></div>
      <div class="col-6 col-md-2"><select class="form-select form-select-sm" data-f="crave" aria-label="Craving"><option value="">Craving</option>${opts.cravings.map((c) => `<option value="${c.id}" ${o.crave === c.id ? "selected" : ""}>${App.esc(c.label)}</option>`).join("")}</select></div>
      <div class="col-6 col-md-2"><select class="form-select form-select-sm" data-f="diet" aria-label="Dietary need"><option value="">Any diet</option>${tx.dietary.map((d) => `<option value="${d.id}" ${o.diet === d.id ? "selected" : ""}>${App.esc(d.label)}</option>`).join("")}</select></div>
      <div class="col-6 col-md-1"><input class="form-control form-control-sm" data-f="max" type="number" min="1" placeholder="max GH₵" value="${App.esc(o.max || "")}" aria-label="Maximum price"></div>
      <div class="col-6 col-md-2"><div class="form-check mt-1"><input class="form-check-input" type="checkbox" data-f="nospice" ${o.nospice ? "checked" : ""} id="ns${i}"><label class="form-check-label small" for="ns${i}">No spice</label></div></div></div>`; }).join("");
  };
  renderPrefs();
  document.getElementById("people").addEventListener("change", renderPrefs);
  const l = App.getLocation();
  document.getElementById("loc-note").innerHTML = l ? "" : `<div class="alert alert-warning py-2 d-flex justify-content-between align-items-center gap-2"><span>Set your location so the delivery fee is included in the budget.</span><button class="btn btn-sm btn-dark" id="set-loc">Set location</button></div>`;
  root.addEventListener("click", async (e) => {
    if (e.target.id === "set-loc") return LocationPicker.open(user, () => location.reload());
    if (e.target.id !== "go") return;
    const btn = e.target; btn.disabled = true;
    const rows = [...prefsBox.querySelectorAll(".pref-row")].map((r) => { const f = (k) => r.querySelector(`[data-f=${k}]`);
      return { label: f("label").value.trim(), terms: f("terms").value.split(",").map((x) => x.trim()).filter(Boolean), cravings: f("crave").value ? [f("crave").value] : [], dietary: f("diet").value ? [f("diet").value] : [], max_price: f("max").value ? String(f("max").value) : null, no_spice: f("nospice").checked }; });
    const loc = App.getLocation();
    document.getElementById("out").innerHTML = App.skeletons(2);
    const data = await App.call("/ai/group/split", { method: "POST", body: { people: Number(document.getElementById("people").value), budget: String(document.getElementById("budget").value), fulfillment: document.getElementById("ff").value, preferences: rows, lat: loc ? loc.lat : null, lng: loc ? loc.lng : null } });
    btn.disabled = false; if (!data) { document.getElementById("out").innerHTML = ""; return; }
    document.getElementById("headline").textContent = data.headline;
    document.getElementById("out").innerHTML = GroupAI.results(data) + `<p class="meta">Want to order together? <a href="/search.html">Open a vendor</a> and start a group order, then let the group AI do this for your real friends.</p>`;
  });
})();
