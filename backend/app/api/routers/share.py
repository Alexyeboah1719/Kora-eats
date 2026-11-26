"""QR codes and shareable pages.

QR codes are generated on the SERVER (SVG, no tracking, no third-party service). Shareable pages (/v/12-name, /f/7-name) are plain HTML with
Open Graph tags so WhatsApp/Facebook/Twitter previews show the right title, price and photo; people are then sent on to the real app page.
Everything dynamic is HTML-escaped. Only approved vendors and available dishes are ever shown."""
import html
import io

import segno
from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import share
from app.core.config import get_settings
from app.core.errors import api_error
from app.core.money import cedis
from app.db.session import get_db
from app.models.catalog import BUSINESS_LABELS, MenuItem, Vendor
from app.models.groups import GroupOrder
from app.models.orders import FulfillmentType, Order, OrderStatus as S
from app.models.user import User
from app.services import catalog_service as cat

qr_router = APIRouter(tags=["share"])
page_router = APIRouter(include_in_schema=False)
SVG_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"}


def base() -> str:
    return get_settings().frontend_base_url.rstrip("/")


def qr_response(data: str, cache: str) -> Response:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0f2e22", xmldecl=False, svgns=True, nl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={**SVG_HEADERS, "Cache-Control": cache})


def _vendor(db: Session, vendor_id: int) -> Vendor:
    v = db.scalar(cat.public_vendors_query().where(Vendor.id == vendor_id))
    if v is None:
        raise api_error(404, "not_found", "Vendor not found.")
    return v


# ------------------------------------------------------------------ QR codes
@qr_router.get("/vendors/{vendor_id}/qr.svg")
def vendor_qr(vendor_id: int, db: Session = Depends(get_db)):
    """A QR code vendors can print for their counter: scanning opens their menu."""
    v = _vendor(db, vendor_id)
    return qr_response(base() + share.vendor_path(v.id, v.name), "public, max-age=3600")


@qr_router.get("/menu-items/{item_id}/qr.svg")
def food_qr(item_id: int, db: Session = Depends(get_db)):
    it = db.scalar(select(MenuItem).where(MenuItem.id == item_id, MenuItem.vendor_id.in_(cat.public_vendors_query().with_only_columns(Vendor.id))))
    if it is None:
        raise api_error(404, "not_found", "Dish not found.")
    return qr_response(base() + share.food_path(it.id, it.name), "public, max-age=3600")


@qr_router.get("/orders/{order_id}/pickup-qr.svg")
def pickup_qr(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your pickup code as a QR code, so the vendor can scan it. Only the customer who placed the order can fetch it."""
    o = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == user.id))
    live = (S.PAID, S.CONFIRMED, S.PREPARING, S.READY_FOR_PICKUP)
    if o is None or o.fulfillment_type != FulfillmentType.PICKUP or not o.pickup_code or o.status not in live:
        raise api_error(404, "not_found", "No active pickup code for this order.")
    return qr_response(f"KORAEATS|PICKUP|{o.code}|{o.pickup_code}", "no-store")


@qr_router.get("/groups/{code}/qr.svg")
def group_qr(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = db.scalar(select(GroupOrder).where(GroupOrder.code == code.upper()))
    if g is None or not any(m.user_id == user.id for m in g.members):
        raise api_error(404, "not_found", "Group order not found.")
    return qr_response(f"{base()}/group/{g.code}", "no-store")


# ------------------------------------------------------------------ shareable pages with link previews
def _abs(path: str | None) -> str:
    return base() + (path or "/icons/icon-512.png")


def _page(title: str, description: str, image: str, canonical: str, target: str, heading: str, status: int = 200) -> HTMLResponse:
    e = html.escape
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(description)}"><link rel="canonical" href="{e(canonical)}">
<meta property="og:site_name" content="KORA EATS"><meta property="og:type" content="website"><meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}"><meta property="og:image" content="{e(image)}"><meta property="og:url" content="{e(canonical)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}"><meta name="twitter:description" content="{e(description)}"><meta name="twitter:image" content="{e(image)}">
<meta name="theme-color" content="#0F2E22"><link rel="icon" href="/icons/favicon-64.png">
{'' if status != 200 else f'<meta http-equiv="refresh" content="0;url={e(target)}">'}
<style>body{{font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f7f4;color:#0f2e22;text-align:center;padding:1.5rem}}
.c{{max-width:420px}}img{{max-width:100%;border-radius:14px}}a.b{{display:inline-block;background:#0f2e22;color:#fff;border-radius:10px;padding:.7rem 1.2rem;text-decoration:none;font-weight:600;margin-top:1rem}}p{{color:#4b5f55}}</style></head>
<body><div class="c"><img src="{e(image)}" alt="" width="320" height="240" style="object-fit:cover"><h1>{e(heading)}</h1><p>{e(description)}</p><a class="b" href="{e(target)}">Open in KORA EATS</a></div></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "public, max-age=300" if status == 200 else "no-store"})


def _not_found() -> HTMLResponse:
    return _page("Not found | KORA EATS", "We couldn't find that page. It may have been removed.", _abs(None), base() + "/", "/", "Not found", status=404)


@page_router.get("/v/{ident}")
def vendor_page(ident: str, db: Session = Depends(get_db)):
    vid = share.parse_ident(ident)
    v = db.scalar(cat.public_vendors_query().where(Vendor.id == vid)) if vid else None
    if v is None:
        return _not_found()
    place = v.area or v.region or "Ghana"
    rating = f" \u2605 {v.rating_avg:.1f} ({v.rating_count} reviews)." if v.rating_count else ""
    desc = f"{BUSINESS_LABELS[v.business_type.value]} in {place}.{rating} {(v.description or '')[:120]}".strip()
    return _page(f"{v.name} on KORA EATS", desc, _abs(v.cover_url or v.logo_url), base() + share.vendor_path(v.id, v.name),
                 f"/vendor.html?id={v.id}", v.name)


@page_router.get("/f/{ident}")
def food_page(ident: str, db: Session = Depends(get_db)):
    iid = share.parse_ident(ident)
    it = db.scalar(select(MenuItem).where(MenuItem.id == iid, MenuItem.vendor_id.in_(cat.public_vendors_query().with_only_columns(Vendor.id)))) if iid else None
    if it is None:
        return _not_found()
    price, _ = cat.price_for(it)
    desc = f"{cedis(price)} from {it.vendor.name}" + (f" in {it.vendor.area}" if it.vendor.area else "") + f". {(it.description or '')[:100]}"
    return _page(f"{it.name} - {cedis(price)} | KORA EATS", desc.strip(), _abs(it.image_url or it.vendor.cover_url or it.vendor.logo_url),
                 base() + share.food_path(it.id, it.name), f"/vendor.html?id={it.vendor_id}", it.name)


@page_router.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    urls = [base() + "/"] + [base() + share.vendor_path(v.id, v.name) for v in db.scalars(cat.public_vendors_query().limit(5000)).unique()]
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{html.escape(u)}</loc></url>" for u in urls) + "</urlset>"
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@page_router.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin/\nDisallow: /vendor/\nDisallow: /rider/\nDisallow: /auth/\n"
                             f"Sitemap: {base()}/sitemap.xml\n", headers={"Cache-Control": "public, max-age=3600"})
