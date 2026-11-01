"""Pure parsing for the keyless news monitor.

Feeds are RSS 2.0 or Atom. No third-party parser: the two shapes are small and
keeping the dependency surface down matters more than handling every malformed
feed in the wild. A feed that will not parse is reported as an error and the
other feeds keep working.

``extract_mentions`` is the deterministic link between an article and the
graph's reference entities: cashtags for tickers, exact roster names for
people. Nothing here calls a model.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.graph.seed_data import POLITICIANS, TICKER_SECTORS

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    """Feed summaries often carry escaped HTML; keep the words, drop the tags."""
    return " ".join(html.unescape(_TAG_RE.sub(" ", value or "")).split())


def _local(tag: str) -> str:
    """ElementTree reports ``{namespace}name``; comparisons want ``name``."""
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element, *names: str) -> str:
    for child in element:
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return strip_html(text)
    return ""


def _link(element: ElementTree.Element) -> str:
    """RSS puts the URL in ``<link>`` text; Atom puts it in ``href``."""
    for child in element:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_date(value: str) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom), normalized to aware UTC."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_feed(payload: bytes) -> list[dict]:
    """Parse one RSS/Atom document into article dicts.

    Raises ``ElementTree.ParseError`` for malformed XML; the source catches it
    per feed so one broken outlet cannot stop the others.
    """
    root = ElementTree.fromstring(payload)
    articles: list[dict] = []
    for entry in root.iter():
        if _local(entry.tag) not in ("item", "entry"):
            continue
        title = _text(entry, "title")
        summary = _text(entry, "description", "summary", "content")
        url = _link(entry)
        published = _text(entry, "pubDate", "published", "updated", "date")
        guid = _text(entry, "guid", "id")
        if not title and not url:
            continue
        articles.append(
            {
                "title": title,
                "summary": summary,
                "url": url,
                "published": published,
                "guid": guid,
            }
        )
    return articles


def extract_mentions(title: str, summary: str = "") -> list[dict]:
    """Deterministic entity mentions: cashtags and exact roster names.

    Cashtags are matched against the reference ticker universe so ``$A`` in a
    headline does not become a ticker by accident. People are matched on their
    full roster name, case-insensitively.
    """
    text = f"{title or ''} {summary or ''}"
    mentions: list[dict] = []
    seen: set[str] = set()

    for match in CASHTAG_RE.finditer(text):
        ticker = match.group(1).upper()
        if ticker in TICKER_SECTORS and ticker not in seen:
            seen.add(ticker)
            mentions.append({"kind": "ticker", "id": ticker, "label": ticker})

    lowered = text.lower()
    for person in POLITICIANS:
        name = person["name"]
        if name.lower() in lowered and person["id"] not in seen:
            seen.add(person["id"])
            mentions.append({"kind": "politician", "id": person["id"], "label": name})

    return mentions


def needs_extraction(record) -> bool:
    """Whether a raw record should go through event extraction.

    Non-news records always do. A news article does only when it names a
    tracked ticker (cashtag) or roster politician: without one, no extracted
    event could attach to the graph, so a model call would be pure cost.
    """
    meta = getattr(record, "raw_json", None) or {}
    if meta.get("source_kind") != "news":
        return True
    return bool(extract_mentions(meta.get("title") or "", meta.get("summary") or ""))
