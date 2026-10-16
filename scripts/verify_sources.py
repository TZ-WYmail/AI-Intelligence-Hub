"""Recheck actual publisher-linked RSS feeds. No body/full-article crawling."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from app.collectors.gdelt import GDELTCollector
from app.collectors.rss import RSSCollector
from app.models import now


def candidates():
    rows = []
    pages = [json.loads(line) for line in Path("docs/source-discovery.jsonl").read_text().splitlines()]
    selected = {
        "nvidia.com": [
            ("releases.xml", "NVIDIA Newsroom", "semiconductor"),
            ("nvidiablog", "NVIDIA Blog", "ai"),
            ("blog/feed", "NVIDIA Developer", "ai"),
        ],
        "nasa.gov": [
            ("/news-release/feed/", "NASA News", "manufacturing"),
            ("/technology/feed/", "NASA Technology", "manufacturing"),
            ("/aeronautics/feed/", "NASA Aeronautics", "manufacturing"),
            ("/missions/station/feed/", "NASA Station", "manufacturing"),
            ("/missions/artemis/feed/", "NASA Artemis", "manufacturing"),
        ],
        "eia.gov": [
            ("todayinenergy.xml", "EIA Today in Energy", "energy"),
            ("press_rss.xml", "EIA Press", "energy"),
            ("gas_diesel_rss.xml", "EIA Gas Diesel", "energy"),
        ],
        "ecb.europa.eu": [
            ("/rss/press.html", "ECB Press", "finance"),
            ("/rss/blog.html", "ECB Blog", "finance"),
            ("/rss/statpress.html", "ECB Statistics", "finance"),
            ("/rss/pub.html", "ECB Publications", "finance"),
            ("/rss/wppub.html", "ECB Working Papers", "finance"),
            ("/rss/rbu.html", "ECB Research Bulletin", "finance"),
        ],
    }
    for page in pages:
        for domain, specs in selected.items():
            if domain not in page["page"]:
                continue
            for suffix, name, industry in specs:
                for _, url in page.get("links", []):
                    if url.endswith(suffix):
                        rows.append(
                            dict(
                                name=name,
                                source_type="rss",
                                url=url,
                                industry_ids=[industry],
                                poll_interval_seconds=1800,
                                trust_level=0.9,
                                language="en",
                                evidence_url=page["page"],
                                terms_note="NVIDIA: free personal noncommercial information use only; retain attribution"
                                if domain == "nvidia.com"
                                else "Publisher provides this feed for subscription; personal metadata/summary reader only; no full text redistribution. Numeric rate limit not found; conservative 30m polling.",
                                authentication="none",
                                terms_review="personal RSS subscription; commercial reuse not authorized by this verification",
                            )
                        )
                        break
    for category, industry in [
        ("cs.AI", "ai"),
        ("cs.LG", "ai"),
        ("cs.RO", "manufacturing"),
        ("q-bio", "health"),
    ]:
        rows.append(
            dict(
                name="arXiv " + category,
                source_type="rss",
                url="https://rss.arxiv.org/rss/" + category,
                industry_ids=[industry],
                poll_interval_seconds=3600,
                trust_level=0.75,
                language="en",
                evidence_url="https://info.arxiv.org/help/rss.html",
                authentication="none",
                terms_note="Metadata CC0; full text rights vary. Single connection, max 1 request/3s across all arXiv feeds. Daily publication feed, not breaking news.",
                terms_review="https://info.arxiv.org/help/api/tou.html",
            )
        )
    rows.append(
        dict(
            name="GDELT AI",
            source_type="gdelt",
            url="https://api.gdeltproject.org/api/v2/doc/doc?query=artificial%20intelligence&mode=artlist&format=json&maxrecords=50&timespan=1d",
            industry_ids=["ai"],
            poll_interval_seconds=900,
            trust_level=0.6,
            language="en",
            evidence_url="https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
            authentication="none",
            terms_note="Discovery metadata only; linked publisher rights apply; no numeric quota verified.",
            terms_review="manual review required",
        )
    )
    return rows


def verify(row):
    start = time.monotonic()
    try:
        result = (GDELTCollector() if row["source_type"] == "gdelt" else RSSCollector()).collect(
            row["url"], {}
        )
        dates = [r["published_at"].isoformat() for r in result.entries if not r["publication_time_inferred"]]
        row.update(
            enabled=bool(result.entries),
            status="verified" if result.entries else "empty",
            entries=len(result.entries),
            latest_published_at=max(dates) if dates else None,
            checked_at=now().isoformat(),
            fields=["title", "link", "summary", "published_at", "raw_payload"],
            update_delay="Not measured across time; snapshot only",
            error=None,
        )
    except Exception as exc:
        row.update(
            enabled=False,
            status="unverified",
            entries=0,
            checked_at=now().isoformat(),
            error=type(exc).__name__,
        )
    row["elapsed_seconds"] = round(time.monotonic() - start, 2)
    return row


def main():
    rows = candidates()
    # arXiv is verified serially with a mandatory delay; others can run across publishers.
    normal = [row for row in rows if "arxiv.org" not in row["url"]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(verify, normal))
    for row in rows:
        if "arxiv.org" in row["url"]:
            time.sleep(3.1)
            verified.append(verify(row))
    Path("config/sources.yaml").write_text(yaml.safe_dump(verified, allow_unicode=True, sort_keys=False))
    Path("docs/source-validation.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2))
    for row in verified:
        print(row["name"], row["enabled"], row["entries"], row.get("latest_published_at"), row.get("error"))
    print("VERIFIED", sum(r["enabled"] for r in verified), "/", len(verified))


if __name__ == "__main__":
    main()
