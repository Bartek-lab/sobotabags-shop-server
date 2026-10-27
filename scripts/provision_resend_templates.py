"""One-off provisioning script: creates (or re-creates) every transactional
email template this project needs in Resend, via the real Templates API
(create -> publish are separate calls; Resend doesn't auto-publish).

Run once with: uv run python scripts/provision_resend_templates.py
Re-running is safe -- it looks up existing templates by name first and
updates+republishes them in place instead of creating duplicates.

Design notes:
- Table-based layout, inline styles only -- the only reliable approach
  across real-world email clients (Gmail/Outlook strip <style> blocks and
  modern CSS in many contexts).
- Fonts: Georgia/serif as the email-safe stand-in for the storefront's
  Fraunces display font, system sans-serif stack standing in for Inter --
  custom web fonts aren't reliably supported in email.
- Colors approximate the storefront's oklch "leather" palette
  (app/globals.css) in plain hex, since oklch() itself has poor email
  client support.
- Server-constructed HTML fragments (item rows, address blocks) use
  triple-brace {{{VAR}}} (raw/unescaped). Anything that could contain
  customer-supplied text (name, message) is escaped server-side before
  being handed to the template as a pre-built fragment -- see
  app/services/email_service.py.
- `from`/`reply_to` default to onboarding@resend.dev since this Resend
  account has no verified custom domain yet. Known limitation: Resend
  restricts unverified-domain sending to the account owner's own address
  for some recipients -- verify a real domain before relying on this for
  arbitrary customers.
"""

import os

import resend
from dotenv import load_dotenv

load_dotenv()
resend.api_key = os.environ["RESEND_API_KEY"]

FROM_ADDRESS = "KS <onboarding@resend.dev>"
LEATHER = "#8B5E3C"
CREAM = "#F7F3EC"
INK = "#2B2420"
MUTED = "#6B5F54"
BORDER = "#E5DED3"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
# Served directly from the deployed storefront's public/ folder -- already
# a self-contained dark mark on transparent background, no CSS mask trick
# needed the way the site itself uses it (email clients don't support
# mask-image anyway).
LOGO_URL = "https://sobotabags-shop-client-app.vercel.app/assets/KS_logo.png"


def shell(body_html: str) -> str:
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KS</title>
</head>
<body style="margin:0; padding:0; background-color:{CREAM}; font-family:{SANS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{CREAM}; padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px; background-color:#ffffff; border-radius:4px; overflow:hidden;">
<tr><td style="padding:24px 40px; border-bottom:1px solid {BORDER};">
<img src="{LOGO_URL}" alt="KS" width="23" height="40" style="display:block; height:40px; width:auto;">
</td></tr>
<tr><td style="padding:32px 40px;">
{body_html}
</td></tr>
<tr><td style="padding:24px 40px; border-top:1px solid {BORDER}; font-size:12px; color:{MUTED}; line-height:1.6;">
KS — Karolina Sobota, ul. Okrężna 24, 63-200 Jarocin<br>
sobotabags@gmail.com
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def h1(text: str) -> str:
    return f'<h1 style="font-family:{SERIF}; font-size:24px; font-weight:500; color:{INK}; margin:0 0 12px 0;">{text}</h1>'


def p(text: str) -> str:
    return f'<p style="font-size:15px; line-height:1.6; color:{MUTED}; margin:0 0 20px 0;">{text}</p>'


def button(label: str, url_var: str) -> str:
    # Built with plain concatenation, not an f-string -- counting literal
    # `{` brace characters inside an f-string's own `{{` escaping is exactly
    # the kind of thing that's easy to get subtly wrong.
    mustache_var = "{{{" + url_var + "}}}"
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 4px 0;">'
        + '<tr><td style="background-color:' + LEATHER + '; border-radius:4px;">'
        + '<a href="' + mustache_var + '" style="display:inline-block; padding:12px 24px; '
        + 'font-size:14px; font-weight:600; color:#ffffff; text-decoration:none;">' + label + "</a>"
        + "</td></tr></table>"
    )


