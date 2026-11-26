/* Global cart helper loaded on every customer page (adds to cart + header badge). */
const Cart = (() => {
  const setBadge = (n) => { const b = document.getElementById("cart-badge"); if (!b) return; b.textContent = n; b.classList.toggle("d-none", !n); };
  async function refreshBadge() {
    if (!App.state.user) return setBadge(0);
    const r = await App.api("/cart");
    setBadge(r.ok ? r.data.item_count : 0);
  }
  async function add(itemId, quantity = 1) {
    if (!App.state.user) return location.assign("/auth/login.html?next=" + encodeURIComponent(location.pathname + location.search));
    let r = await App.api("/cart/items", { method: "POST", body: { menu_item_id: itemId, quantity } });
    if (!r.ok && r.data?.detail?.code === "different_vendor") {
      if (!confirm(r.data.detail.message)) return;
      r = await App.api("/cart/items", { method: "POST", body: { menu_item_id: itemId, quantity, replace: true } });
    }
    if (!r.ok) return UI.toast(App.errorMessage(r), "error");
    setBadge(r.data.item_count);
    UI.toast("Added to cart.");
  }
  return { add, refreshBadge, setBadge };
})();
