"""Fetching recipe pages for URL import (ING-2), carefully.

The web app fetches URLs that a person (or the scout) supplies, so the server must not be
turned into a way to reach this computer or the home network: only http(s), every host
(including each redirect) must resolve to public addresses, and pages are capped in size and
time. A name that resolves differently between the check and the request (DNS rebinding) is
not defended against; this is a household tool, not a public service.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 10.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 mealplan/0.1"
)


class FetchError(ValueError):
    """Why a page could not be fetched, in words for the household."""


@dataclass(frozen=True)
class Page:
    url: str  # as asked for
    final_url: str  # after redirects
    html: str


Resolver = Callable[[str], list[str]]
Fetcher = Callable[[str], Page]


def resolve(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
    except socket.gaierror:
        raise FetchError(f"could not find {host}") from None


def check_url(url: str, resolver: Resolver = resolve) -> str:
    """The URL, if it is http(s) to a public host; FetchError otherwise."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"not a web link: {url!r}")
    host = parts.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise FetchError(f"{host} is on this computer or network")
    for address in resolver(host):
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError(f"{host} is on this computer or network")
    return parts.geturl()


def fetch_page(url: str, client: httpx.Client | None = None, resolver: Resolver = resolve) -> Page:
    """GET a page, following up to MAX_REDIRECTS, each hop checked. Text only, size-capped."""
    own = client is None
    http = client or httpx.Client(
        timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    )
    try:
        current = check_url(url, resolver)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with http.stream("GET", current, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        current = check_url(urljoin(current, location), resolver)
                        continue
                    if response.status_code >= 400:
                        raise FetchError(f"the site answered {response.status_code}")
                    kind = response.headers.get("content-type", "text/html")
                    if "html" not in kind and "text" not in kind:
                        raise FetchError(f"not a web page ({kind.split(';')[0]})")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError("the page is too large")
                    encoding = response.encoding or "utf-8"
                    return Page(url, current, body.decode(encoding, errors="replace"))
            except httpx.TimeoutException:
                raise FetchError("the site took too long to answer") from None
            except httpx.HTTPError as e:
                raise FetchError(f"could not load the page ({type(e).__name__})") from None
        raise FetchError("too many redirects")
    finally:
        if own:
            http.close()
