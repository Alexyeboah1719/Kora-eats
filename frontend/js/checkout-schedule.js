/* "Deliver now" or "Schedule for later" choice on the checkout page. */
(() => {
  let rendered = false;
  window.addEventListener("checkout:summary", () => {
    const slot = document.getElementById("extra-options");
    if (rendered || !slot) return;
    rendered = true;
    const pad = (n) => String(n).padStart(2, "0");
    const local = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const min = new Date(Date.now() + 60 * 60000), max = new Date(Date.now() + 7 * 86400000);
    slot.innerHTML = `<div class="panel mb-3"><h2 class="h6">When?</h2>
      <div class="form-check"><input class="form-check-input" type="radio" name="when" id="w-now" checked><label class="form-check-label" for="w-now">Deliver now</label></div>
      <div class="form-check"><input class="form-check-input" type="radio" name="when" id="w-later"><label class="form-check-label" for="w-later">Schedule for later</label></div>
      <div class="mt-2 d-none" id="later-box"><label class="form-label" for="when-at">Delivery time</label><input class="form-control" type="datetime-local" id="when-at" min="${local(min)}" max="${local(max)}" value="${local(min)}">
        <div class="hint">Up to 7 days ahead, while the vendor is open. We'll send your order to the vendor shortly before it's due.</div></div></div>`;
    slot.addEventListener("change", () => document.getElementById("later-box").classList.toggle("d-none", !document.getElementById("w-later").checked));
  });
  window.addEventListener("checkout:body", (e) => {
    const later = document.getElementById("w-later"), at = document.getElementById("when-at");
    if (later && later.checked && at.value) e.detail.body.scheduled_for = new Date(at.value).toISOString();   // local time -> UTC
  });
})();
