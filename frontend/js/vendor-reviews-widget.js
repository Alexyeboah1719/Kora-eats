/* Shows customer reviews at the bottom of a vendor page. */
window.addEventListener("vendor:loaded", async (e) => {
  const v = e.detail, slot = document.getElementById("reviews");
  if (!slot) return;
  const list = await App.call(`/vendors/${v.id}/reviews`);
  if (!list) return;
  const stars = (n) => "&#9733;".repeat(n) + '<span style="color:#d3d9d5">' + "&#9733;".repeat(5 - n) + "</span>";
  slot.innerHTML = `<div class="section-title"><span>Reviews${v.rating_count ? ` (${v.rating_count})` : ""}</span></div>` + (list.length ? `<div class="row g-2">${list.map((r) => `<div class="col-md-6"><div class="panel h-100">
    <div class="d-flex justify-content-between"><strong>${App.esc(r.customer)}</strong><span class="meta">${App.timeAgo(r.created_at)}</span></div>
    <div class="rating">Food ${stars(r.food_rating)} <span class="meta">&middot;</span> Service ${stars(r.vendor_rating)}</div>
    ${r.comment ? `<p class="mb-1 mt-1">${App.esc(r.comment)}</p>` : ""}${r.image_url ? `<img class="rounded mt-1" src="${App.esc(r.image_url)}" alt="Photo from review" style="max-width:100%;max-height:160px">` : ""}</div></div>`).join("")}</div>`
    : App.empty("bi-star", "No reviews yet", "Be the first to order and review."));
});
