"""Stdlib RSS-2.0 + Atom parser -> list[NewsItem]. Defensive: malformed input yields []."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from .models import NewsItem, make_id

_TAG = re.compile(r"<[^>]+>")
_MAX_SNIPPET = 400          # cap stored text so the reader shows a snippet, never a full body


def _strip_html(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def _snippet(text: str) -> str:
    """Plain-text preview capped at _MAX_SNIPPET chars — never the full (possibly licensed) body."""
    text = text.strip()
    return text if len(text) <= _MAX_SNIPPET else text[:_MAX_SNIPPET].rstrip() + "…"


def _to_ms(value: str) -> int:
    """Parse an RFC-822 (RSS) or ISO-8601 (Atom) timestamp to epoch ms; 0 if unparseable."""
    value = (value or "").strip()
    if not value:
        return 0
    try:                                   # RFC 822: "Mon, 01 Jun 2026 12:04:00 GMT"
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        pass
    try:                                   # ISO 8601: "2026-06-01T10:30:00Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]          # drop the {namespace}


def parse_feed(data: bytes | str, *, source: str, market: str) -> list[NewsItem]:
    """Parse RSS-2.0 ``<item>`` or Atom ``<entry>`` nodes. Never raises on bad input."""
    try:
        # expat (3.12+) caps entity expansion, so billion-laughs raises ParseError here rather
        # than blowing up memory; external entities are not resolved (no XXE). Keep on a guarded
        # Python — don't swap for a parser that resolves entities.
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, TypeError):
        return []
    items: list[NewsItem] = []
    for node in (e for e in root.iter() if _localname(e.tag) in ("item", "entry")):
        title = link = summary = content = pub = ""
        for child in node:
            name = _localname(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link":
                link = (child.text or "").strip() or child.attrib.get("href", "").strip()
            elif name in ("description", "summary") and not summary:
                summary = _strip_html(child.text or "")
            elif name == "content" and not content:
                content = _strip_html(child.text or "")   # only a fallback; full bodies live here
            elif name in ("pubDate", "published", "updated", "date") and not pub:
                pub = child.text or ""
        if not title and not link:
            continue
        items.append(NewsItem(
            id=make_id(link, title, source),
            title=html.unescape(title),
            url=link,
            summary=_snippet(summary or content),   # prefer the real summary; cap either way
            source=source,
            market=market,
            published_ms=_to_ms(pub),
        ))
    return items
