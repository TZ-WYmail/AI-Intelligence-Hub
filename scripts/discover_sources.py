"""Read publisher RSS directories; output actual linked feed URLs, not guessed addresses."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

pages = [
    "https://www.nvidia.com/en-us/about-nvidia/rss/",
    "https://www.federalreserve.gov/feeds/feeds.htm",
    "https://www.nasa.gov/rss-feeds/",
    "https://www.nih.gov/news-events/news-releases",
    "https://www.eia.gov/tools/rssfeeds/",
    "https://www.ecb.europa.eu/home/html/rss.en.html",
    "https://www.who.int/rss-feeds",
    "https://www.jpl.nasa.gov/rss/",
]


def discover(url):
    try:
        response = httpx.get(url, follow_redirects=True, timeout=20, trust_env=False)
        soup = BeautifulSoup(response.content, "html.parser")
        links = [
            (a.get_text(" ", strip=True)[:90], urljoin(str(response.url), a.get("href", "")))
            for a in soup.find_all(["a", "link"], href=True)
            if any(t in a.get("href", "").lower() for t in [".xml", "/feed", "/rss"])
        ]
        return {"page": url, "status": response.status_code, "links": links[:45]}
    except Exception as exc:
        return {"page": url, "error": type(exc).__name__}


if __name__ == "__main__":
    import json

    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(discover, pages):
            print(json.dumps(result, ensure_ascii=False))
