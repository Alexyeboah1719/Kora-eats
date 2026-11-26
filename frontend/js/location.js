/* Delivery-location picker modal: saved addresses or the browser's geolocation. */
const LocationPicker = (() => {
  async function open(user, onChange) {
    document.getElementById("loc-modal")?.remove();
    const addrs = user ? (await App.call("/me/addresses")) || [] : [];
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div class="modal fade" id="loc-modal" tabindex="-1" aria-labelledby="loc-title" aria-hidden="true"><div class="modal-dialog modal-dialog-centered modal-dialog-scrollable"><div class="modal-content">
      <div class="modal-header"><h2 class="modal-title h5" id="loc-title">Delivery location</h2><button class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>
      <div class="modal-body d-grid gap-2">
        <button class="btn btn-dark" id="loc-gps" type="button"><i class="bi bi-crosshair"></i> Use my current location</button>
        ${addrs.map((a) => `<button class="panel text-start" data-addr="${a.id}" type="button"><strong>${App.esc(a.label)}</strong><br><span class="meta">${App.esc(a.address_line)}</span></button>`).join("")}
        ${user ? '<a class="link-dark small" href="/addresses.html">Manage saved addresses</a>' : '<span class="meta">Log in to save addresses.</span>'}
      </div></div></div></div>`;
    document.body.append(wrap);
    const modal = new bootstrap.Modal(wrap.querySelector(".modal"));
    const done = (loc) => { App.setLocation(loc); modal.hide(); onChange && onChange(); };
    wrap.querySelector("#loc-gps").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { done(await App.detectLocation()); } catch (err) { UI.toast(err.message, "error"); e.target.disabled = false; }
    });
    wrap.querySelectorAll("[data-addr]").forEach((b) => b.addEventListener("click", () => {
      const a = addrs.find((x) => x.id === Number(b.dataset.addr));
      done({ lat: a.latitude, lng: a.longitude, label: a.label + " - " + a.address_line });
    }));
    modal.show();
  }
  return { open };
})();
