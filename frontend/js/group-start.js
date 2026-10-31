/* Adds a "Start a group order" button to vendor pages. */
window.addEventListener("vendor:loaded", (e) => {
  const v = e.detail, slot = document.getElementById("vendor-actions");
  if (!slot || !v.is_open) return;
  slot.innerHTML = `<button class="btn btn-sm btn-outline-dark" id="start-group"><i class="bi bi-people"></i> Order together (group order)</button>`;
  document.getElementById("start-group").addEventListener("click", async () => {
    if (!App.state.user) return location.assign("/auth/login.html?next=" + encodeURIComponent(location.pathname + location.search));
    const g = await App.call("/groups", { method: "POST", body: { vendor_id: v.id } });
    if (g) location.assign(g.share_path);
  });
});
