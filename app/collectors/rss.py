import json

import feedparser

from app.collectors.base import Collection
from app.core.security import fetch
from app.processing.normalizer import clean, language, timestamp


def parse_feed(body: bytes) -> list[dict]:
    parsed = feedparser.parse(body, sanitize_html=False)
    if not parsed.version or (parsed.bozo and not parsed.entries):
        raise ValueError("INVALID_FEED")
    entries = []
    for item in parsed.entries[:500]:
        title = clean(item.get("title", ""))
        link = item.get("link", "")
        if not title or not link:
            continue
        published = item.get("published_parsed") or item.get("updated_parsed")
        summary = clean(item.get("summary", ""))
        entries.append(
            {
                "external_id": str(item.get("id") or link),
                "title": title,
                "summary": summary[:12000],
                "content": "",
                "original_url": link,
                "author": clean(item.get("author", "")),
                "language": language(title + summary),
                "published_at": timestamp(published),
                "publication_time_inferred": not bool(published),
                "raw_payload": json.loads(json.dumps(item, default=str)),
            }
        )
    return entries


class RSSCollector:
    def collect(self, url: str, headers: dict) -> Collection:
        status, response_headers, body = fetch(url, headers)
        return Collection([] if status == 304 else parse_feed(body), response_headers, status == 304)
