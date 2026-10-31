AuthUI.initPasswordToggles();
AuthUI.redirectIfLoggedIn();
AuthUI.initGoogle();
const form = document.getElementById("login-form");
const btn = document.getElementById("submit-btn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  AuthUI.hideAlert();
  const email = form.email.value.trim();
  AuthUI.setLoading(btn, true);
  const r = await AuthApi.login({ email, password: form.password.value });
  if (r.ok) return AuthUI.goToSuccess();
  if (AuthApi.errorCode(r) === "email_not_verified") {
    return location.assign(`/auth/verify-otp.html?email=${encodeURIComponent(email)}`);
  }
  AuthUI.showAlert(AuthApi.errorMessage(r));
  AuthUI.setLoading(btn, false, "Log in");
});
