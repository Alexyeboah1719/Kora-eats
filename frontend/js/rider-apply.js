(async () => {
  const user = await App.mountShell({ active: "profile", requireLogin: true });
  if (!user) return;
  const root = document.getElementById("page");
  if (user.role === "RIDER") return location.replace("/rider/dashboard.html");
  if (user.role !== "CUSTOMER") { root.innerHTML = App.empty("bi-lock", "Rider applications are for customer accounts", ""); return; }
  const r = await App.api("/rider/application");
  const app = r.ok ? r.data : null;
  const pending = app && app.approval_status === "PENDING";
  root.innerHTML = `<h1 class="h4">Deliver with KORA EATS</h1><p class="meta">Earn delivering food in your area. Applications are reviewed by our team.</p>
    ${pending ? `<div class="panel mb-3" style="border-color:var(--gold)"><span class="status-pill warn">Under review</span><p class="mt-2 mb-0 meta">We'll email you when a decision is made.${app.has_document ? "" : " Please upload your ID below."}</p></div>` : ""}
    ${app && app.approval_status === "REJECTED" ? `<div class="panel mb-3" style="border-color:var(--bad)"><span class="status-pill bad">Not approved</span><p class="mt-2 mb-0"><strong>Reason:</strong> ${App.esc(app.rejection_reason)}</p></div>` : ""}
    <form class="panel" id="rform" novalidate><div class="row g-3">
      ${pending ? "" : `<div class="col-md-6"><label class="form-label" for="phone">Phone</label><input class="form-control" id="phone" required inputmode="tel" value="${App.esc(app?.phone || "")}" placeholder="+233 20 000 0000"></div>
      <div class="col-md-6"><label class="form-label" for="vehicle">Vehicle</label><select class="form-select" id="vehicle">${["MOTORBIKE", "BICYCLE", "CAR", "ON_FOOT"].map((v) => `<option value="${v}" ${app?.vehicle_type === v ? "selected" : ""}>${v.replace("_", " ")}</option>`).join("")}</select></div>
      <div class="col-md-6"><label class="form-label" for="plate">Plate number (if any)</label><input class="form-control" id="plate" value="${App.esc(app?.plate_number || "")}"></div>`}
      <div class="col-md-6"><label class="form-label" for="idfile">ID card or driver's licence (PDF/JPG/PNG)</label><input class="form-control" id="idfile" type="file" accept="application/pdf,image/jpeg,image/png,image/webp"></div>
      <div class="col-12"><div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark" id="submit">${pending ? "Upload ID" : "Submit application"}</button></div></div></form>`;
  document.getElementById("rform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = document.getElementById("form-alert"), btn = document.getElementById("submit"); a.classList.add("d-none"); btn.disabled = true;
    const fail = (m) => { a.textContent = m; a.classList.remove("d-none"); btn.disabled = false; };
    const file = document.getElementById("idfile").files[0];
    if (!pending) {
      if (!file && !app?.has_document) return fail("Upload your ID so we can verify you.");
      const res = await App.api("/rider/apply", { method: "POST", body: { phone: phone.value.trim(), vehicle_type: vehicle.value, plate_number: plate.value.trim() || null } });
      if (!res.ok) return fail(App.errorMessage(res));
    } else if (!file) return fail("Choose a file first.");
    if (file) { const fd = new FormData(); fd.append("file", file); const u = await App.api("/rider/id-document", { method: "POST", form: fd }); if (!u.ok) return fail(App.errorMessage(u)); }
    UI.toast("Saved. We'll email you after review."); setTimeout(() => location.reload(), 900);
  });
})();
