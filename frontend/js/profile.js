(async () => {
  const user = await App.mountShell({ active: "profile", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  const p = await App.call("/me/profile");
  if (!p) return;
  const roleLinks = {
    CUSTOMER: `<a class="panel d-block text-decoration-none mb-2" href="/vendor-apply.html"><i class="bi bi-shop"></i> <strong>Sell on KORA EATS</strong><br><span class="meta">Apply to open your restaurant</span></a>
               <a class="panel d-block text-decoration-none mb-2" href="/rider-apply.html"><i class="bi bi-bicycle"></i> <strong>Deliver with KORA EATS</strong><br><span class="meta">Apply to become a rider</span></a>`,
  }[user.role] || `<a class="panel d-block text-decoration-none mb-2" href="${App.ROLE_HOME[user.role]}"><i class="bi bi-speedometer2"></i> <strong>Go to my dashboard</strong></a>`;
  root.innerHTML = `<h1 class="h4">Profile</h1><div class="row g-3"><div class="col-lg-6">
    <form class="panel" id="pform" novalidate>
      <div class="d-flex justify-content-between mb-3"><span class="text-break">${App.esc(p.email)}</span><span class="status-pill">${App.esc(p.role)}</span></div>
      <div class="mb-2"><label class="form-label" for="name">Full name</label><input class="form-control" id="name" value="${App.esc(p.full_name)}" required></div>
      <div class="mb-3"><label class="form-label" for="phone">Phone</label><input class="form-control" id="phone" value="${App.esc(p.phone || "")}" placeholder="+233 20 000 0000" inputmode="tel"></div>
      <div id="form-alert" class="alert alert-danger d-none" role="alert"></div>
      <button class="btn btn-dark" type="submit">Save changes</button></form></div>
    <div class="col-lg-6">${roleLinks}
      <a class="panel d-block text-decoration-none mb-2" href="/food-profile.html"><i class="bi bi-stars"></i> <strong>My food profile</strong><br><span class="meta">Diet, allergens and what KORA EATS has learned</span></a>
      <a class="panel d-block text-decoration-none mb-2" href="/addresses.html"><i class="bi bi-geo-alt"></i> <strong>My addresses</strong></a>
      <a class="panel d-block text-decoration-none mb-2" href="/payments.html"><i class="bi bi-credit-card"></i> <strong>Payment history</strong></a>
      <a class="panel d-block text-decoration-none mb-2" href="/support.html"><i class="bi bi-life-preserver"></i> <strong>Help &amp; support</strong></a>
      <div id="install-slot"></div>
      <button class="btn btn-outline-danger w-100 mt-2" id="logout" type="button"><i class="bi bi-box-arrow-right"></i> Log out</button></div></div>`;
  document.getElementById("pform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = document.getElementById("form-alert"); a.classList.add("d-none");
    const r = await App.api("/me/profile", { method: "PUT", body: { full_name: name.value.trim(), phone: phone.value.trim() || null } });
    if (r.ok) UI.toast("Profile updated."); else { a.textContent = App.errorMessage(r); a.classList.remove("d-none"); }
  });
  document.getElementById("logout").addEventListener("click", async () => { await App.api("/auth/logout", { method: "POST" }); location.assign("/auth/login.html"); });
})();
