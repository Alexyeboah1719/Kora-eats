AuthUI.initPasswordToggles();
AuthUI.redirectIfLoggedIn();
AuthUI.initGoogle();
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
    password: form.password.value,
    confirm_password: form.confirm_password.value,
  });
  if (r.ok) return location.assign(`/auth/verify-otp.html?email=${encodeURIComponent(email)}`);
  AuthUI.showAlert(AuthApi.errorMessage(r));
  AuthUI.setLoading(btn, false, "Create account");
});
