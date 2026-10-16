"""Read the tracker Gmail inbox over IMAP and store new messages."""

from __future__ import annotations

import imaplib
import logging
import re
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from html import unescape

from sqlalchemy.orm import Session

from app.config import IMAP_ENABLED, IMAP_HOST, IMAP_PORT, SMTP_PASSWORD, SMTP_USER
from app.models import InboundEmail

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")
_MAX_BODY = 8000
_MAX_NEW = 40


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = unescape(text)
    text = _WS_RE.sub("\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _body_text(msg) -> str:
    plain = None
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    body = (plain or "").strip() or _strip_html(html or "")
    if len(body) > _MAX_BODY:
        body = body[:_MAX_BODY] + "…"
    return body


def _received_at(msg) -> datetime | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when


def poll_inbox(db: Session) -> tuple[int, str]:
    """Fetch new inbox messages. Returns (count stored, status).

    Opens the mailbox read-only, so Gmail still shows the mail as unread.
    """
    if not IMAP_ENABLED:
        return 0, "Inbox reading is off. Set SMTP_USER and SMTP_PASSWORD, then restart."

    user = (SMTP_USER or "").strip()
    password = (SMTP_PASSWORD or "").replace(" ", "")
    try:
        mailbox = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
        mailbox.login(user, password)
        mailbox.select("INBOX", readonly=True)
        status, data = mailbox.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            mailbox.logout()
            return 0, "Inbox is empty."
        uids = [int(u) for u in data[0].split()]
    except Exception as exc:
        logger.error("Inbox poll failed: %s", exc)
        return 0, f"Could not read the inbox. {exc}"

    known = {row[0] for row in db.query(InboundEmail.imap_uid).all()}
    fresh = [u for u in uids if u not in known][-_MAX_NEW:]
    stored = 0
    try:
        for uid in fresh:
            status, fetched = mailbox.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1] if isinstance(fetched[0], tuple) else None
            if not raw:
                continue
            msg = message_from_bytes(raw)
            name, addr = parseaddr(_decode_header(msg.get("From")))
            db.add(InboundEmail(
                imap_uid=uid,
                message_id=(_decode_header(msg.get("Message-ID")) or "")[:500],
                from_name=(name or "")[:200],
                from_addr=(addr or "")[:200],
                subject=(_decode_header(msg.get("Subject")) or "(no subject)")[:500],
                body_text=_body_text(msg),
                received_at=_received_at(msg),
            ))
            stored += 1
        if stored:
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Inbox store failed: %s", exc)
        stored = 0
        note = f"Read the server, but could not save messages. {exc}"
    else:
        note = f"Saved {stored} new message(s)." if stored else "No new mail."
    finally:
        try:
            mailbox.logout()
        except Exception:
            pass
    try:
        from app.services.email_checkout import process_checkout_replies
        checked_out = process_checkout_replies(db)
        if checked_out:
            note += f" Checked out {checked_out} from email."
    except Exception as exc:
        logger.error("Checkout reply handling failed: %s", exc)
    return stored, note
