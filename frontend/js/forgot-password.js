const form = document.getElementById("forgot-form");
const btn = document.getElementById("submit-btn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  AuthUI.hideAlert();
  AuthUI.setLoading(btn, true);
  const r = await AuthApi.forgotPassword({ email: form.email.value.trim() });
  if (r.ok) {
    form.classList.add("d-none");
    document.getElementById("sent-state").classList.remove("d-none");
  } else {
    AuthUI.showAlert(AuthApi.errorMessage(r));
    AuthUI.setLoading(btn, false, "Send reset link");
  }
});
