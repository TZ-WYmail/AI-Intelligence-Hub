import calendar
import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.models import now


def clean(text: str) -> str:
    soup = BeautifulSoup(text or "", "html.parser")
    for node in soup(["script", "style", "iframe"]):
        node.decompose()
    return " ".join(soup.get_text(" ").split())


def canonical(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("invalid article URL")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(query)), ""))


def timestamp(value) -> datetime:
    if not value:
        return now()
    if isinstance(value, (tuple, list)):
        return datetime.fromtimestamp(calendar.timegm(value), UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def content_hash(title: str, summary: str) -> str:
    value = re.sub(r"\s+", " ", f"{title} {summary}".casefold()).strip()
    return hashlib.sha256(value.encode()).hexdigest()


def language(text: str, fallback: str = "en") -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else fallback
