"""Turning raw Gmail payloads into safe plain text, and drafts into MIME.

Everything here is pure (no network, no Google libraries) so it can be unit-tested.

Email is the most hostile input this program reads: anyone can send you text that an LLM will see.
Cleaning therefore does more than decode: it removes invisible characters (used to smuggle instructions
past a human reader), drops HTML that is hidden from the human (display:none, zero font size...), and
bounds the size. The cleaned text is still *untrusted*; the tools label it so.
"""
from __future__ import annotations

import base64
import re
import unicodedata
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, Optional

MAX_BODY_CHARS = 6000
MAX_RECIPIENTS = 10
MAX_DRAFT_BODY_CHARS = 10000

_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "section", "article", "header", "footer"}
_SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|%)?\s*(?:;|$)"
                           r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)|max-height\s*:\s*0", re.IGNORECASE)
_ADDRESS = re.compile(r"^[^@\s<>\",;]+@[^@\s<>\",;]+\.[^@\s<>\",;]+$")


# ------------------------------------------------------------------------------ text hygiene
def clean_text(text: str) -> str:
    """Remove control and invisible format characters (zero-width, bidi, Unicode 'tag' characters) and
    collapse whitespace. Visible characters, including accents and emoji, are kept."""
    kept = []
    for ch in text:
        if ch in "\n\t":
            kept.append(ch)
            continue
        if ch == "\r":
            kept.append("\n")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            if ch == "‍":  # zero-width joiner is part of some emoji; a lone one is harmless
                kept.append(ch)
            continue
        kept.append(" " if ch == " " else ch)
    text = "".join(kept).replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def one_line(text: str, limit: int) -> str:
    text = " ".join(clean_text(str(text or "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def truncate(text: str, limit: int = MAX_BODY_CHARS) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit].rstrip() + "\n[...truncated]", True)


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, hides_content)
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in _VOID_TAGS:
            return
        attr = dict(attrs)
        hides = tag in _SKIP_TAGS or "hidden" in attr or bool(_HIDDEN_STYLE.search(attr.get("style") or ""))
        self._stack.append((tag, hides))
        if hides:
            self._hidden += 1

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        for i in range(len(self._stack) - 1, -1, -1):  # tolerate unclosed tags
            if self._stack[i][0] == tag:
                for _, hides in self._stack[i:]:
                    if hides:
                        self._hidden -= 1
                del self._stack[i:]
                break

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must never break reading mail
        pass
    return clean_text("".join(parser.parts))


# ------------------------------------------------------------------------------ Gmail payloads
def decode_b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def header(payload: dict, name: str) -> str:
    for item in payload.get("headers") or []:
        if str(item.get("name", "")).lower() == name.lower():
            return str(item.get("value", ""))
    return ""


def _decode_part(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    raw = decode_b64url(data)
    charset = "utf-8"
    for item in part.get("headers") or []:
        if str(item.get("name", "")).lower() == "content-type":
            match = re.search(r"charset=\"?([\w-]+)", str(item.get("value", "")), re.IGNORECASE)
            if match:
                charset = match.group(1)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_body(payload: dict) -> tuple[str, list[str]]:
    """Return (plain text, attachment file names) for a Gmail ``payload`` tree.

    text/plain wins over text/html (multipart/alternative); HTML is only used when there is no plain part.
    """
    plain: list[str] = []
    html: list[str] = []
    attachments: list[str] = []

    def walk(part: dict) -> None:
        mime = str(part.get("mimeType", ""))
        filename = part.get("filename")
        if filename:
            attachments.append(one_line(filename, 120))
            return
        children = part.get("parts") or []
        if children:
            for child in children:
                walk(child)
        elif mime == "text/plain":
            plain.append(_decode_part(part))
        elif mime == "text/html":
            html.append(_decode_part(part))

    walk(payload)
    if any(p.strip() for p in plain):
        text = clean_text("\n\n".join(plain))
    else:
        text = html_to_text("\n".join(html))
    return text, attachments


def addresses(value: str) -> list[str]:
    """Bare, validated e-mail addresses from a header value or user input."""
    out = []
    for _, addr in getaddresses([value or ""]):
        addr = addr.strip()
        if addr and _ADDRESS.match(addr):
            out.append(addr)
    return out


def parse_recipients(value: str, field: str) -> list[str]:
    """Validate recipients typed by the model. Raises ValueError with a message the model can act on."""
    value = value or ""
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be a single line.")
    parsed = [a.strip() for _, a in getaddresses([value]) if a.strip()]
    bad = [a for a in parsed if not _ADDRESS.match(a)]
    if bad:
        raise ValueError(f"Invalid e-mail address in {field}: {bad[0]!r}")
    if len(parsed) > MAX_RECIPIENTS:
        raise ValueError(f"At most {MAX_RECIPIENTS} recipients are allowed in {field}.")
    return parsed


def build_mime(draft: dict[str, Any]) -> EmailMessage:
    """Build the MIME message for a draft dict: to, cc, subject, body, in_reply_to, references."""
    msg = EmailMessage()
    msg["To"] = ", ".join(draft["to"])
    if draft.get("cc"):
        msg["Cc"] = ", ".join(draft["cc"])
    msg["Subject"] = draft["subject"]
    if draft.get("in_reply_to"):
        msg["In-Reply-To"] = draft["in_reply_to"]
        refs = " ".join(x for x in (draft.get("references"), draft["in_reply_to"]) if x)
        msg["References"] = refs
    msg.set_content(draft["body"])
    return msg


def draft_body_for_gmail(draft: dict[str, Any]) -> dict:
    raw = base64.urlsafe_b64encode(build_mime(draft).as_bytes()).decode("ascii")
    message: dict[str, Any] = {"raw": raw}
    if draft.get("thread_id"):
        message["threadId"] = draft["thread_id"]
    return {"message": message}


def reply_subject(subject: str) -> str:
    subject = one_line(subject, 200)
    return subject if re.match(r"^(re|ré|aw|sv)\s*:", subject, re.IGNORECASE) else f"Re: {subject}"


def normalise_message(raw: dict, tz, with_body: bool) -> dict:
    """Gmail API message resource -> the dict shape the tools use (see mail_backends)."""
    from datetime import datetime, timezone

    payload = raw.get("payload") or {}
    try:
        date = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=timezone.utc).astimezone(tz)
    except (KeyError, ValueError, TypeError):
        date = None
    message: dict[str, Any] = {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "from": one_line(header(payload, "From"), 200),
        "to": one_line(header(payload, "To"), 300),
        "cc": one_line(header(payload, "Cc"), 300),
        "reply_to": one_line(header(payload, "Reply-To"), 200),
        "subject": one_line(header(payload, "Subject"), 300) or "(no subject)",
        "date": date,
        "snippet": one_line(raw.get("snippet", ""), 300),
        "unread": "UNREAD" in (raw.get("labelIds") or []),
        "message_id_header": one_line(header(payload, "Message-ID") or header(payload, "Message-Id"), 300),
        "references": one_line(header(payload, "References"), 600),
    }
    if with_body:
        message["body"], message["attachments"] = extract_body(payload)
    return message


def first_address(value: Optional[str]) -> str:
    found = addresses(value or "")
    return found[0] if found else ""
