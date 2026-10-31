/* QR scanning for pickup hand-over. Uses the browser's built-in BarcodeDetector where available (Chrome/Edge on Android and desktop);
   everywhere else the vendor types the code, which always works. A scanned QR contains: KORAEATS|PICKUP|<order code>|<pickup code> */
const Scanner = (() => {
  const supported = () => !!(window.BarcodeDetector && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  function parse(text) {
    const p = String(text || "").split("|");
    return p.length === 4 && p[0] === "KORAEATS" && p[1] === "PICKUP" ? { order: p[2], code: p[3] } : null;
  }
  /** Dialog: scan the customer's QR or type the code. Resolves to the pickup code (string) or null. orderCode = the order being handed over. */
  function promptCode({ orderCode }) {
    return new Promise(async (resolve) => {
      const dlg = document.createElement("dialog"); dlg.className = "share-dlg"; dlg.setAttribute("aria-label", "Hand over pickup order");
      dlg.innerHTML = `<form method="dialog"><h2 class="h6">Hand over ${App.esc(orderCode)}</h2><p class="meta">Scan the QR on the customer's order page, or type the code they show you.</p>
        <div id="scan-box" class="mb-2 d-none"><video playsinline muted style="width:100%;max-height:40vh;border-radius:12px;background:#000"></video><div class="meta" id="scan-msg">Point the camera at the QR code...</div></div>
        <div class="input-group mb-2"><input class="form-control text-uppercase" id="scan-code" maxlength="8" autocomplete="off" aria-label="Pickup code" placeholder="Pickup code"><button class="btn btn-dark" value="ok" id="scan-ok">Confirm</button></div>
        <div class="d-flex gap-2">${supported() ? '<button class="btn btn-sm btn-outline-dark" type="button" id="scan-start"><i class="bi bi-qr-code-scan"></i> Scan QR</button>' : '<span class="meta">QR scanning isn\'t supported on this browser: type the code.</span>'}<button class="btn btn-sm btn-outline-secondary ms-auto" value="x" id="scan-x">Cancel</button></div></form>`;
      document.body.append(dlg);
      let stream, stop = false;
      const finish = (v) => { stop = true; stream && stream.getTracks().forEach((t) => t.stop()); dlg.close(); dlg.remove(); resolve(v); };
      dlg.querySelector("#scan-x").onclick = () => finish(null);
      dlg.addEventListener("cancel", () => finish(null));
      dlg.querySelector("#scan-ok").onclick = (e) => { e.preventDefault(); const v = dlg.querySelector("#scan-code").value.trim(); if (v) finish(v); };
      const start = dlg.querySelector("#scan-start");
      if (start) start.onclick = async () => {
        try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }); }
        catch { dlg.querySelector("#scan-msg").textContent = "Camera not available: type the code instead."; dlg.querySelector("#scan-box").classList.remove("d-none"); return; }
        const box = dlg.querySelector("#scan-box"), video = box.querySelector("video"); box.classList.remove("d-none"); video.srcObject = stream; await video.play();
        const det = new BarcodeDetector({ formats: ["qr_code"] });
        const tick = async () => {
          if (stop) return;
          try {
            const found = await det.detect(video);
            const got = found.length ? parse(found[0].rawValue) : null;
            if (got) {
              if (got.order !== orderCode) { dlg.querySelector("#scan-msg").textContent = `That QR is for a different order (${got.order}).`; }
              else return finish(got.code);
            }
          } catch { /* keep trying */ }
          requestAnimationFrame(tick);
        };
        tick();
      };
      dlg.showModal();
    });
  }
  return { supported, parse, promptCode };
})();
