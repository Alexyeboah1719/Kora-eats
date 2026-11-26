(async () => {
  if (!(await App.mountShell({ active: "profile", requireLogin: true }))) return;
  const root = document.getElementById("page");
  const orders = (await App.call("/orders")) || [];
  const pre = App.qs("order");
  root.innerHTML = `<h1 class="h4">Help &amp; support</h1><div class="row g-3"><div class="col-lg-5"><form class="panel" id="tf" novalidate><h2 class="h6">Contact us</h2>
      <label class="form-label" for="t-order">About an order (optional)</label><select class="form-select mb-2" id="t-order"><option value="">General question</option>${orders.map((o) => `<option value="${o.id}" ${String(o.id) === pre ? "selected" : ""}>${App.esc(o.code)} - ${App.esc(o.vendor.name)}</option>`).join("")}</select>
      <label class="form-label" for="t-sub">Subject</label><input class="form-control mb-2" id="t-sub" maxlength="120" required>
      <label class="form-label" for="t-msg">How can we help?</label><textarea class="form-control mb-2" id="t-msg" rows="5" maxlength="3000" required></textarea>
      <div id="form-alert" class="alert alert-danger d-none" role="alert"></div><button class="btn btn-dark">Send message</button></form></div>
    <div class="col-lg-7"><h2 class="h6">Your conversations</h2><div id="list">${App.skeletons(1)}</div></div></div>`;
  async function load() {
    const list = await App.call("/support/tickets"); if (!list) return;
    document.getElementById("list").innerHTML = list.length ? list.map((t) => `<div class="panel mb-2"><div class="d-flex justify-content-between"><strong>${App.esc(t.subject)}</strong><span class="status-pill ${t.status === "OPEN" ? "warn" : ""}">${t.status === "OPEN" ? "Waiting for reply" : "Answered"}</span></div>
      <div class="meta">${App.fmtDate(t.created_at)}</div><p class="my-2">${App.esc(t.message)}</p>${t.admin_reply ? `<div class="panel" style="background:#f5f7f4;border-left:4px solid var(--ok)"><strong class="small">KORA EATS support</strong><div>${App.esc(t.admin_reply)}</div></div>` : ""}</div>`).join("")
      : App.empty("bi-chat-dots", "No messages yet", "Questions about an order? Send us a message.");
  }
  document.getElementById("tf").addEventListener("submit", async (e) => {
    e.preventDefault(); const a = document.getElementById("form-alert"); a.classList.add("d-none");
    const o = document.getElementById("t-order").value;
    const r = await App.api("/support/tickets", { method: "POST", body: { subject: document.getElementById("t-sub").value.trim(), message: document.getElementById("t-msg").value.trim(), order_id: o ? Number(o) : null } });
    if (!r.ok) { a.textContent = App.errorMessage(r); return a.classList.remove("d-none"); }
    UI.toast("Message sent. We'll reply here and by email."); e.target.reset(); load();
  });
  load();
})();
