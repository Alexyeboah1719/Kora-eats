Dash.register("admin", { id: "notify", label: "Send announcement", icon: "bi-megaphone", async render(el) {
  el.innerHTML = `<form class="panel" id="bf" style="max-width:560px"><p class="meta">Sends an in-app notification (and a push notification to devices that enabled it) to a group of users. Max 2,000 recipients per send.</p>
    <div class="mb-2"><label class="form-label" for="b-aud">Send to</label><select class="form-select" id="b-aud"><option value="CUSTOMERS">All customers</option><option value="VENDORS">All vendors</option><option value="RIDERS">All riders</option><option value="ALL">Everyone</option></select></div>
    <div class="mb-2"><label class="form-label" for="b-title">Title</label><input class="form-control" id="b-title" maxlength="100" required></div>
    <div class="mb-2"><label class="form-label" for="b-body">Message</label><textarea class="form-control" id="b-body" rows="3" maxlength="280" required></textarea></div>
    <div class="mb-3"><label class="form-label" for="b-url">Open this page when tapped (optional)</label><input class="form-control" id="b-url" placeholder="/search.html" maxlength="100"></div>
    <div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark">Send announcement</button></form>`;
  el.querySelector("#bf").addEventListener("submit", async (e) => { e.preventDefault(); const a = el.querySelector("#form-alert"); a.classList.add("d-none");
    if (!confirm("Send this announcement now?")) return;
    const r = await App.api("/admin/notifications/broadcast", { method: "POST", body: { title: el.querySelector("#b-title").value.trim(), body: el.querySelector("#b-body").value.trim(), url: el.querySelector("#b-url").value.trim() || null, audience: el.querySelector("#b-aud").value } });
    if (!r.ok) { a.textContent = App.errorMessage(r); return a.classList.remove("d-none"); }
    UI.toast(`Sent to ${r.data.sent_to} user(s).`); e.target.reset(); });
} });
