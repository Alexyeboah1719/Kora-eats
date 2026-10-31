/* Shared auth helpers used by every auth page. Talks to /api/auth/*.
   Tokens live in HttpOnly cookies, so this code never sees or stores them. */
const AuthApi = (() => {
  async function raw(path, { method = "GET", body } = {}) {
    const res = await fetch(`/api/auth${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    return { ok: res.ok, status: res.status, data };
  }

  /** Message to show the user for a failed response. */
  function errorMessage(r) {
    const d = r.data && r.data.detail;
    if (d && d.message) return d.message;
    if (Array.isArray(d) && d[0]) return String(d[0].msg).replace(/^Value error, /, "");
    return "Something went wrong. Please try again.";
  }
  const errorCode = (r) => (r.data && r.data.detail && r.data.detail.code) || null;

  const post = (path, body) => raw(path, { method: "POST", body });
  return {
    errorMessage, errorCode,
    config: () => raw("/config"),
    register: (b) => post("/register", b),
    verifyOtp: (b) => post("/verify-otp", b),
    resendOtp: (b) => post("/resend-otp", b),
    login: (b) => post("/login", b),
    google: (credential) => post("/google", { credential }),
    logout: () => post("/logout"),
    forgotPassword: (b) => post("/forgot-password", b),
    resetPassword: (b) => post("/reset-password", b),
    /** Current user, or null. Silently refreshes the session once if the access token expired. */
    async me() {
      let r = await raw("/me");
      if (r.status === 401 && (await post("/refresh")).ok) r = await raw("/me");
      return r.ok ? r.data : null;
    },
  };
})();

const AuthUI = (() => {
  const param = (name) => new URLSearchParams(location.search).get(name) || "";

  function setLoading(btn, loading, label) {
    btn.disabled = loading;
    btn.innerHTML = loading
      ? '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Please wait...'
      : label;
  }

  function showAlert(message, type = "danger") {
    const el = document.getElementById("form-alert");
    el.className = `alert alert-${type}`;
    el.textContent = message;
    el.classList.remove("d-none");
  }
  const hideAlert = () => document.getElementById("form-alert").classList.add("d-none");

  function initPasswordToggles() {
    document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = document.querySelector(btn.dataset.togglePassword);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.querySelector("i").className = `bi ${show ? "bi-eye-slash" : "bi-eye"}`;
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  }

  /** Remember where the user was heading (?next=/cart.html) across login -> register -> OTP pages.
   *  Only same-site paths are accepted, so nobody can use this to redirect users to another website. */
  const safePath = (p) => (typeof p === "string" && /^\/(?![\/\\])/.test(p) ? p : null);
  (() => { const n = safePath(new URLSearchParams(location.search).get("next")); if (n) sessionStorage.setItem("foodapp.next", n); })();
  const goToSuccess = () => {
    const n = safePath(sessionStorage.getItem("foodapp.next"));
    sessionStorage.removeItem("foodapp.next");
    location.assign(n || "/auth/success.html");
  };

  async function redirectIfLoggedIn() {
    if (await AuthApi.me()) goToSuccess();
  }

  /** Renders the Google button, or an honest note when no client ID is configured. */
  async function initGoogle() {
    const box = document.getElementById("google-btn");
    const note = document.getElementById("google-note");
    if (!box) return;
    let clientId = null;
    try { clientId = (await AuthApi.config()).data.google_client_id; } catch (_) { /* offline */ }
    if (!clientId) { note.classList.remove("d-none"); return; }

    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    }).catch(() => { note.textContent = "Couldn't load Google sign-in."; note.classList.remove("d-none"); });
    if (!window.google) return;

    google.accounts.id.initialize({
      client_id: clientId,
      callback: async (resp) => {
        const r = await AuthApi.google(resp.credential);
        r.ok ? goToSuccess() : showAlert(AuthApi.errorMessage(r));
      },
    });
    google.accounts.id.renderButton(box, { theme: "outline", size: "large", width: Math.min(box.clientWidth || 340, 400), text: "continue_with" });
  }

  return { param, setLoading, showAlert, hideAlert, initPasswordToggles, goToSuccess, redirectIfLoggedIn, initGoogle };
})();
