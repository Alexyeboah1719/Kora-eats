/* Role-specific AI assistant widget for Admin, Vendor, and Rider dashboards.
This version uses role-specific endpoints with authentication verification. */
const RoleAIWidget = (() => {
  let panel = null, convId = null, busy = false, role = null;

  function getRole() {
    if (!App.state.user) return null;
    return App.state.user.role;
  }

  function getEndpoint() {
    role = getRole();
    if (role === "ADMIN") return "/ai/admin/chat";
    if (role === "VENDOR") return "/ai/vendor/chat";
    if (role === "RIDER") return "/ai/rider/chat";
    return null;
  }

  function getTitle() {
    if (role === "ADMIN") return "Admin Assistant";
    if (role === "VENDOR") return "Business Assistant";
    if (role === "RIDER") return "Delivery Assistant";
    return "AI Assistant";
  }

  function getSuggestions() {
    if (role === "ADMIN") {
      return ["Show me order statistics", "How are vendors performing?", "What's the dispatch status?", "AI performance summary"];
    }
    if (role === "VENDOR") {
      return ["Today's sales", "Demand forecast", "Popular items", "My earnings"];
    }
    if (role === "RIDER") {
      return ["Today's deliveries", "My earnings", "My performance", "This week's stats"];
    }
    return ["I have GH₵40 and want something filling", "Spicy food near me", "Chicken and rice under GH₵50"];
  }

  function build() {
    const endpoint = getEndpoint();
    if (!endpoint) {
      console.error("No valid role for role-specific AI");
      return;
    }

    const fab = document.createElement("button");
    fab.className = "ai-fab"; fab.type = "button"; fab.setAttribute("aria-label", "Open AI assistant");
    fab.innerHTML = '<i class="bi bi-stars"></i><span class="d-none d-sm-inline"> Ask AI</span>';
    fab.addEventListener("click", open);
    document.body.append(fab);

    panel = document.createElement("section");
    panel.className = "ai-panel d-none"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", getTitle());
    panel.innerHTML = `<header><strong><i class="bi bi-stars"></i> ${getTitle()}</strong><button type="button" class="btn-close btn-close-white" aria-label="Close" id="ai-close"></button></header>
      <div class="ai-msgs" id="ai-msgs" aria-live="polite"></div>
      <div class="ai-sugg" id="ai-sugg">${getSuggestions().map((s) => `<button type="button" class="pill-btn" data-s="${App.esc(s)}">${App.esc(s)}</button>`).join("")}</div>
      <form id="ai-form" class="ai-form"><input id="ai-input" maxlength="300" autocomplete="off" placeholder="Ask about your business..." aria-label="Message the AI assistant"><button class="btn-add" aria-label="Send"><i class="bi bi-send"></i></button></form>
      <div class="ai-note">All answers are based on your account data and are role-specific.</div>`;
    document.body.append(panel);
    App.wireCards(panel);
    panel.querySelector("#ai-close").addEventListener("click", close);
    panel.querySelector("#ai-sugg").addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (b) send(b.dataset.s); });
    panel.querySelector("#ai-form").addEventListener("submit", (e) => { e.preventDefault(); const i = panel.querySelector("#ai-input"); const t = i.value.trim(); if (t) { i.value = ""; send(t); } });
    add("bot", `Hi! I'm your ${getTitle().toLowerCase()}. I can help you with insights specific to your role. What would you like to know?`);
  }

  function add(who, html) {
    const box = panel.querySelector("#ai-msgs");
    const d = document.createElement("div"); d.className = "ai-msg " + who; d.innerHTML = html; box.append(d); box.scrollTop = box.scrollHeight; return d;
  }

  async function send(text) {
    if (busy) return; busy = true;
    panel.querySelector("#ai-sugg")?.remove();
    add("me", App.esc(text));
    const typing = add("bot", '<span class="spinner-border spinner-border-sm"></span> Looking up your data...');
    const endpoint = getEndpoint();
    const r = await App.api(endpoint, { method: "POST", body: { message: text } });
    typing.remove();
    busy = false;
    if (!r.ok) return add("bot", App.esc(App.errorMessage(r)));
    const el = add("bot", r.data.reply.replace(/\n/g, "<br>"));
    el.scrollIntoView({ block: "nearest" });
  }

  function open() {
    if (!panel) build();
    if (!panel) {
      UI.toast("AI assistant not available for your role", "error");
      return;
    }
    panel.classList.remove("d-none");
    panel.querySelector("#ai-input").focus();
  }

  function close() {
    panel.classList.add("d-none");
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("page") && !panel && getRole()) {
      build();
    }
  });
  if (document.readyState !== "loading" && document.getElementById("page") && getRole()) build();

  return { open, close };
})();
