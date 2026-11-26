const email = AuthUI.param("email");
if (!email) location.replace("/auth/register.html");
document.getElementById("email-label").textContent = email;

const inputs = [...document.querySelectorAll(".otp-input")];
const btn = document.getElementById("submit-btn");
const resendBtn = document.getElementById("resend-btn");

inputs[0].focus();
inputs.forEach((input, i) => {
  input.addEventListener("input", () => {
    input.value = input.value.replace(/\D/g, "").slice(-1);
    if (input.value && inputs[i + 1]) inputs[i + 1].focus();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && !input.value && inputs[i - 1]) inputs[i - 1].focus();
  });
  input.addEventListener("paste", (e) => {   // paste the whole code into any box
    const digits = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
    if (!digits) return;
    e.preventDefault();
    digits.split("").forEach((d, n) => { inputs[n].value = d; });
    inputs[Math.min(digits.length, 5)].focus();
  });
});

document.getElementById("otp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  AuthUI.hideAlert();
  const code = inputs.map((i) => i.value).join("");
  if (code.length !== 6) return AuthUI.showAlert("Enter the 6-digit code.");
  AuthUI.setLoading(btn, true);
  const r = await AuthApi.verifyOtp({ email, code });
  if (r.ok) return AuthUI.goToSuccess();
  AuthUI.showAlert(AuthApi.errorMessage(r));
  AuthUI.setLoading(btn, false, "Verify email");
  inputs.forEach((i) => (i.value = ""));
  inputs[0].focus();
});

let timer = null;
function startCountdown(seconds = 60) {
  let left = seconds;
  resendBtn.disabled = true;
  clearInterval(timer);
  timer = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(timer);
      resendBtn.disabled = false;
      resendBtn.textContent = "Resend code";
    } else {
      resendBtn.textContent = `Resend code in ${left}s`;
    }
  }, 1000);
  resendBtn.textContent = `Resend code in ${left}s`;
}
startCountdown();

resendBtn.addEventListener("click", async () => {
  AuthUI.hideAlert();
  const r = await AuthApi.resendOtp({ email });
  if (r.ok) { UI.toast("A new code is on its way."); startCountdown(); }
  else AuthUI.showAlert(AuthApi.errorMessage(r));
});
