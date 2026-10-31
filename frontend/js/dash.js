/* Dashboard framework shared by vendor / rider / admin. Each section file calls
   Dash.register(area, {id, label, icon, render(el)}); Dash.init(area, opts) builds the layout. */
const Dash = (() => {
  const registry = {};
  const register = (area, section) => (registry[area] ||= []).push(section);

  async function init(area, { title, roles, home = "/" }) {
    document.body.classList.add("dash-body");
    const user = await App.loadUser();
    if (!user) return location.replace("/auth/login.html?next=" + encodeURIComponent(location.pathname));
    if (!roles.includes(user.role)) {
      document.getElementById("dash-root").innerHTML = `<div class="container py-5">${App.empty("bi-lock", "This area isn't available for your account", "")}<div class="text-center"><a class="btn btn-dark" href="/">Back to KORA EATS</a></div></div>`;
      return null;
    }
    const sections = registry[area] || [];
    const root = document.getElementById("dash-root");
    root.innerHTML = `<div class="dash"><aside class="dash-side">
        <a class="brand" href="/"><i class="bi bi-bag-heart-fill text-warning"></i> KORA EATS</a>
        <div class="who">${App.esc(title)} &middot; ${App.esc(user.full_name)}</div>
        <nav class="dash-nav" aria-label="${App.esc(title)}">${sections.map((s) => `<a href="#${s.id}" data-sec="${s.id}"><i class="bi ${s.icon}"></i>${App.esc(s.label)}</a>`).join("")}
          <a href="/" ><i class="bi bi-house-door"></i>Customer app</a>
          <a href="#" id="dash-logout"><i class="bi bi-box-arrow-right"></i>Log out</a></nav></aside>
      <main class="dash-main" id="dash-main" tabindex="-1"></main></div>`;
    document.getElementById("dash-logout").addEventListener("click", async (e) => { e.preventDefault(); await App.api("/auth/logout", { method: "POST" }); location.assign("/auth/login.html"); });
    let current = null;
    async function show() {
      const id = location.hash.slice(1) || sections[0].id;
      const sec = sections.find((s) => s.id === id) || sections[0];
      root.querySelectorAll("[data-sec]").forEach((a) => a.classList.toggle("active", a.dataset.sec === sec.id));
      const el = document.getElementById("dash-main");
      el.innerHTML = `<h1 class="h4 mb-3">${App.esc(sec.label)}</h1><div id="sec-body">${App.skeletons(2)}</div>`;
      if (current && current.teardown) current.teardown();
      current = sec;
      await sec.render(document.getElementById("sec-body"), user);
    }
    window.addEventListener("hashchange", show);
    await show();
    return user;
  }

  /** Bootstrap modal with a form. fields: [{name,label,type,value,options,required,hint}] -> resolves values or null. */
  function formModal({ title, fields, submitLabel = "Save" }) {
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      const inputs = fields.map((f) => {
        const id = "fm-" + f.name, v = f.value ?? "";
        if (f.type === "checks") {
          const have = Array.isArray(f.value) ? f.value : [];
          return `<fieldset class="mb-3 border rounded p-2"><legend class="float-none w-auto fs-6 px-1 mb-0">${App.esc(f.label)}</legend>${f.hint ? `<div class="hint mb-1">${App.esc(f.hint)}</div>` : ""}${f.options.map((o) => `<div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" data-grp="${f.name}" value="${App.esc(o.value)}" id="fm-${f.name}-${App.esc(o.value)}" ${have.includes(o.value) ? "checked" : ""}><label class="form-check-label" for="fm-${f.name}-${App.esc(o.value)}">${App.esc(o.label)}</label></div>`).join("")}</fieldset>`;
        }
        const ctl = f.type === "select" ? `<select class="form-select" id="${id}">${f.options.map((o) => `<option value="${App.esc(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${App.esc(o.label)}</option>`).join("")}</select>`
          : f.type === "textarea" ? `<textarea class="form-control" id="${id}" rows="3">${App.esc(v)}</textarea>`
          : f.type === "checkbox" ? `<div class="form-check"><input class="form-check-input" type="checkbox" id="${id}" ${v ? "checked" : ""}><label class="form-check-label" for="${id}">${App.esc(f.label)}</label></div>`
          : `<input class="form-control" id="${id}" type="${f.type || "text"}" value="${App.esc(v)}" ${f.step ? `step="${f.step}"` : ""} ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.required ? "required" : ""} ${f.maxlength ? `maxlength="${f.maxlength}"` : ""}>`;
        return f.type === "checkbox" ? `<div class="mb-3">${ctl}</div>` : `<div class="mb-3"><label class="form-label" for="${id}">${App.esc(f.label)}</label>${ctl}${f.hint ? `<div class="hint">${App.esc(f.hint)}</div>` : ""}</div>`;
      }).join("");
      wrap.innerHTML = `<div class="modal fade" tabindex="-1" aria-modal="true"><div class="modal-dialog modal-dialog-centered modal-dialog-scrollable"><form class="modal-content" novalidate>
        <div class="modal-header"><h2 class="modal-title h5">${App.esc(title)}</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>
        <div class="modal-body">${inputs}<div class="alert alert-danger d-none" role="alert"></div></div>
        <div class="modal-footer"><button type="button" class="btn btn-light" data-bs-dismiss="modal">Cancel</button><button class="btn btn-dark" type="submit">${App.esc(submitLabel)}</button></div></form></div></div>`;
      document.body.append(wrap);
      const el = wrap.querySelector(".modal"), modal = new bootstrap.Modal(el);
      let done = false;
      wrap.querySelector("form").addEventListener("submit", (e) => {
        e.preventDefault();
        const out = {};
        for (const f of fields) {
          if (f.type === "checks") { out[f.name] = [...wrap.querySelectorAll(`[data-grp="${f.name}"]:checked`)].map((i) => i.value); continue; }
          const c = wrap.querySelector("#fm-" + f.name);
          out[f.name] = f.type === "checkbox" ? c.checked : c.value.trim();
          if (f.required && !out[f.name]) { const a = wrap.querySelector(".alert"); a.textContent = f.label + " is required."; a.classList.remove("d-none"); return; }
        }
        done = true; modal.hide(); resolve(out);
      });
      el.addEventListener("hidden.bs.modal", () => { if (!done) resolve(null); wrap.remove(); });
      modal.show();
    });
  }
  const confirmBox = (message) => Promise.resolve(window.confirm(message));
  const table = (heads, rows) => `<div class="table-wrap"><table class="table table-hover align-middle"><thead><tr>${heads.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  const stat = (n, l) => `<div class="stat"><div class="n">${n}</div><div class="l">${App.esc(l)}</div></div>`;
  return { register, init, formModal, confirmBox, table, stat };
})();
