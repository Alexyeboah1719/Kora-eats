/* Frontend smoke test. Loads KORA EATS pages in jsdom against a RUNNING server (real API, real data, demo logins) and fails on:
 *   - any uncaught JavaScript error, or a script that references something that doesn't exist
 *   - any 5xx from the API, or an unexpected 4xx
 *   - a page that renders empty, or shows "undefined", "NaN" or "[object Object]" to the user
 *   - an authenticated page that bounces the user to the login screen
 * It also drives the AI screens (afford, craving, split the budget) and every dashboard section.
 *
 *   python -m scripts.seed_dev_data --demo-users    (in backend/, once)
 *   uvicorn app.main:app                            (leave running)
 *   cd frontend-tests && npm install && npm run smoke
 */
const { JSDOM, ResourceLoader, VirtualConsole } = require("jsdom");

const BASE = (process.env.BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const PASSWORD = "Demo!2345";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
let current = null;

process.on("unhandledRejection", (e) => { if (current) current.errors.push("unhandled promise rejection: " + (e && e.stack ? e.stack.split("\n")[0] : e)); });

/** A browser-like cookie jar + fetch for one logged-in (or guest) person. */
class Session {
  constructor() { this.cookies = new Map(); }
  header() { return [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; "); }
  async fetch(path, opts = {}) {
    const url = path.startsWith("http") ? path : BASE + path;
    const headers = { ...(opts.headers || {}), origin: BASE };
    if (this.cookies.size) headers.cookie = this.header();
    const res = await fetch(url, { ...opts, headers, redirect: "manual" });
    for (const c of res.headers.getSetCookie ? res.headers.getSetCookie() : []) { const [kv] = c.split(";"); const i = kv.indexOf("="); const k = kv.slice(0, i), v = kv.slice(i + 1); if (/Max-Age=0|expires=Thu, 01 Jan 1970/i.test(c) || v === "") this.cookies.delete(k); else this.cookies.set(k, v); }
    return res;
  }
  async json(path, opts = {}) { const r = await this.fetch(path, { ...opts, headers: { "content-type": "application/json", ...(opts.headers || {}) }, body: opts.body ? JSON.stringify(opts.body) : undefined }); return { status: r.status, data: await r.json().catch(() => null) }; }
  async login(email) { const r = await this.json("/api/auth/login", { method: "POST", body: { email, password: PASSWORD } }); if (r.status !== 200) throw new Error(`login ${email} failed: ${r.status} ${JSON.stringify(r.data)}`); return this; }
}

/** An "anything goes" stand-in for CDN libraries we don't load here (Bootstrap JS, Leaflet, Chart.js). */
function stub() { const f = function () {}; return new Proxy(f, { get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : stub()), apply: () => stub(), construct: () => stub() }); }

async function openPage(session, path, { loc = true } = {}) {
  const errors = [], calls = [], navigations = [];
  const entry = { path, errors, calls, navigations, warnings: [] };
  current = entry;
  const html = await (await fetch(BASE + path.replace(/#.*/, ""))).text().catch(() => "");
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { const m = String(e.message || e); if (/Not implemented: navigation/i.test(m)) navigations.push(m); else if (!/Not implemented/i.test(m)) errors.push(m.split("\n")[0]); });
  vc.on("error", (m) => errors.push(String(m).split("\n")[0]));
  let pending = 0;
  class Loader extends ResourceLoader {
    fetch(url) { return url.startsWith(BASE) ? fetch(url).then((r) => r.arrayBuffer()).then((b) => Buffer.from(b)) : Promise.resolve(Buffer.from("")); }
  }
  const dom = new JSDOM(html, {
    url: BASE + path, runScripts: "dangerously", resources: new Loader(), pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, o = {}) => {
        pending++;
        try { const r = await session.fetch(String(u).startsWith("http") ? u : new URL(u, BASE).href, o); calls.push({ url: String(u), status: r.status, method: o.method || "GET" }); return r; }
        finally { pending--; }
      };
      Object.assign(w, { bootstrap: stub(), L: stub(), Chart: stub() });
      w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
      w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.scrollTo = () => {}; w.print = () => {};
      w.addEventListener("error", (e) => errors.push(`${e.message}${e.filename ? " (" + e.filename.replace(BASE, "") + ":" + e.lineno + ")" : ""}`));
      if (loc) w.localStorage.setItem("foodapp.location", JSON.stringify({ lat: 5.557, lng: -0.182, label: "Osu, Accra" }));
    },
  });
  entry.dom = dom;
  entry.settle = async (ms = 450, limit = 9000) => { const t0 = Date.now(); let quiet = 0; while (Date.now() - t0 < limit) { await sleep(60); quiet = pending === 0 ? quiet + 60 : 0; if (quiet >= ms) break; } };
  await sleep(150);
  await entry.settle();
  return entry;
}

const BAD_TEXT = /\bundefined\b|\bNaN\b|\[object Object\]|Something went wrong/;
function check(entry, label, { authed = false, expect = null, root = "#page, main, #dash-root, .dash-main" } = {}) {
  const w = entry.dom.window, doc = w.document, problems = [];
  problems.push(...entry.errors.map((e) => "JS error: " + e));
  for (const c of entry.calls) { if (c.status >= 500) problems.push(`API ${c.method} ${c.url} -> ${c.status}`); else if (c.status >= 400 && c.status !== 401 && !/\/auth\//.test(c.url) && !(c.status === 404 && /\/application$/.test(c.url))) problems.push(`API ${c.method} ${c.url} -> ${c.status}`); }
  if (authed && entry.navigations.length) problems.push("bounced to the login page (not signed in?)");
  const el = doc.querySelector(root) || doc.body;
  const text = (el.textContent || "").replace(/\s+/g, " ").trim();
  if (text.length < 15) problems.push("page rendered (almost) nothing");
  const bad = text.match(BAD_TEXT);
  if (bad) problems.push(`page shows "${bad[0]}" to the user: ...${text.slice(Math.max(0, text.search(BAD_TEXT) - 40), text.search(BAD_TEXT) + 40)}...`);
  if (expect) { const n = doc.querySelectorAll(expect.sel).length; if (n < (expect.min ?? 1)) problems.push(`expected at least ${expect.min ?? 1} of "${expect.sel}" but found ${n}`); }
  results.push({ label: label || entry.path, problems });
  return problems;
}
const close = (entry) => { try { entry.dom.window.close(); } catch (_) { /* ignore */ } };
// a missing button is a finding ("the screen is broken"), not a crash of the test
const click = (entry, sel) => { const e = entry.dom.window.document.querySelector(sel); if (!e) { entry.errors.push(`expected an element "${sel}" to click, but it isn't on the page`); return false; } e.dispatchEvent(new entry.dom.window.MouseEvent("click", { bubbles: true, cancelable: true })); return true; };
const setValue = (entry, sel, v) => { const e = entry.dom.window.document.querySelector(sel); if (!e) { entry.errors.push(`expected an input "${sel}", but it isn't on the page`); return false; } e.value = v; e.dispatchEvent(new entry.dom.window.Event("input", { bubbles: true })); e.dispatchEvent(new entry.dom.window.Event("change", { bubbles: true })); return true; };

async function main() {
  const health = await fetch(BASE + "/api/health").then((r) => r.status).catch(() => 0);
  if (health !== 200) { console.error(`Server not reachable at ${BASE} (health=${health}). Start it first, with demo users seeded.`); process.exit(2); }
  const guest = new Session();
  const [customer, vendor, rider, admin] = await Promise.all([new Session().login("customer@demo.kora"), new Session().login("vendor@demo.kora"), new Session().login("rider@demo.kora"), new Session().login("admin@demo.kora")]);
  const vendors = (await guest.json("/api/vendors?lat=5.557&lng=-0.182")).data || [];
  if (!vendors.length) throw new Error("No vendors found: run `python -m scripts.seed_dev_data --demo-users` first.");
  const vid = vendors[0].id;

  // data the customer pages need: an address, a cart, an order, a group
  const addr = (await customer.json("/api/me/addresses", { method: "POST", body: { label: "Home", address_line: "12 Oxford St", city: "Accra", latitude: 5.557, longitude: -0.182, is_default: true } })).data;
  const items = (await guest.json(`/api/menu-items?vendor_id=${vid}`)).data;
  await customer.json("/api/cart/items", { method: "POST", body: { menu_item_id: items[0].id, quantity: 1 } });
  const order = (await customer.json("/api/orders", { method: "POST", body: { address_id: addr.id } })).data;
  const group = (await customer.json("/api/groups", { method: "POST", body: { vendor_id: vid } })).data;
  await customer.json("/api/cart/items", { method: "POST", body: { menu_item_id: items[0].id, quantity: 1 } });

  const pages = [
    [guest, "/", false], [guest, "/search.html", false], [guest, "/search.html?q=chicken", false], [guest, `/vendor.html?id=${vid}`, false, { sel: ".i-card", min: 1 }],
    [guest, "/decide.html", false], [guest, "/decide.html?tab=feed", false, { sel: ".i-card", min: 1 }], [guest, "/split.html", false], [guest, "/auth/login.html", false], [guest, "/auth/register.html", false], [guest, "/offline.html", false],
    [customer, "/cart.html", true], [customer, "/checkout.html", true], [customer, "/orders.html", true], [customer, `/order.html?id=${order.id}`, true],
    [customer, "/profile.html", true], [customer, "/addresses.html", true], [customer, "/food-profile.html", true], [customer, "/favorites.html", true], [customer, "/notifications.html", true], [customer, "/payments.html", true],
    [customer, `/group/${group.code}`, true, { sel: "#gai", min: 1 }], [customer, "/vendor-apply.html", true], [customer, "/rider-apply.html", true],
  ];
  for (const [session, path, authed, expect] of pages) {
    const e = await openPage(session, path); const p = check(e, path, { authed, expect }); close(e);
    process.stdout.write(p.length ? "x" : ".");
  }

  // dashboards: every section
  for (const [session, path, label] of [[vendor, "/vendor/dashboard.html", "vendor"], [rider, "/rider/dashboard.html", "rider"], [admin, "/admin/dashboard.html", "admin"]]) {
    const e = await openPage(session, path);
    const secs = [...e.dom.window.document.querySelectorAll("[data-sec]")].map((a) => a.dataset.sec);
    check(e, `${label} dashboard (first section)`, { authed: true });
    for (const s of secs) {
      e.errors.length = 0; e.calls.length = 0; e.navigations.length = 0;
      click(e, `[data-sec="${s}"]`); await e.settle(600);
      const p = check(e, `${label} dashboard > ${s}`, { authed: true, root: "#dash-content, .dash-main, main, #page" });
      process.stdout.write(p.length ? "x" : ".");
    }
    close(e);
  }

  // buttons that must really DO something (a page that merely loads can still be dead: this caught "Add to cart" doing nothing)
  const sawCall = (e, re, method) => e.calls.some((c) => re.test(c.url) && (!method || c.method === method) && c.status < 400);
  for (const [path, label] of [[`/vendor.html?id=${vid}`, "vendor page"], ["/search.html", "search page"], ["/", "home page"]]) {
    await customer.json("/api/cart", { method: "DELETE" });
    const e = await openPage(customer, path);
    e.calls.length = 0;
    const btn = e.dom.window.document.querySelector("button[data-add-item]:not([disabled])");
    if (!btn) e.errors.push(`no "Add" button on the ${label}`); else btn.dispatchEvent(new e.dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    await e.settle(500);
    const cartNow = (await customer.json("/api/cart")).data;
    if (!sawCall(e, /\/api\/cart\/items$/, "POST")) e.errors.push(`clicking "Add" on the ${label} sent nothing to the server`);
    if (e.calls.filter((c) => c.method === "POST" && /\/api\/cart\/items$/.test(c.url)).length !== 1) e.errors.push(`one click on "Add" on the ${label} sent ${e.calls.filter((c) => c.method === "POST" && /\/api\/cart\/items$/.test(c.url)).length} requests (should be exactly 1)`);
    if (cartNow && cartNow.items.length && cartNow.items[0].quantity !== 1) e.errors.push(`one click added ${cartNow.items[0].quantity} portions on the ${label} (should be 1)`);
    if (!cartNow || !cartNow.items.length) e.errors.push(`after clicking "Add" on the ${label}, the cart is still empty`);
    const badge = e.dom.window.document.getElementById("cart-badge");
    if (badge && badge.classList.contains("d-none")) e.errors.push(`the cart badge did not update on the ${label}`);
    const p = check(e, `click: Add to cart (${label})`, { authed: true }); close(e); process.stdout.write(p.length ? "x" : ".");
  }
  {
    const e = await openPage(customer, "/cart.html");
    const p = check(e, "click-through: the cart shows what was added", { authed: true, expect: { sel: ".summary-row", min: 1 } }); close(e); process.stdout.write(p.length ? "x" : ".");
    const c = await openPage(customer, "/checkout.html");
    c.calls.length = 0; setValue(c, "#phone", "0244000111"); click(c, "#place"); await c.settle(800);
    if (!sawCall(c, /\/api\/orders$/, "POST")) c.errors.push("clicking 'Place order' did not create an order");
    const q = check(c, "click: Place order from the checkout page", {});   // the page then moves on to payment, so a navigation here is expected close(c); process.stdout.write(q.length ? "x" : ".");
  }
  for (const [session, path, label] of [[customer, "/addresses.html", "addresses"], [customer, "/vendor-apply.html", "vendor application"]]) {
    const e = await openPage(session, path);
    if (!e.dom.window.document.querySelector("#map-slot .map-box")) e.errors.push(`the location map picker never appeared on the ${label} page`);
    const p = check(e, `map picker present (${label})`, {}); close(e); process.stdout.write(p.length ? "x" : ".");
  }

  // the AI screens, driven like a person would
  let e = await openPage(customer, "/decide.html");
  setValue(e, "#budget", "60"); click(e, "#go"); await e.settle(700);
  check(e, "AI: What can I afford? (GH₵60)", { authed: true, expect: { sel: "#results .i-card", min: 1 } }); close(e);
  e = await openPage(customer, "/decide.html?tab=craving");
  click(e, '[data-crave="filling"]'); click(e, "#go"); await e.settle(700);
  check(e, "AI: Craving diagnosis (filling)", { authed: true, expect: { sel: "#results .i-card", min: 1 } }); close(e);
  e = await openPage(guest, "/split.html");
  click(e, "#go"); await e.settle(900);
  check(e, "AI: Split the budget (5 people, GH₵150)", { expect: { sel: "[data-plan]", min: 1 } }); close(e);
  e = await openPage(customer, `/group/${group.code}`);
  click(e, "#gs-go"); await e.settle(900);
  check(e, "AI: Group food AI suggestions", { authed: true, expect: { sel: "#gs-out [data-plan]", min: 1 } }); close(e);
  process.stdout.write("\n");

  const failed = results.filter((r) => r.problems.length);
  console.log(`\nChecked ${results.length} screens: ${results.length - failed.length} clean, ${failed.length} with problems.`);
  for (const f of failed) { console.log(`\n  x ${f.label}`); [...new Set(f.problems)].slice(0, 6).forEach((p) => console.log("      - " + p)); }
  process.exit(failed.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(2); });
