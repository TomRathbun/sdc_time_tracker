"""One email shell, matching the tracker: dark surface, indigo accent, Inter."""

from html import escape

from app.config import APP_NAME

_FONT = "Inter, 'Segoe UI', system-ui, sans-serif"
_MONO = "'JetBrains Mono', Consolas, monospace"


def render_email(title: str, body_html: str, kicker: str = "", show_logo: bool = False) -> str:
    """Wrap inner HTML in the same card the site uses."""
    logo = ""
    if show_logo:
        logo = (
            '<td align="right" width="52" valign="middle">'
            '<img src="cid:app_logo" width="48" height="48" alt="" '
            'style="display:block;border-radius:12px;border:1px solid rgba(255,255,255,0.12);">'
            "</td>"
        )
    kicker_html = ""
    if kicker:
        kicker_html = (
            '<div style="font-size:11px;font-weight:600;letter-spacing:0.14em;'
            'text-transform:uppercase;color:#818cf8;margin-bottom:6px;">'
            f"{escape(kicker)}</div>"
        )
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#020617;">
<table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#020617" style="background:#020617;">
<tr><td align="center" style="padding:28px 16px;">
<table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:600px;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,0.08);font-family:{_FONT};">
<tr><td height="3" style="height:3px;line-height:3px;font-size:0;background:#818cf8;background-image:linear-gradient(90deg,#818cf8,#38bdf8);">&nbsp;</td></tr>
<tr><td style="padding:28px 32px 8px;">
<table width="100%" border="0" cellspacing="0" cellpadding="0"><tr>
<td valign="middle">
{kicker_html}
<div style="font-size:22px;font-weight:700;color:#f8fafc;line-height:1.3;">{escape(title)}</div>
</td>
{logo}
</tr></table>
</td></tr>
<tr><td style="padding:8px 32px 28px;color:#cbd5e1;font-size:14px;line-height:1.6;">
{body_html}
</td></tr>
<tr><td style="padding:14px 32px 20px;border-top:1px solid rgba(255,255,255,0.06);font-size:12px;color:#64748b;">
{escape(APP_NAME)}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def stat_tile(label: str, value: str, color: str) -> str:
    return (
        '<td width="50%" bgcolor="#1e293b" style="background:#1e293b;border-radius:12px;'
        'border:1px solid rgba(255,255,255,0.06);text-align:center;padding:18px 12px;">'
        f'<div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#94a3b8;margin-bottom:8px;">{escape(label)}</div>'
        f'<div style="font-size:32px;font-weight:700;color:{color};font-family:{_MONO};">{escape(value)}</div>'
        "</td>"
    )
