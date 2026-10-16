import json

from app.collectors.base import Collection
from app.core.security import fetch
from app.processing.normalizer import clean, timestamp


class GDELTCollector:
    def collect(self, url: str, headers: dict) -> Collection:
        _, response_headers, body = fetch(url, headers)
        data = json.loads(body)
        if not isinstance(data.get("articles"), list):
            raise ValueError("INVALID_GDELT")
        entries = []
        for item in data["articles"][:250]:
            if not item.get("title") or not item.get("url"):
                continue
            entries.append(
                {
                    "external_id": item["url"],
                    "title": clean(item["title"]),
                    "summary": "",
                    "content": "",
                    "original_url": item["url"],
                    "language": item.get("language", "en"),
                    "author": "",
                    "published_at": timestamp(item.get("seendate")),
                    "publication_time_inferred": True,
                    "raw_payload": item,
                }
            )
        return Collection(entries, response_headers)
