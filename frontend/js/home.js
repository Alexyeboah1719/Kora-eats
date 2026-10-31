(async () => {
  const user = await App.mountShell({ active: "home" });
  const root = document.getElementById("page");

  // Default the delivery location to the user's default saved address the first time.
  if (!App.getLocation() && user) {
    const addrs = await App.call("/me/addresses");
    const d = addrs && (addrs.find((a) => a.is_default) || addrs[0]);
    if (d) App.setLocation({ lat: d.latitude, lng: d.longitude, label: d.label + " - " + d.address_line });
  }

  const hero = user ? "" : `
    <div class="liquid-glass-bg">
      <div class="liquid-glass-blob"></div>
      <div class="liquid-glass-blob"></div>
      <div class="liquid-glass-blob"></div>
      <div class="liquid-glass-blob"></div>
      <div class="liquid-glass-blob"></div>
    </div>
    <section class="hero-landing" aria-labelledby="hero-title">
      <h1 id="hero-title">Hot food from vendors near you.</h1>
      <p class="mt-2">Jollof, waakye, fufu and more. Pay with Mobile Money and track your rider live.</p>
      <div class="d-flex flex-wrap gap-2 mt-3 position-relative">
        <a class="btn btn-gold" href="/auth/register.html">Create free account</a>
        <a class="btn btn-ghost" href="/auth/login.html">Log in</a>
      </div>
      <div class="mt-3 small position-relative" style="color:rgba(255,255,255,0.85)">Own a restaurant? <a class="text-warning fw-semibold" href="/vendor-apply.html">Sell on KORA EATS</a> &middot; Want to earn? <a class="text-warning fw-semibold" href="/rider-apply.html">Deliver with KORA EATS</a></div>
    </section>
    <div class="feature-row">
      <div class="feature"><i class="bi bi-phone"></i><div><strong>Mobile Money</strong><div class="meta">Pay securely from your phone.</div></div></div>
      <div class="feature"><i class="bi bi-geo-alt"></i><div><strong>Live tracking</strong><div class="meta">Watch your rider on the map.</div></div></div>
      <div class="feature"><i class="bi bi-stars"></i><div><strong>Ask the AI assistant</strong><div class="meta">"I have GH₵40 and want something filling."</div></div></div>
    </div>`;
  root.innerHTML = hero + `
    <h1 class="h3 mb-1" id="greeting">&nbsp;</h1>
    <button class="loc-bar mb-3" id="loc-btn" type="button" aria-label="Change delivery location">
      <i class="bi bi-geo-alt-fill"></i><span class="flex-grow-1 text-truncate" id="loc-label"></span><i class="bi bi-chevron-down"></i></button>
    <form class="searchbox mb-3" action="/search.html" role="search">
      <input name="q" type="search" placeholder="Search jollof, waakye, vendors..." aria-label="Search food" autocomplete="off">
      <button class="btn-add px-3" type="submit"><i class="bi bi-search"></i></button></form>
    <div id="ai-slot"></div>
    <div class="chip-row" id="cats"></div>
    <div id="promos"></div>
    <div class="section-title"><span>Vendors near you</span><a href="/search.html?tab=vendors">See all</a></div>
    <div class="card-grid" id="vendors">${App.skeletons(4)}</div>
    <div class="section-title"><span>Popular right now</span></div>
    <div class="card-grid" id="popular">${App.skeletons(4)}</div>
    <div class="section-title"><span id="rec-title">Recommended for you</span><a href="/decide.html">Find my meal</a></div>
    <div class="card-grid" id="recommended">${App.skeletons(4)}</div>`;

  const setLabel = () => {
    const l = App.getLocation();
    document.getElementById("loc-label").textContent = l ? l.label : "Set your delivery location";
  };
  setLabel();
  document.getElementById("loc-btn").addEventListener("click", () => LocationPicker.open(user, () => { setLabel(); load(); }));
  if ((typeof AIWidget !== "undefined" ? AIWidget : undefined)) document.getElementById("ai-slot").innerHTML =
    `<button class="panel w-100 text-start mb-3 d-flex gap-3 align-items-center" id="ai-card" type="button" style="border-color:var(--gold)">
       <i class="bi bi-stars fs-3" style="color:#946b00"></i><span><strong>Ask the food assistant</strong><br><span class="meta">"I have GH₵40 and want something filling"</span></span></button>`;
  document.getElementById("ai-card")?.addEventListener("click", () => AIWidget.open());
  App.wireCards(root);

  async function load() {
    const data = await App.call("/home", { params: App.locParams() });
    if (!data) return;
    document.getElementById("greeting").textContent = data.greeting;
    if (data.headline) document.getElementById("rec-title").textContent = data.headline;
    document.getElementById("cats").innerHTML = data.categories.map((c) =>
      `<a class="chip" href="/search.html?category_id=${c.id}"><span class="ic"><i class="bi ${App.esc(c.icon)}"></i></span>${App.esc(c.name)}</a>`).join("");
    document.getElementById("promos").innerHTML = (data.promotions || []).length
      ? `<div class="section-title"><span>Offers</span></div><div class="card-grid">${data.promotions.map((p) =>
          `<a class="panel text-decoration-none" style="border-color:var(--gold)" href="/vendor.html?id=${p.vendor_id}"><span class="status-pill warn">${App.esc(p.badge)}</span>
           <h3 class="h6 mt-2 mb-1">${App.esc(p.title)}</h3><div class="meta">${App.esc(p.vendor_name)}</div></a>`).join("")}</div>` : "";
    const fill = (id, html, emptyMsg) => (document.getElementById(id).innerHTML = html || App.empty("bi-shop", emptyMsg));
    fill("vendors", data.nearby_vendors.map(App.vendorCard).join(""), "No vendors found near you yet");
    fill("popular", data.popular_items.map((i) => App.itemCard(i)).join(""), "Nothing popular yet");
    fill("recommended", data.recommended_items.map((i) => App.itemCard(i)).join(""), "No recommendations yet");
  }
  const footer = document.createElement("footer");
  footer.className = "site-footer";
  footer.innerHTML = `<div class="mb-2">${user ? "" : '<a href="/auth/login.html">Log in</a><a href="/auth/register.html">Sign up</a>'}<a href="/vendor-apply.html">Sell on KORA EATS</a><a href="/rider-apply.html">Deliver with KORA EATS</a><a href="/support.html">Help &amp; support</a></div><div>&copy; KORA EATS &middot; <a href="/status.html">System status</a></div>`;
  root.append(footer);
  load();
})();
