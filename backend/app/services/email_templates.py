"""HTML email templates. Each returns (subject, html). Inline styles because
email clients ignore external CSS."""
from html import escape

from app.core.config import get_settings
from app.core.money import cedis


def _layout(title: str, body: str) -> str:
    return f"""<div style="background:#f5f7f4;padding:24px;font-family:Arial,sans-serif;color:#0f2e22">
<div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #dde4df">
<div style="background:#0f2e22;color:#fff;padding:16px 24px;font-size:20px;font-weight:bold;border-bottom:4px solid #f2b431">KORA EATS</div>
<div style="padding:24px"><h2 style="margin:0 0 12px">{title}</h2>{body}</div>
<div style="padding:16px 24px;font-size:12px;color:#4b5f55">If you didn't request this, you can safely ignore this email.</div>
</div></div>"""


def otp_email(name: str, code: str) -> tuple[str, str]:
    minutes = get_settings().otp_expire_minutes
    body = (
        f"<p>Hi {escape(name)}, use this code to verify your email:</p>"
        f'<p style="font-size:32px;letter-spacing:8px;font-weight:bold;margin:16px 0">{code}</p>'
        f"<p>It expires in {minutes} minutes. Never share this code with anyone.</p>"
    )
    return "Your KORA EATS verification code", _layout("Verify your email", body)


def welcome_email(name: str) -> tuple[str, str]:
    body = f"<p>Welcome, {escape(name)}! Your email is verified and your account is ready.</p>"
    return "Welcome to KORA EATS", _layout("You're in", body)


def password_reset_email(name: str, link: str) -> tuple[str, str]:
    minutes = get_settings().reset_token_minutes
    body = (
        f"<p>Hi {escape(name)}, we received a request to reset your password.</p>"
        f'<p><a href="{escape(link)}" style="background:#f2b431;color:#0f2e22;padding:12px 20px;'
        f'border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block">Reset password</a></p>'
        f"<p>This link expires in {minutes} minutes and can be used once.</p>"
    )
    return "Reset your KORA EATS password", _layout("Reset your password", body)


def vendor_approved_email(name: str, business: str) -> tuple[str, str]:
    link = f"{get_settings().frontend_base_url}/vendor/dashboard.html"
    body = (f"<p>Great news, {escape(name)}! <strong>{escape(business)}</strong> has been approved.</p>"
            f'<p><a href="{escape(link)}" style="background:#f2b431;color:#0f2e22;padding:12px 20px;border-radius:8px;'
            f'text-decoration:none;font-weight:bold;display:inline-block">Open your dashboard</a></p>')
    return "Your KORA EATS vendor account is approved", _layout("You're approved", body)


def vendor_rejected_email(name: str, business: str, reason: str) -> tuple[str, str]:
    body = (f"<p>Hi {escape(name)}, we couldn't approve <strong>{escape(business)}</strong> yet.</p>"
            f"<p><strong>Reason:</strong> {escape(reason)}</p><p>You can fix this and re-apply from your profile page.</p>")
    return "Update on your KORA EATS vendor application", _layout("Application update", body)


def rider_approved_email(name: str) -> tuple[str, str]:
    link = f"{get_settings().frontend_base_url}/rider/dashboard.html"
    body = (f"<p>Welcome aboard, {escape(name)}! Your rider account is approved.</p>"
            f'<p><a href="{escape(link)}" style="background:#f2b431;color:#0f2e22;padding:12px 20px;border-radius:8px;'
            f'text-decoration:none;font-weight:bold;display:inline-block">Open rider dashboard</a></p>')
    return "You're approved as a KORA EATS rider", _layout("You're approved", body)


def rider_rejected_email(name: str, reason: str) -> tuple[str, str]:
    body = (f"<p>Hi {escape(name)}, we couldn't approve your rider application yet.</p>"
            f"<p><strong>Reason:</strong> {escape(reason)}</p><p>You can fix this and re-apply from your profile page.</p>")
    return "Update on your KORA EATS rider application", _layout("Application update", body)


def notification_email(title: str, text: str, link_path: str | None = None, cta: str = "View order") -> tuple[str, str]:
    body = f"<p>{escape(text)}</p>"
    if link_path:
        link = get_settings().frontend_base_url + link_path
        body += (f'<p><a href="{escape(link)}" style="background:#f2b431;color:#0f2e22;padding:12px 20px;border-radius:8px;'
                 f'text-decoration:none;font-weight:bold;display:inline-block">{escape(cta)}</a></p>')
    return title, _layout(escape(title), body)


def order_summary_email(name: str, order_code: str, items: list[tuple[str, int, float]], total: float, title: str,
                        intro: str, link_path: str) -> tuple[str, str]:
    rows = "".join(f"<tr><td>{q} x {escape(n)}</td><td style='text-align:right'>{cedis(t)}</td></tr>" for n, q, t in items)
    body = (f"<p>Hi {escape(name)}, {escape(intro)}</p><p><strong>Order {escape(order_code)}</strong></p>"
            f"<table style='width:100%;border-collapse:collapse'>{rows}<tr><td style='border-top:1px solid #dde4df;padding-top:6px'><strong>Total</strong></td>"
            f"<td style='border-top:1px solid #dde4df;text-align:right;padding-top:6px'><strong>{cedis(total)}</strong></td></tr></table>"
            f'<p><a href="{escape(get_settings().frontend_base_url + link_path)}" style="background:#f2b431;color:#0f2e22;padding:12px 20px;'
            f'border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block">Track your order</a></p>')
    return title, _layout(escape(title), body)
