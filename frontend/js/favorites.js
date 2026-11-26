(async () => {
  if (!(await App.mountShell({ active: "favorites", requireLogin: true }))) return;
  const root = document.getElementById("page");
  root.innerHTML = `<h1 class="h4">Favorites</h1><div class="section-title"><span>Vendors</span></div><div class="card-grid" id="fv">${App.skeletons(2)}</div>
    <div class="section-title"><span>Foods</span></div><div class="card-grid" id="fi">${App.skeletons(2)}</div>`;
  App.wireCards(root);
  const f = await App.call("/me/favorites");
  if (!f) return;
  document.getElementById("fv").innerHTML = f.vendors.length ? f.vendors.map(App.vendorCard).join("") : App.empty("bi-heart", "No favorite vendors yet", "Tap the heart on a vendor to save it.");
  document.getElementById("fi").innerHTML = f.items.length ? f.items.map((i) => App.itemCard(i)).join("") : App.empty("bi-heart", "No favorite foods yet", "Tap the heart on a meal to save it.");
})();
