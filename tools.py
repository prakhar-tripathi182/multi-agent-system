"""Tools the agents can call: web search (Tavily) and SSRF-safe page scraping."""

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain.tools import tool
from requests.adapters import HTTPAdapter
from tavily import TavilyClient

load_dotenv()  # read keys from the .env file (never hardcode them)

# Tavily client; the key comes from the TAVILY_API_KEY environment variable
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 1_000_000  # 1 MB cap so giant pages cannot exhaust memory
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


class UnsafeURLError(ValueError):
    """Raised when a URL or its resolved IP points to a non-public destination."""


def _is_public_ip(ip_str: str) -> bool:
    """Return True only if ip_str is a globally routable public IP address."""
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return False

    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so IPv4 rules still apply
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _resolve_and_validate_url(url: str) -> tuple[str, str, str]:
    """
    Validate scheme/hostname, resolve DNS once, ensure EVERY resolved IP is public,
    and return (hostname, host_header, pinned_ip) so the request connects directly to
    that IP while keeping the real Host header and TLS SNI/certificate checks.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError("only http and https URLs are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("missing hostname.")

    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        raise UnsafeURLError("localhost addresses are blocked.")

    try:
        explicit_port = parsed.port
    except ValueError as e:
        raise UnsafeURLError(f"invalid port: {e}") from e
    port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)

    host_part = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    host_header = host_part if default_port else f"{host_part}:{port}"

    # If the host is already an IP literal, validate it directly without DNS
    ip_obj = None
    try:
        ip_obj = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        pass

    if ip_obj is not None:
        if not _is_public_ip(str(ip_obj)):
            raise UnsafeURLError("non-public IP address is blocked.")
        return hostname, host_header, str(ip_obj)

    try:
        addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURLError(f"could not resolve hostname ({e}).") from e

    resolved_ips = []
    for info in addr_info:
        ip_str = info[4][0]
        if not _is_public_ip(ip_str):
            raise UnsafeURLError("hostname resolved to a non-public IP address.")
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    if not resolved_ips:
        raise UnsafeURLError("hostname did not resolve to any IP address.")

    # Prefer IPv4 when available for broader host network compatibility
    ipv4 = [ip for ip in resolved_ips if ":" not in ip]
    return hostname, host_header, (ipv4[0] if ipv4 else resolved_ips[0])


class _PinnedIPAdapter(HTTPAdapter):
    """
    Connects directly to the pre-validated IP address while keeping the original
    hostname for HTTP Host headers, TLS SNI, and TLS certificate verification.
    """

    def __init__(self, pinned_ip: str, original_host: str):
        self.pinned_ip = pinned_ip
        self.original_host = original_host
        super().__init__()

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params["host"] = self.pinned_ip
        if host_params.get("scheme") == "https":
            pool_kwargs["server_hostname"] = self.original_host
            pool_kwargs["assert_hostname"] = self.original_host
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


def _fetch_public_url(url: str) -> str:
    """Fetch a public URL while validating the initial target and every redirect hop."""
    current_url = url

    for _ in range(_MAX_REDIRECTS + 1):
        hostname, host_header, pinned_ip = _resolve_and_validate_url(current_url)

        with requests.Session() as session:
            # Ignore ambient proxy env vars so traffic cannot bypass IP validation
            session.trust_env = False
            adapter = _PinnedIPAdapter(pinned_ip, hostname)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            with session.get(
                current_url,
                timeout=8,
                headers={"User-Agent": _USER_AGENT, "Host": host_header},
                allow_redirects=False,
                stream=True,
            ) as resp:
                if resp.status_code in _REDIRECT_STATUSES and resp.headers.get("location"):
                    current_url = urljoin(current_url, resp.headers["location"])
                    continue

                # Read at most _MAX_RESPONSE_BYTES to protect memory
                chunks = []
                total = 0
                for chunk in resp.iter_content(chunk_size=16384):
                    if not chunk:
                        continue
                    remaining = _MAX_RESPONSE_BYTES - total
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    total += len(chunks[-1])

                encoding = resp.encoding or resp.apparent_encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace")

    raise UnsafeURLError("too many redirects.")


@tool
def web_search(query: str) -> str:
    """Search the web for recent and reliable information on a topic. Returns Titles, URLs and snippets."""
    try:
        results = tavily.search(query=query, max_results=5)
    except Exception as e:
        # Return the error as text so the agent (and pipeline) keeps running
        return f"WEB SEARCH FAILED: {str(e)}"

    # Format each result as Title / URL / Snippet (first 300 chars)
    out = []
    for r in results["results"]:
        out.append(f"Title:{r['title']}\nURL:{r['url']}\nSnippet:{r['content'][:300]}\n")
    return "\n-----\n".join(out)


@tool
def scrape_url(url: str) -> str:
    """Scrape and return clean text content from a given public URL for deeper reading."""
    try:
        html = _fetch_public_url(url)
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements before extracting text
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()

        # Keep only the first 3000 characters to stay within the LLM's context budget
        return soup.get_text(separator=" ", strip=True)[:3000]

    except Exception as e:
        return f"Could not scrape URL: {str(e)}"
