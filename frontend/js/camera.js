/* Camera helpers. Compress shrinks big phone photos in the browser BEFORE upload (saves data and money on mobile networks);
   capture opens the live camera and takes a photo. If the camera is unavailable or denied, callers fall back to the normal file picker. */
const Camera = (() => {
  async function compress(file, { maxSide = 1600, quality = 0.82 } = {}) {
    if (!file || !/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 200 * 1024) return file;      // small files and non-images go as they are
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas"); c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const type = file.type === "image/png" ? "image/png" : "image/jpeg";
      const blob = await new Promise((res) => c.toBlob(res, type, quality));
      return blob && blob.size < file.size ? new File([blob], file.name.replace(/\.\w+$/, type === "image/png" ? ".png" : ".jpg"), { type }) : file;
    } catch { return file; }
  }
  const supported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  /** Live camera dialog -> Promise<File|null>. */
  function capture() {
    if (!supported()) return Promise.resolve(null);
    return new Promise(async (resolve) => {
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }); }
      catch { UI.toast("Camera isn't available. Choose a photo from your gallery instead.", "info"); return resolve(null); }
      const dlg = document.createElement("dialog"); dlg.className = "share-dlg"; dlg.setAttribute("aria-label", "Take a photo");
      dlg.innerHTML = `<div class="text-center"><video playsinline autoplay muted style="width:100%;max-height:60vh;border-radius:12px;background:#000"></video>
        <div class="d-flex gap-2 justify-content-center mt-2"><button class="btn btn-dark" id="cam-snap"><i class="bi bi-camera"></i> Take photo</button><button class="btn btn-outline-secondary" id="cam-cancel">Cancel</button></div></div>`;
      document.body.append(dlg);
      const video = dlg.querySelector("video"); video.srcObject = stream;
      const done = (file) => { stream.getTracks().forEach((t) => t.stop()); dlg.close(); dlg.remove(); resolve(file); };
      dlg.querySelector("#cam-cancel").onclick = () => done(null);
      dlg.addEventListener("cancel", () => done(null));
      dlg.querySelector("#cam-snap").onclick = () => {
        const c = document.createElement("canvas"); c.width = video.videoWidth; c.height = video.videoHeight; c.getContext("2d").drawImage(video, 0, 0);
        c.toBlob(async (b) => done(b ? await compress(new File([b], "photo.jpg", { type: "image/jpeg" }), { quality: 0.85 }) : null), "image/jpeg", 0.9);
      };
      dlg.showModal();
    });
  }
  return { compress, capture, supported };
})();
