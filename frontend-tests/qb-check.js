/* Focused check: the mobile quick bar shows cart + ongoing-order counts for a logged-in user. */
const { JSDOM, ResourceLoader, VirtualConsole } = require("jsdom");
const BASE = "http://localhost:8000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Session {
  constructor() { this.cookies = new Map(); }
  header() { return [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; "); }
  async fetch(path, opts = {}) {
    const url = path.startsWith("http") ? path : BASE + path;
    const headers = { ...(opts.headers || {}), origin: BASE };
    if (this.cookies.size) headers.cookie = this.header();
    const res = await fetch(url, { ...opts, headers, redirect: "manual" });
    for (const c of res.headers.getSetCookie ? res.headers.getSetCookie() : []) {
      const [kv] = c.split(";"); const i = kv.indexOf("="); const k = kv.slice(0, i), v = kv.slice(i + 1);
      if (/Max-Age=0|expires=Thu, 01 Jan 1970/i.test(c) || v === "") this.cookies.delete(k); else this.cookies.set(k, v);
    }
    return res;
  }
  async json(path, opts = {}) {
    const r = await this.fetch(path, { ...opts, headers: { "content-type": "application/json", ...(opts.headers || {}) }, body: opts.body ? JSON.stringify(opts.body) : undefined });
    return { status: r.status, data: await r.json().catch(() => null) };
  }
}

async function openPage(session, path) {
  const errors = [];
  const html = await (await fetch(BASE + path)).text();
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(String(e.message || e).split("\n")[0]));
  vc.on("error", (m) => errors.push(String(m).split("\n")[0]));
  class Loader extends ResourceLoader { fetch(url) { return url.startsWith(BASE) ? fetch(url).then((r) => r.arrayBuffer()).then((b) => Buffer.from(b)) : Promise.resolve(Buffer.from("")); } }
  const dom = new JSDOM(html, { url: BASE + path, runScripts: "dangerously", resources: new Loader(), pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, o = {}) => session.fetch(String(u).startsWith("http") ? u : new URL(u, BASE).href, o);
      w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
      w.scrollTo = () => {}; w.alert = () => {}; w.confirm = () => true;
    } });
  // let async shell + API calls settle
  for (let i = 0; i < 40; i++) { await sleep(250); if (dom.window.document.querySelector(".quick-bar #qb-cart-n")) break; }
  await sleep(500);
  return { dom, errors };
}

(async () => {
  const s = new Session();
  const email = `qbtest${Date.now()}@test.dev`;
  let r = await s.json("/api/auth/register", { method: "POST", body: { full_name: "QB Test", email, phone: "0244000999", password: "Test!23456", confirm_password: "Test!23456" } });
  console.log("register:", r.status, JSON.stringify(r.data).slice(0, 120));
  r = await s.json("/api/auth/login", { method: "POST", body: { email, password: "Test!23456" } });
  console.log("login:", r.status);
  if (r.status !== 200) process.exit(1);

  const items = (await s.json("/api/menu-items?vendor_id=1")).data || [];
  if (!items.length) { console.log("no menu items for vendor 1"); process.exit(1); }
  await s.json("/api/cart/items", { method: "POST", body: { menu_item_id: items[0].id, quantity: 2 } });
  const cart = (await s.json("/api/cart")).data;
  console.log("cart item_count:", cart && cart.item_count);

  // place an order so the ongoing-orders badge has something to count (pickup: vendor is outside the test address's delivery radius)
  const order = (await s.json("/api/orders", { method: "POST", body: { fulfillment_type: "PICKUP", payment_mode: "COD" } })).data;
  console.log("order placed:", order && order.id, order && order.status, JSON.stringify(order && order.detail || "").slice(0, 120));

  const { dom, errors } = await openPage(s, "/search.html");
  const d = dom.window.document;
  const qb = d.querySelector(".quick-bar");
  console.log("quick-bar present:", !!qb);
  if (qb) {
    const cn = d.getElementById("qb-cart-n"), on = d.getElementById("qb-orders-n");
    console.log("cart badge text:", cn && cn.textContent, "hidden:", cn && cn.classList.contains("d-none"));
    console.log("orders badge text:", on && on.textContent, "hidden:", on && on.classList.contains("d-none"));
    console.log("qb bottom style:", qb.style.bottom, "| body class:", d.body.className);
  }
  console.log("js errors:", errors.length ? errors.slice(0, 5) : "none");
  dom.window.close();
})().catch((e) => { console.error(e); process.exit(1); });
