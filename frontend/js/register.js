AuthUI.initPasswordToggles();
AuthUI.redirectIfLoggedIn();
AuthUI.initGoogle();
AuthUI.initTurnstile();
const form = document.getElementById("register-form");
const btn = document.getElementById("submit-btn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  AuthUI.hideAlert();
  if (form.password.value !== form.confirm_password.value) {
    return AuthUI.showAlert("Passwords do not match.");
  }
  const email = form.email.value.trim();
  AuthUI.setLoading(btn, true);
  const r = await AuthApi.register({
    full_name: form.full_name.value.trim(),
    email,
    phone: form.phone.value.trim(),
    password: form.password.value,
    confirm_password: form.confirm_password.value,
    turnstile_token: AuthUI.turnstileToken(),
  });
  AuthUI.resetTurnstile();
  if (r.ok) {
    const cfg = await AuthApi.config();
    // Test mode (OTP off): the account is already verified — send them straight to log in.
    if (cfg.ok && cfg.data && cfg.data.otp_required === false) {
      UI.toast("Account created — log in with your email and password.");
      return location.assign("/auth/login.html");
    }
    return location.assign(`/auth/verify-otp.html?email=${encodeURIComponent(email)}`);
  }
  AuthUI.showAlert(AuthApi.errorMessage(r));
  AuthUI.setLoading(btn, false, "Create account");
});
