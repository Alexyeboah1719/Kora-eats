Dash.register("admin", { id: "settings", label: "System settings", icon: "bi-sliders", async render(el) {
  const list = await App.call("/admin/settings"); if (!list) return;
  el.innerHTML = `<form class="panel" id="sf" style="max-width:640px"><p class="meta">Changes apply to all servers within a minute. Secrets and API keys are never editable here; they live in the server's environment.</p>
    ${list.map((s) => `<div class="mb-3"><label class="form-label" for="s-${s.key}">${App.esc(s.label)}</label><input class="form-control" id="s-${s.key}" type="number" step="${s.type === "int" ? 1 : 0.5}" min="${s.min}" max="${s.max}" value="${s.value}" data-key="${s.key}" data-orig="${s.value}"><div class="hint">Allowed: ${s.min} to ${s.max}</div></div>`).join("")}
    <button class="btn btn-dark">Save changes</button></form>`;
  el.querySelector("#sf").addEventListener("submit", async (e) => { e.preventDefault(); let n = 0;
    for (const i of el.querySelectorAll("[data-key]")) { if (i.value === i.dataset.orig) continue;
      if (!(await App.call(`/admin/settings/${i.dataset.key}`, { method: "PUT", body: { value: Number(i.value) } }))) return; i.dataset.orig = i.value; n++; }
    UI.toast(n ? `${n} setting(s) saved.` : "Nothing changed."); });
} });
