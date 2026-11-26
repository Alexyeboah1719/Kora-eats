/* Share dialog: link, WhatsApp, device share sheet and a printable QR code. QR images come from OUR server (no third party). */
const Share = (() => {
  function open({ title, text, path, qr }) {
    const url = location.origin + path;
    let dlg = document.getElementById("share-dlg");
    if (dlg) dlg.remove();
    dlg = document.createElement("dialog");
    dlg.id = "share-dlg"; dlg.className = "share-dlg"; dlg.setAttribute("aria-label", "Share");
    const msg = `${text || title} ${url}`;
    dlg.innerHTML = `<form method="dialog"><div class="d-flex justify-content-between align-items-start"><h2 class="h6 mb-2">${App.esc(title)}</h2><button class="btn-close" value="x" aria-label="Close"></button></div>
      ${qr ? `<div class="text-center mb-2"><img id="share-qr" src="${App.esc(qr)}" width="180" height="180" alt="QR code for ${App.esc(title)}"><div class="meta">Scan with a phone camera</div></div>` : ""}
      <div class="input-group input-group-sm mb-2"><input class="form-control" readonly id="share-link" value="${App.esc(url)}" aria-label="Link"><button class="btn btn-outline-dark" type="button" id="share-copy">Copy</button></div>
      <div class="d-flex gap-2 flex-wrap"><a class="btn btn-sm btn-success" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(msg)}"><i class="bi bi-whatsapp"></i> WhatsApp</a>
        ${navigator.share ? '<button class="btn btn-sm btn-outline-dark" type="button" id="share-native"><i class="bi bi-share"></i> More</button>' : ""}
        ${qr ? '<button class="btn btn-sm btn-outline-dark" type="button" id="share-print"><i class="bi bi-printer"></i> Print QR</button>' : ""}</div></form>`;
    document.body.append(dlg);
    dlg.addEventListener("click", async (e) => {
      const t = e.target.closest("button"); if (!t) return;
      if (t.id === "share-copy") { try { await navigator.clipboard.writeText(url); UI.toast("Link copied."); } catch { dlg.querySelector("#share-link").select(); UI.toast("Press Ctrl+C to copy.", "info"); } }
      if (t.id === "share-native") { try { await navigator.share({ title, text: text || title, url }); } catch { /* cancelled */ } }
      if (t.id === "share-print") { const w = window.open("", "_blank"); if (w) { w.document.write(`<title>${App.esc(title)}</title><body style="font-family:sans-serif;text-align:center;margin:3rem"><h1>${App.esc(title)}</h1><img src="${App.esc(location.origin + qr)}" width="320"><p>Scan to order on KORA EATS</p><script>onload=()=>print()<\/script>`); w.document.close(); } }
    });
    dlg.addEventListener("close", () => dlg.remove());
    dlg.showModal();
  }
  return { open };
})();
