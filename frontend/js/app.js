/* Shared client code for every signed-in/out app page: API wrapper, shell (header + bottom nav),
   location, formatting. Tokens are HttpOnly cookies - this file never sees them. */
const App = (() => {
  const state = { user: null };

  // ---------------------------------------------------------------- API
  async function raw(path, { method = "GET", body, params, form, idempotencyKey } = {}) {
    const url = "/api" + path + (params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")) : "");
    const opts = { method, credentials: "same-origin", headers: {} };
    if (form) opts.body = form;                                        // multipart (uploads)
    else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    if (idempotencyKey) opts.headers["Idempotency-Key"] = idempotencyKey;
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    return { ok: res.ok, status: res.status, data };
  }
  /** Calls the API; on 401 silently refreshes the session once and retries. */
  async function api(path, options = {}) {
    let r = await raw(path, options);
    if (r.status === 401 && !path.startsWith("/auth/")) {
      if ((await raw("/auth/refresh", { method: "POST" })).ok) r = await raw(path, options);
    }
    return r;
  }
  function errorMessage(r) {
    const d = r && r.data && r.data.detail;
    if (d && d.message) return d.message;
    if (Array.isArray(d) && d[0]) return String(d[0].msg).replace(/^Value error, /, "");
    return "Something went wrong. Please try again.";
  }
  /** Runs a request, toasts on failure, returns data or null. */
  async function call(path, options) {
    try {
      const r = await api(path, options);
      if (!r.ok) { UI.toast(errorMessage(r), "error"); return null; }
      return r.data;
    } catch (_) { UI.toast("Can't reach the server.", "error"); return null; }
  }

  // ---------------------------------------------------------------- helpers
  const money = (n) => "GH₵" + Number(n || 0).toLocaleString("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });   // GH₵1,250.00
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const qs = (name) => new URLSearchParams(location.search).get(name) || "";
  const timeAgo = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago"; return new Date(iso).toLocaleDateString();
  };
  const fmtDate = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const STATUS_LABEL = {
    PENDING_PAYMENT: "Awaiting payment", PAID: "Paid", CONFIRMED: "Confirmed", PREPARING: "Preparing",
    READY_FOR_PICKUP: "Ready for pickup", RIDER_ASSIGNED: "Rider assigned", PICKED_UP: "Picked up",
    OUT_FOR_DELIVERY: "Out for delivery", DELIVERED: "Delivered", COLLECTED: "Collected", PAYMENT_FAILED: "Payment failed",
    CANCELLED: "Cancelled", REFUNDED: "Refunded",
  };
  const statusClass = (s) => (["PAYMENT_FAILED", "CANCELLED", "REFUNDED"].includes(s) ? "bad" : ["PENDING_PAYMENT"].includes(s) ? "warn" : "");
  const statusPill = (s) => `<span class="status-pill ${statusClass(s)}">${esc(STATUS_LABEL[s] || s)}</span>`;

  // ---------------------------------------------------------------- location (stored in the browser)
  const LOC_KEY = "foodapp.location";
  const getLocation = () => { try { return JSON.parse(localStorage.getItem(LOC_KEY)); } catch (_) { return null; } };
  const setLocation = (loc) => localStorage.setItem(LOC_KEY, JSON.stringify(loc));
  const locParams = () => { const l = getLocation(); return l ? { lat: l.lat, lng: l.lng } : {}; };
  function detectLocation() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error("Location is not supported on this device."));
      navigator.geolocation.getCurrentPosition(
        (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, label: "Current location" }),
        () => reject(new Error("We couldn't get your location. Check your browser's location permission.")),
        { enableHighAccuracy: true, timeout: 10000 });
    });
  }

  // ---------------------------------------------------------------- shared UI pieces
  const placeholderIcon = (cat) => "bi-egg-fried";
  function imageBlock(url, icon = "bi-egg-fried") {
    return url ? `<div class="ph" style="background-image:url('${esc(url)}')" role="img"></div>`
               : `<div class="ph"><i class="bi ${icon}" aria-hidden="true"></i></div>`;
  }
  function vendorCard(v) {
    return `<a class="v-card" href="/vendor.html?id=${v.id}">
      <div class="pos-rel">${imageBlock(v.cover_url || v.logo_url)}
        ${!v.is_open ? '<span class="badge-closed position-absolute" style="top:.6rem;left:.6rem">Closed</span>' : v.in_zone === false ? '<span class="badge-closed position-absolute" style="top:.6rem;left:.6rem">Too far</span>' : ""}</div>
      <div class="card-body-s"><h3>${esc(v.name)}</h3>
        <div class="meta">${esc(v.business_label || "Restaurant")}${v.area ? " &middot; " + esc(v.area) : ""} &middot; ${v.avg_prep_minutes} min prep</div>
        <div class="d-flex justify-content-between align-items-center mt-auto pt-1">
          <span class="rating"><i class="bi bi-star-fill"></i> ${v.rating_count ? v.rating_avg.toFixed(1) : "New"}</span>
          <span class="meta">${v.distance_km != null ? v.distance_km.toFixed(1) + " km" : ""}${v.delivery_fee != null ? " &middot; " + money(v.delivery_fee) + " delivery" : ""}</span>
        </div></div></a>`;
  }
  function itemCard(i, { showVendor = true } = {}) {
    const price = i.original_price ? `<s>${money(i.original_price)}</s>${money(i.price)}` : money(i.price);
    return `<div class="i-card">
      <div class="pos-rel">${imageBlock(i.image_url)}
        <button class="fav-btn" data-fav-item="${i.id}" aria-label="Save to favorites" aria-pressed="${i.is_favorite}"><i class="bi ${i.is_favorite ? "bi-heart-fill" : "bi-heart"}"></i></button><button class="fav-btn share-btn" data-share-item="${i.id}" data-share-name="${esc(i.name)}" data-share-path="${esc(i.share_path || "")}" aria-label="Share ${esc(i.name)}"><i class="bi bi-share"></i></button></div>
      <div class="card-body-s"><h3>${esc(i.name)}</h3>
        ${showVendor ? `<a class="meta" href="/vendor.html?id=${i.vendor_id}">${esc(i.vendor_name)}</a>` : ""}
        <div class="meta">${i.is_spicy ? '<span class="badge-spicy">' + (["", "Mild", "Spicy", "Hot"][i.spice_level] || "Spicy") + "</span> " : ""}${i.prep_minutes} min${i.distance_km != null ? " &middot; " + i.distance_km.toFixed(1) + " km" : ""}</div>
        ${labelChips(i)}${whyBlock(i)}
        <div class="d-flex justify-content-between align-items-center mt-auto pt-2">
          <span class="price">${price}</span>
          <button class="btn-add" data-add-item="${i.id}" ${i.is_available ? "" : "disabled"}>${i.is_available ? "Add" : "Unavailable"}</button>
        </div></div></div>`;
  }
  const fulfilPill = (o) => (o && o.fulfillment_type === "PICKUP" ? '<span class="status-pill warn"><i class="bi bi-bag-check"></i> Pickup</span>' : "");
  const pretty = (s) => String(s).replace(/_/g, " ");
  const labelChips = (i) => { const l = [...(i.dietary || []), ...(i.traits || [])].slice(0, 3); return l.length ? `<div>${l.map((x) => `<span class="chip-mini">${esc(pretty(x))}</span>`).join("")}</div>` : ""; };
  const whyBlock = (i) => (i.why && i.why.length ? `<details class="why mt-1"><summary>Why this? <span class="status-pill">${i.match_score}% match</span></summary><ul class="small ps-3 mb-0 mt-1">${i.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : "");
  const skeletons = (n = 4) => Array.from({ length: n }, () => '<div class="skeleton-card"></div>').join("");
  const empty = (icon, title, text = "") => `<div class="empty"><i class="bi ${icon}"></i><h2 class="h6">${esc(title)}</h2><p class="mb-0">${esc(text)}</p></div>`;

  /** Wires every Add / heart button inside `root` (event delegation). Requires cart.js for Add. */
  function wireCards(root) {
    root.addEventListener("click", async (e) => {
      if (e.__cardClickHandled) return;          // the page body AND the page content both listen: whichever sees the click first handles it, ONCE
      e.__cardClickHandled = true;
      const add = e.target.closest("[data-add-item]");
      if (add) { e.preventDefault(); return (typeof Cart !== "undefined" ? Cart : undefined) && Cart.add(Number(add.dataset.addItem)); }
      const sh = e.target.closest("[data-share-item]");
      if (sh && (typeof Share !== "undefined" ? Share : undefined) && sh.dataset.sharePath) { e.preventDefault(); return Share.open({ title: sh.dataset.shareName, path: sh.dataset.sharePath, qr: `/api/menu-items/${sh.dataset.shareItem}/qr.svg`, text: `${sh.dataset.shareName} on KORA EATS` }); }
      const fav = e.target.closest("[data-fav-item],[data-fav-vendor]");
      if (fav) {
        e.preventDefault();
        if (!state.user) return location.assign("/auth/login.html?next=" + encodeURIComponent(location.pathname + location.search));
        const key = fav.dataset.favItem ? "menu_item_id" : "vendor_id";
        const id = Number(fav.dataset.favItem || fav.dataset.favVendor);
        const on = fav.getAttribute("aria-pressed") === "true";
        const r = on ? await api("/me/favorites", { method: "DELETE", params: { [key]: id } })
                     : await api("/me/favorites", { method: "POST", body: { [key]: id } });
        if (r.ok) { fav.setAttribute("aria-pressed", String(!on)); fav.querySelector("i").className = "bi " + (on ? "bi-heart" : "bi-heart-fill"); }
        else UI.toast(errorMessage(r), "error");
      }
    });
  }

  // ---------------------------------------------------------------- shell
  const ROLE_HOME = { VENDOR: "/vendor/dashboard.html", RIDER: "/rider/dashboard.html", ADMIN: "/admin/dashboard.html" };
  async function loadUser() {
    const r = await api("/auth/me");
    state.user = r.ok ? r.data : null;
    return state.user;
  }
  /**
   * Builds header + bottom nav around #page. opts: { active, requireLogin, title }
   * Returns the user (or null for guests on public pages).
   */
  async function mountShell(opts = {}) {
    document.body.classList.add("app");
    const user = await loadUser();
    if (opts.requireLogin && !user) { location.replace("/auth/login.html?next=" + encodeURIComponent(location.pathname + location.search)); return null; }
    const link = (href, key, label) => `<a class="nav-link-top ${opts.active === key ? "active" : ""}" href="${href}">${label}</a>`;
    const roleLink = user && ROLE_HOME[user.role] ? `<a class="nav-link-top" href="${ROLE_HOME[user.role]}">${user.role === "ADMIN" ? "Admin" : "My dashboard"}</a>` : "";
    const header = document.createElement("header");
    header.className = "app-header";
    header.innerHTML = `<div class="container d-flex align-items-center gap-2 py-2">
      <a class="brand me-3" href="/"><i class="bi bi-bag-heart-fill text-warning" aria-hidden="true"></i> KORA EATS</a>
      <nav class="d-none d-lg-flex gap-1" aria-label="Main">${link("/", "home", "Home")}${link("/search.html", "search", "Explore")}${link("/decide.html", "decide", "Find my meal")}${link("/orders.html", "orders", "Orders")}${link("/favorites.html", "favorites", "Favorites")}${roleLink}</nav>
      <div class="ms-auto d-flex align-items-center gap-1">
        <a class="icon-btn" href="/cart.html" aria-label="Cart"><i class="bi bi-bag"></i><span id="cart-badge" class="badge-dot d-none">0</span></a>
        ${user ? `<a class="icon-btn" href="/notifications.html" aria-label="Notifications"><i class="bi bi-bell"></i><span id="notif-badge" class="badge-dot d-none">0</span></a>
                  <a class="icon-btn" href="/profile.html" aria-label="Profile"><i class="bi bi-person-circle"></i></a>`
               : `<a class="btn btn-sm btn-outline-light ms-1" href="/auth/login.html?next=${encodeURIComponent(location.pathname + location.search)}">Log in</a>
                  <a class="btn btn-sm btn-warning fw-semibold" href="/auth/register.html?next=${encodeURIComponent(location.pathname + location.search)}">Sign up</a>`}
      </div></div>`;
    document.body.prepend(header);
    const nav = document.createElement("nav");
    nav.className = "bottom-nav"; nav.setAttribute("aria-label", "Primary");
    const b = (href, key, icon, label) => `<a href="${href}" class="${opts.active === key ? "active" : ""}"><i class="bi ${icon}"></i>${label}</a>`;
    nav.innerHTML = b("/", "home", "bi-house-door", "Home") + b("/search.html", "search", "bi-search", "Explore") + b("/cart.html", "cart", "bi-bag", "Cart") + b("/orders.html", "orders", "bi-receipt", "Orders") + b(user ? "/profile.html" : "/auth/login.html", "profile", "bi-person", user ? "Profile" : "Log in");
    document.body.append(nav);
    wireCards(document.body);
    if ((typeof Cart !== "undefined" ? Cart : undefined)) Cart.refreshBadge();
    if (user && (typeof Notifs !== "undefined" ? Notifs : undefined)) Notifs.refreshBadge();
    if ((typeof PWA !== "undefined" ? PWA : undefined)) PWA.init();
    return user;
  }

  return { state, api, call, errorMessage, money, esc, qs, timeAgo, fmtDate, statusPill, STATUS_LABEL, getLocation, setLocation,
           locParams, detectLocation, imageBlock, vendorCard, itemCard, fulfilPill, skeletons, empty, wireCards, mountShell, loadUser, ROLE_HOME };
})();
