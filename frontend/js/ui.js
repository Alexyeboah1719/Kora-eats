/* Shared UI helpers (toasts). Reused by every page. */
const UI = (() => {
  function toast(message, type = "success") {
    const el = document.getElementById("app-toast");
    document.getElementById("toast-body").textContent = message;
    el.className = `toast align-items-center border-0 text-bg-${type === "error" ? "danger" : "success"}`;
    bootstrap.Toast.getOrCreateInstance(el, { delay: 3500 }).show();
  }
  return { toast };
})();
