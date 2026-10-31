/* Thin wrapper around fetch. Every page talks to the backend through this. */
const Api = (() => {
  async function request(path, options = {}) {
    const res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON body */ }
    return { ok: res.ok, status: res.status, data };
  }
  return { get: (path) => request(path) };
})();
