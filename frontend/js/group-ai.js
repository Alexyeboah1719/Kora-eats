/* Group Food AI: shared plan renderer + the "My wishes / Suggest meals" panel used on the group page. */
const GroupAI = (() => {
  const esc = (s) => App.esc(s), money = (n) => App.money(n);

  /** One suggested plan: vendor, compatibility, what each person gets, what each pays, totals. */
  function planCard(p, { apply = false, idx = 0 } = {}) {
    const ful = p.fulfillment === "PICKUP" ? "Pickup" : "Delivery";
    return `<div class="panel mb-3" data-plan="${idx}">
      <div class="d-flex justify-content-between flex-wrap gap-2 align-items-start"><div><strong class="fs-6">${esc(p.vendor.name)}</strong>${p.vendor.area ? `<span class="meta"> &middot; ${esc(p.vendor.area)}</span>` : ""}
        <div class="meta">${p.vendor.rating_count ? "&#9733; " + p.vendor.rating_avg.toFixed(1) + " (" + p.vendor.rating_count + ")" : "New vendor"} &middot; ${ful}${p.fee_unknown ? " (fee depends on location)" : ""}</div></div>
        <div class="text-end"><span class="status-pill" title="How well this plan fits everyone's wishes">${p.compatibility}% group match</span><div class="price">${money(p.total)}</div><div class="meta">${money(p.cost_per_person)} each</div></div></div>
      <div class="table-responsive mt-2"><table class="table table-sm align-middle mb-1"><thead><tr><th>Who</th><th>Dish</th><th class="text-end">Price</th><th class="text-end">Pays</th></tr></thead><tbody>
        ${p.people.map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(x.dish)}<div class="small text-muted">${x.why.map(esc).join(" &middot; ")}${x.why.length ? " &middot; " : ""}${x.match}% match</div></td><td class="text-end">${money(x.price)}</td><td class="text-end">${money(x.pays)}</td></tr>`).join("")}</tbody></table></div>
      <div class="d-flex justify-content-between small"><span>Food ${money(p.food_total)}${p.delivery_fee ? " + shared delivery " + money(p.delivery_fee) : ""}</span>${p.left_over !== null && p.left_over !== undefined ? `<span class="text-success">${money(p.left_over)} left from the budget</span>` : ""}</div>
      <ul class="small ps-3 mb-1 mt-1">${p.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
      ${p.pickup_message ? `<div class="small text-success"><i class="bi bi-piggy-bank"></i> ${esc(p.pickup_message)}</div>` : ""}
      ${apply ? `<button class="btn btn-dark btn-sm mt-2" data-apply="${idx}">Use this plan for the group</button>` : ""}</div>`;
  }
  function results(data, opts = {}) {
    const plans = data.plans || [];
    return (data.note ? `<div class="alert alert-info py-2">${esc(data.note)}</div>` : "") +
      (plans.length ? plans.map((p, i) => planCard(p, { ...opts, idx: i })).join("") : App.empty("bi-people", "No plan fits yet", data.hint || "Try a higher budget or relax a requirement.")) +
      (!plans.length && data.partial && data.partial.length ? `<div class="meta">Vendors that couldn't serve everyone: ${data.partial.map((x) => `${esc(x.vendor)} (nothing for ${x.no_dish_for.map(esc).join(", ")})`).join("; ")}.</div>` : "");
  }

  /** Group page panel. g = group view, reload() refreshes the page after a plan is applied. */
  async function mount(slot, g, code, reload) {
    const [opts, mine] = await Promise.all([App.call("/ai/options"), App.call(`/groups/${code}/preferences`)]);
    if (!opts || !mine) { slot.innerHTML = ""; return; }
    const pending = g.members.filter((m) => !m.has_preferences).map((m) => m.name.split(" ")[0]);
    let lastPlans = [];
    slot.innerHTML = `<div class="panel mb-3" style="border-color:var(--gold)"><h2 class="h6"><i class="bi bi-stars"></i> Group food AI</h2>
      <p class="meta mb-2">Everyone says what they want, then KORA EATS finds one dish each that suits the group. Your wishes are private: others only see that you've answered.</p>
      <details ${mine.terms.length || mine.cravings.length || mine.no_spice || mine.max_price ? "" : "open"}><summary class="fw-semibold small">My wishes</summary>
        <div class="row g-2 mt-1"><div class="col-12"><label class="form-label small mb-0" for="gw-terms">I'd like (food words, comma separated)</label><input class="form-control form-control-sm" id="gw-terms" placeholder="chicken, jollof" value="${esc(mine.terms.join(", "))}"></div>
          <div class="col-12 d-flex gap-1 flex-wrap">${opts.cravings.map((c) => `<button type="button" class="pill-btn ${mine.cravings.includes(c.id) ? "active" : ""}" data-gwc="${c.id}" aria-pressed="${mine.cravings.includes(c.id)}">${esc(c.label)}</button>`).join("")}</div>
          <div class="col-6"><label class="form-label small mb-0" for="gw-max">My max price (GH₵)</label><input class="form-control form-control-sm" id="gw-max" type="number" min="1" step="0.5" value="${mine.max_price ?? ""}"></div>
          <div class="col-6 d-flex align-items-end"><div class="form-check"><input class="form-check-input" type="checkbox" id="gw-nospice" ${mine.no_spice ? "checked" : ""}><label class="form-check-label small" for="gw-nospice">No spice</label></div></div>
          <div class="col-12"><button class="btn btn-sm btn-dark" id="gw-save">Save my wishes</button> <span class="meta">Your saved <a href="/food-profile.html">food profile</a> (diet, allergens) is applied automatically.</span></div></div></details>
      ${g.is_organizer ? `<hr><div class="fw-semibold small mb-1">Find the best meals ${pending.length ? `<span class="meta">(still waiting for: ${pending.map(esc).join(", ")})</span>` : ""}</div>
        <div class="row g-2"><div class="col-5"><div class="input-group input-group-sm"><span class="input-group-text">GH₵</span><input class="form-control" id="gs-budget" type="number" min="1" step="0.5" placeholder="Group budget (optional)"></div></div>
          <div class="col-4"><select class="form-select form-select-sm" id="gs-ff"><option value="BEST">Best option</option><option value="DELIVERY">Delivery</option><option value="PICKUP">Pickup</option></select></div>
          <div class="col-3"><button class="btn btn-sm btn-dark w-100" id="gs-go">Suggest</button></div>
          <div class="col-12"><div class="form-check"><input class="form-check-input" type="checkbox" id="gs-any"><label class="form-check-label small" for="gs-any">Look at other vendors too</label></div></div></div><div id="gs-out" class="mt-2"></div>` : ""}</div>`;
    const crav = new Set(mine.cravings);
    slot.addEventListener("click", async (e) => {
      const t = e.target.closest("button"); if (!t) return;
      if (t.dataset.gwc) { const k = t.dataset.gwc; crav.has(k) ? crav.delete(k) : crav.add(k); t.classList.toggle("active", crav.has(k)); t.setAttribute("aria-pressed", String(crav.has(k))); }
      else if (t.id === "gw-save") {
        const mp = document.getElementById("gw-max").value;
        const r = await App.call(`/groups/${code}/preferences`, { method: "PUT", body: { terms: document.getElementById("gw-terms").value.split(",").map((x) => x.trim()).filter(Boolean), cravings: [...crav], max_price: mp ? String(mp) : null, no_spice: document.getElementById("gw-nospice").checked } });
        if (r) UI.toast("Your wishes are saved.");
      } else if (t.id === "gs-go") {
        const l = App.getLocation(), b = document.getElementById("gs-budget").value;
        t.disabled = true;
        const data = await App.call(`/groups/${code}/suggest`, { method: "POST", body: { budget: b ? String(b) : null, fulfillment: document.getElementById("gs-ff").value, lat: l ? l.lat : null, lng: l ? l.lng : null, any_vendor: document.getElementById("gs-any").checked } });
        t.disabled = false; if (!data) return;
        lastPlans = data.plans;
        document.getElementById("gs-out").innerHTML = (data.members_without_preferences ? `<div class="meta mb-1">${data.members_without_preferences} member(s) haven't set wishes, so they're treated as easy to please.</div>` : "") + results(data, { apply: true });
      } else if (t.dataset.apply !== undefined) {
        const p = lastPlans[Number(t.dataset.apply)]; if (!p) return;
        if (await App.call(`/groups/${code}/suggest/apply`, { method: "POST", body: { vendor_id: p.vendor.id, assignments: p.people.map((x) => ({ user_id: x.user_id, menu_item_id: x.menu_item_id })) } })) { UI.toast("Plan applied to the group."); reload(); }
      }
    });
  }
  return { planCard, results, mount };
})();
