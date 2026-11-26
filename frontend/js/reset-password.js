AuthUI.initPasswordToggles();
const token = AuthUI.param("token");
const form = document.getElementById("reset-form");
const btn = document.getElementById("submit-btn");
if (!token) {
  form.classList.add("d-none");
  AuthUI.showAlert("This reset link is missing its token. Request a new one.");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  AuthUI.hideAlert();
  if (form.password.value !== form.confirm_password.value) return AuthUI.showAlert("Passwords do not match.");
  AuthUI.setLoading(btn, true);
  const r = await AuthApi.resetPassword({
    token, password: form.password.value, confirm_password: form.confirm_password.value,
  });
  if (r.ok) {
    UI.toast("Password updated. Redirecting to login...");
    return setTimeout(() => location.assign("/auth/login.html"), 1500);
  }
  AuthUI.showAlert(AuthApi.errorMessage(r));
  AuthUI.setLoading(btn, false, "Update password");
});
