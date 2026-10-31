/* Floating food assistant. Answers come from the server, which only ever returns real menu data. */
const AIWidget = (() => {
  let panel = null, convId = null, busy = false;
  const SUGGEST = ["I have GH₵40 and want something filling", "Spicy food near me", "Chicken and rice under GH₵50", "Something quick and cheap", "What can I afford for GH₵30?", "Best rated food near me", "Predict what I'd like", "Show me budget combos"];
  const LOADING_MESSAGES = [
    "Thinking...",
    "Understanding your question...",
    "Checking available options...",
    "Analyzing your request...",
    "Preparing your answer..."
  ];

  function build() {
    const fab = document.createElement("button");
    fab.className = "ai-fab"; fab.type = "button"; fab.setAttribute("aria-label", "Open food assistant");
    fab.innerHTML = '<i class="bi bi-stars"></i><span class="d-none d-sm-inline"> Ask AI</span>';
    fab.addEventListener("click", open);
    document.body.append(fab);

    panel = document.createElement("section");
    panel.className = "ai-panel d-none"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Food assistant");
    panel.setAttribute("aria-modal", "true");
    panel.innerHTML = `<header>
      <strong><i class="bi bi-stars"></i> Food assistant</strong>
      <button type="button" class="ai-close-btn" aria-label="Close food assistant" id="ai-close">
        <i class="bi bi-x-lg"></i>
      </button>
    </header>
      <div class="ai-msgs" id="ai-msgs" aria-live="polite"></div>
      <div class="ai-sugg" id="ai-sugg">${SUGGEST.map((s) => `<button type="button" class="pill-btn" data-s="${App.esc(s)}">${App.esc(s)}</button>`).join("")}</div>
      <form id="ai-form" class="ai-form"><input id="ai-input" maxlength="300" autocomplete="off" placeholder="What are you craving?" aria-label="Message the food assistant"><button class="btn-add" aria-label="Send"><i class="bi bi-send"></i></button></form>
      <div class="ai-note">Answers use live vendor menus. Prices and availability are real.</div>`;
    document.body.append(panel);
    App.wireCards(panel);
    
    // Close button click
    panel.querySelector("#ai-close").addEventListener("click", close);
    
    // Close on Escape key
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !panel.classList.contains("d-none")) {
        close();
      }
    });
    
    // Close on backdrop click
    panel.addEventListener("click", (e) => {
      if (e.target === panel) {
        close();
      }
    });
    
    // Swipe to close for mobile
    let touchStartY = 0;
    panel.addEventListener("touchstart", (e) => {
      touchStartY = e.touches[0].clientY;
    }, { passive: true });
    
    panel.addEventListener("touchmove", (e) => {
      const touchY = e.touches[0].clientY;
      const diff = touchY - touchStartY;
      if (diff > 100) {
        close();
      }
    }, { passive: true });
    
    panel.querySelector("#ai-sugg").addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (b) send(b.dataset.s); });
    panel.querySelector("#ai-form").addEventListener("submit", (e) => { e.preventDefault(); const i = panel.querySelector("#ai-input"); const t = i.value.trim(); if (t) { i.value = ""; send(t); } });
    add("bot", "Hi! Tell me what you feel like eating. I can use your budget, spice level, distance and more.");
  }
  function add(who, html) {
    const box = panel.querySelector("#ai-msgs");
    const d = document.createElement("div"); d.className = "ai-msg " + who; d.innerHTML = html; box.append(d); box.scrollTop = box.scrollHeight; return d;
  }
  async function send(text) {
    if (busy) return; busy = true;
    panel.querySelector("#ai-sugg")?.remove();
    add("me", App.esc(text));

    // Show animated loading with rotating messages
    let loadingMsg = 0;
    const typing = add("bot", `<span class="spinner-border spinner-border-sm"></span> ${LOADING_MESSAGES[loadingMsg]}`);
    const loadingInterval = setInterval(() => {
      loadingMsg = (loadingMsg + 1) % LOADING_MESSAGES.length;
      typing.innerHTML = `<span class="spinner-border spinner-border-sm"></span> ${LOADING_MESSAGES[loadingMsg]}`;
    }, 2000);

    const body = { message: text, conversation_id: convId, ...App.locParams() };
    const r = await App.api("/ai/chat", { method: "POST", body });
    clearInterval(loadingInterval);
    typing.remove();
    busy = false;
    if (!r.ok) return add("bot", App.esc(App.errorMessage(r)));
    convId = r.data.conversation_id || convId;
    
    // Handle role-specific authentication prompts
    if (r.data.auth_required) {
      const requiredRole = r.data.required_role || "account";
      const roleText = {
        "VENDOR": "vendor",
        "ADMIN": "admin", 
        "RIDER": "rider",
        "CUSTOMER": "customer"
      }[requiredRole] || "account";
      
      const authMsg = `${r.data.reply}<div class="mt-3"><button class="btn btn-primary btn-sm" onclick="location.href='/login.html?redirect=' + encodeURIComponent(location.pathname)">Log in to ${roleText} account</button></div>`;
      const el = add("bot", authMsg);
      el.scrollIntoView({ block: "nearest" });
      return;
    }
    
    const chips = r.data.filters.length ? `<div class="mb-2">${r.data.filters.map((f) => `<span class="status-pill me-1">${App.esc(f)}</span>`).join("")}</div>` : "";
    const el = add("bot", chips + `<div>${App.esc(r.data.reply)}</div>` + (r.data.items.length ? `<div class="ai-items">${r.data.items.map((i) => App.itemCard(i)).join("")}</div>` : ""));
    el.scrollIntoView({ block: "nearest" });
  }
  function open() { 
    if (!panel) build(); 
    panel.classList.remove("d-none"); 
    panel.querySelector("#ai-input").focus();
    // Prevent body scroll when panel is open
    document.body.style.overflow = "hidden";
  }
  function close() { 
    panel.classList.add("d-none"); 
    // Restore body scroll
    document.body.style.overflow = "";
  }
  document.addEventListener("DOMContentLoaded", () => { if (document.getElementById("page") && !panel) { build(); } });
  if (document.readyState !== "loading" && document.getElementById("page") && !panel) build();
  return { open, close };
})();
