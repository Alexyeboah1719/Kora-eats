/* Thin wrapper around fetch. Every page talks to the backend through this. */
const Api = (() => {
  // Use Render backend URL in production, localhost in development
  const API_BASE = process.env.RENDER_BACKEND_URL || '';
  
  async function request(path, options = {}) {
    const url = API_BASE ? `${API_BASE}/api${path}` : `/api${path}`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      credentials: API_BASE ? 'include' : 'same-origin',
      ...options,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON body */ }
    return { ok: res.ok, status: res.status, data };
  }
  return { get: (path) => request(path) };
})();