TEMPLATES = {
    "order-confirmation": {
        "subject": "Potwierdzenie zamówienia — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Dziękujemy za zamówienie!")
            + p(
                "Cześć {{{CUSTOMER_NAME}}}, Twoje zamówienie <strong>#{{{ORDER_NUMBER}}}</strong> "
                "zostało przyjęte do realizacji. Poniżej znajdziesz podsumowanie."
            )
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="font-size:14px; color:{INK};">'
            "{{{ITEMS_HTML}}}"
            "</table>"
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="font-size:14px; color:{MUTED}; margin-top:12px; border-top:1px solid {BORDER}; padding-top:16px;">'
            '<tr><td style="padding:4px 0;">Produkty</td><td align="right" style="padding:4px 0;">{{{SUBTOTAL}}}</td></tr>'
            "{{{DISCOUNT_ROW_HTML}}}"
            '<tr><td style="padding:4px 0;">Dostawa</td><td align="right" style="padding:4px 0;">{{{SHIPPING_AMOUNT}}}</td></tr>'
            "{{{GIFT_CARD_ROW_HTML}}}"
            f'<tr><td style="padding-top:12px; font-weight:600; color:{INK}; border-top:1px solid {BORDER};">Razem</td>'
            f'<td align="right" style="padding-top:12px; font-weight:600; color:{INK}; border-top:1px solid {BORDER};">{{{{{{TOTAL}}}}}}</td></tr>'
            "</table>"
            f'<div style="margin-top:24px; padding-top:20px; border-top:1px solid {BORDER};">'
            f'<p style="font-size:12px; color:{MUTED}; margin:0 0 8px 0; text-transform:uppercase; letter-spacing:0.08em;">Dostawa</p>'
            "{{{DELIVERY_HTML}}}"
            "</div>"
        ),
        "variables": [
            {"key": "CUSTOMER_NAME", "type": "string", "fallback_value": "Kliencie"},
            {"key": "ORDER_NUMBER", "type": "number", "fallback_value": 0},
            {"key": "ITEMS_HTML", "type": "string", "fallback_value": ""},
            {"key": "SUBTOTAL", "type": "string", "fallback_value": "0,00 zł"},
            {"key": "DISCOUNT_ROW_HTML", "type": "string", "fallback_value": ""},
            {"key": "SHIPPING_AMOUNT", "type": "string", "fallback_value": "0,00 zł"},
            {"key": "GIFT_CARD_ROW_HTML", "type": "string", "fallback_value": ""},
            {"key": "TOTAL", "type": "string", "fallback_value": "0,00 zł"},
            {"key": "DELIVERY_HTML", "type": "string", "fallback_value": ""},
        ],
    },
    "shipment-confirmation": {
        "subject": "Twoje zamówienie zostało wysłane — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("W drodze do Ciebie!")
            + p(
                "Cześć {{{CUSTOMER_NAME}}}, zamówienie <strong>#{{{ORDER_NUMBER}}}</strong> "
                "zostało właśnie nadane przewoźnikiem {{{CARRIER_NAME}}}."
            )
            + f'<p style="font-size:13px; color:{MUTED}; margin:0 0 4px 0;">Numer przesyłki</p>'
            + f'<p style="font-size:16px; color:{INK}; font-weight:600; margin:0 0 20px 0;">{{{{{{TRACKING_NUMBER}}}}}}</p>'
            + button("Śledź przesyłkę", "TRACKING_URL")
        ),
        "variables": [
            {"key": "CUSTOMER_NAME", "type": "string", "fallback_value": "Kliencie"},
            {"key": "ORDER_NUMBER", "type": "number", "fallback_value": 0},
            {"key": "CARRIER_NAME", "type": "string", "fallback_value": "kuriera"},
            {"key": "TRACKING_NUMBER", "type": "string", "fallback_value": ""},
            {"key": "TRACKING_URL", "type": "string", "fallback_value": "https://sobotabags.pl"},
        ],
    },
    "gift-card-issued": {
        "subject": "Twoja karta podarunkowa KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Masz kartę podarunkową!")
            + p("{{{RECIPIENT_NAME}}} — ktoś wyjątkowy pomyślał o Tobie. Oto Twój kod:")
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background-color:{CREAM}; border-radius:4px; margin:4px 0 20px 0;">'
            f'<tr><td align="center" style="padding:24px;">'
            f'<span style="font-family:{SERIF}; font-size:28px; letter-spacing:0.08em; color:{INK};">{{{{{{CODE}}}}}}</span>'
            f'<br><span style="font-size:14px; color:{MUTED};">Wartość: {{{{{{AMOUNT}}}}}}</span>'
            "</td></tr></table>"
            + p("Kod możesz wykorzystać podczas finalizacji zamówienia w sklepie sobotabags.pl.")
        ),
        "variables": [
            {"key": "RECIPIENT_NAME", "type": "string", "fallback_value": "Cześć"},
            {"key": "CODE", "type": "string", "fallback_value": ""},
            {"key": "AMOUNT", "type": "string", "fallback_value": "0,00 zł"},
        ],
    },
    "contact-form-ack": {
        "subject": "Otrzymaliśmy Twoją wiadomość — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Dziękujemy za wiadomość!")
            + p(
                "Cześć {{{CUSTOMER_NAME}}}, Twoja wiadomość dotarła do nas. "
                "Odpowiemy najszybciej, jak to możliwe — zwykle w ciągu 1-2 dni roboczych."
            )
            + p("Pozdrawiam,<br>Karolina")
        ),
        "variables": [
            {"key": "CUSTOMER_NAME", "type": "string", "fallback_value": "Kliencie"},
        ],
    },
    "contact-form-notify": {
        "subject": "Nowa wiadomość ze strony — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Nowa wiadomość z formularza kontaktowego")
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px; color:{INK}; margin-bottom:16px;">'
            '<tr><td style="padding:4px 0; width:100px; color:' + MUTED + ';">Od</td><td style="padding:4px 0;">{{{CUSTOMER_NAME}}} ({{{CUSTOMER_EMAIL}}})</td></tr>'
            '<tr><td style="padding:4px 0; color:' + MUTED + ';">Telefon</td><td style="padding:4px 0;">{{{CUSTOMER_PHONE}}}</td></tr>'
            '<tr><td style="padding:4px 0; color:' + MUTED + ';">Temat</td><td style="padding:4px 0;">{{{TOPIC}}}</td></tr>'
            "</table>"
            + f'<div style="background-color:{CREAM}; border-radius:4px; padding:16px; font-size:14px; color:{INK}; line-height:1.6;">'
            "{{{MESSAGE_HTML}}}"
            "</div>"
        ),
        "variables": [
            {"key": "CUSTOMER_NAME", "type": "string", "fallback_value": ""},
            {"key": "CUSTOMER_EMAIL", "type": "string", "fallback_value": ""},
            {"key": "CUSTOMER_PHONE", "type": "string", "fallback_value": "--"},
            {"key": "TOPIC", "type": "string", "fallback_value": ""},
            {"key": "MESSAGE_HTML", "type": "string", "fallback_value": ""},
        ],
    },
    "new-order-alert": {
        "subject": "Nowe zamówienie! — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Sprzedano!")
            + p("Zamówienie <strong>#{{{ORDER_NUMBER}}}</strong> od {{{CUSTOMER_EMAIL}}} na kwotę {{{TOTAL}}}.")
        ),
        "variables": [
            {"key": "ORDER_NUMBER", "type": "number", "fallback_value": 0},
            {"key": "CUSTOMER_EMAIL", "type": "string", "fallback_value": ""},
            {"key": "TOTAL", "type": "string", "fallback_value": "0,00 zł"},
        ],
    },
    "low-stock-alert": {
        "subject": "Niski stan magazynowy — KS",
        "from": FROM_ADDRESS,
        "html": shell(
            h1("Kończy się towar")
            + p(
                "<strong>{{{PRODUCT_NAME}}}</strong> ({{{VARIANT_LABEL}}}, SKU {{{SKU}}}) — "
                "zostało tylko {{{QUANTITY_REMAINING}}} szt."
            )
        ),
        "variables": [
            {"key": "PRODUCT_NAME", "type": "string", "fallback_value": ""},
            {"key": "VARIANT_LABEL", "type": "string", "fallback_value": ""},
            {"key": "SKU", "type": "string", "fallback_value": ""},
            {"key": "QUANTITY_REMAINING", "type": "number", "fallback_value": 0},
        ],
    },
}


def main():
    existing = resend.Templates.list()
    by_name = {t["name"]: t["id"] for t in existing["data"]}

    ids: dict[str, str] = {}
    for name, cfg in TEMPLATES.items():
        params = {
            "name": name,
            "html": cfg["html"],
            "subject": cfg["subject"],
            "from": cfg["from"],
            "variables": cfg["variables"],
        }
        if name in by_name:
            template_id = by_name[name]
            resend.Templates.update({"id": template_id, **params})
            print(f"updated {name} -> {template_id}")
        else:
            created = resend.Templates.create(params)
            template_id = created["id"]
            print(f"created {name} -> {template_id}")

        resend.Templates.publish(template_id)
        print(f"  published {name}")
        ids[name] = template_id

    print("\n--- template IDs (paste into app config) ---")
    for name, tid in ids.items():
        print(f"{name}: {tid}")


if __name__ == "__main__":
    main()
