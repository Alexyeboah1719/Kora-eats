/* Route guard: only logged-in users see this page. */
(async () => {
  const user = await AuthApi.me();
  if (!user) return location.replace("/auth/login.html");
  document.getElementById("user-name").textContent = user.full_name;
  document.getElementById("user-email").textContent = user.email;
  document.getElementById("user-role").textContent = user.role;
  const dest = { VENDOR: ["/vendor/dashboard.html", "Open vendor dashboard"], RIDER: ["/rider/dashboard.html", "Open rider dashboard"], ADMIN: ["/admin/dashboard.html", "Open admin dashboard"] }[user.role] || ["/", "Start ordering"];
  const go = document.getElementById("continue-btn"); go.href = dest[0]; go.textContent = dest[1];
  document.getElementById("loading").classList.add("d-none");
  document.getElementById("content").classList.remove("d-none");
})();

document.getElementById("logout-btn").addEventListener("click", async () => {
  await AuthApi.logout();
  location.assign("/auth/login.html");
});
