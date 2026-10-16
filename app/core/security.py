import ipaddress
import socket
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings


class UnsafeURL(ValueError):
    pass


def resolve_public(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise UnsafeURL("Only public HTTP(S) URLs without credentials are allowed")
        if parts.port not in {None, 80, 443}:
            raise UnsafeURL("Only standard HTTP(S) ports allowed")
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise UnsafeURL("Non-public network address rejected")
    except (OSError, ValueError) as exc:
        raise UnsafeURL("URL cannot be resolved to public addresses") from exc
    ipv4 = [a[4][0] for a in addresses if a[0] == socket.AF_INET]
    return ipv4[0] if ipv4 else addresses[0][4][0]


def validate_url(url: str) -> str:
    resolve_public(url)
    return url


class RemoteError(Exception):
    def __init__(self, code: str, retry_after: int = 0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


_last_request: dict[str, float] = {}


def retry_seconds(value: str) -> int:
    try:
        return max(0, min(int(value), 86400))
    except ValueError:
        try:
            return max(0, min(int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()), 86400))
        except (ValueError, TypeError, OverflowError):
            return 60


def fetch(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """Validate every redirect; bounded response. Environment proxies disabled."""
    cfg = settings()
    deadline = time.monotonic() + cfg.http_timeout * 2
    with httpx.Client(timeout=cfg.http_timeout, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            address = resolve_public(url)
            parts = urlsplit(url)
            hostname = parts.hostname
            if hostname.endswith("arxiv.org"):
                # Single-worker global rate cap, including first request after a restart.
                elapsed = time.monotonic() - _last_request.get("arxiv.org", time.monotonic())
                time.sleep(max(0, 3.1 - elapsed))
                _last_request["arxiv.org"] = time.monotonic()
            pinned = str(httpx.URL(url).copy_with(host=address))
            request_headers = {**(headers or {}), "Host": parts.netloc}
            with client.stream(
                "GET", pinned, headers=request_headers, extensions={"sni_hostname": hostname}
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = str(httpx.URL(url).join(response.headers["location"]))
                    continue
                if response.status_code == 304:
                    return 304, dict(response.headers), b""
                if response.status_code >= 400:
                    retry = response.headers.get("retry-after", "0")
                    raise RemoteError(f"HTTP_{response.status_code}", retry_seconds(retry))
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise RemoteError("TOTAL_TIMEOUT")
                    body.extend(chunk)
                    if len(body) > cfg.max_response_bytes:
                        raise RemoteError("RESPONSE_TOO_LARGE")
                return response.status_code, dict(response.headers), bytes(body)
    raise RemoteError("TOO_MANY_REDIRECTS")


def public_url(url: str) -> str:
    """For display only: drop query strings potentially containing credentials."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
