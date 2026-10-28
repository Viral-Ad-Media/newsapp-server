"""Public HTTPS only, DNS pinned connections, bounded bodies and no redirects/proxies."""

import ipaddress
import socket
import time
from urllib.parse import urlsplit, urljoin
import urllib3
from requests.certs import where as trusted_ca_bundle


class DocumentHTTPError(ValueError):
    def __init__(self, status):
        self.status = int(status)
        super().__init__(
            f"Source returned HTTP {self.status}; redirects are disabled or exhausted."
        )


def validate_feed_url(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii")
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
        if not addresses or any(
            not ipaddress.ip_address(ip).is_global for ip in addresses
        ):
            raise ValueError()
        return parsed, host, sorted(addresses)[0]
    except (ValueError, OSError, UnicodeError):
        raise ValueError(
            "Use a public HTTPS feed URL without credentials, query strings or redirects."
        ) from None


def fetch_document(
    url,
    timeout=10,
    *,
    accept="text/html",
    allow_missing=False,
    xml=False,
    redirects=0,
    allowed_host=None,
    url_check=None,
):
    parsed = urlsplit(url)
    if redirects and not allowed_host:
        raise ValueError("Redirects require an explicit publisher host.")
    if allowed_host:
        normalized = (parsed.hostname or "").lower().removeprefix("www.")
        publisher = allowed_host.lower().removeprefix("www.")
        if normalized != publisher and not normalized.endswith("." + publisher):
            raise ValueError("Redirect leaves the saved publisher domain.")
    parsed, host, address = validate_feed_url(url)
    if url_check:
        url_check(url)
    pool = urllib3.HTTPSConnectionPool(
        address,
        port=443,
        server_hostname=host,
        assert_hostname=host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=trusted_ca_bundle(),
        maxsize=1,
    )
    response = None
    try:
        response = pool.urlopen(
            "GET",
            parsed.path or "/",
            headers={
                "Host": host,
                "User-Agent": "AbokiNews/1.0",
                "Accept-Encoding": "identity",
                "Accept": accept,
            },
            redirect=False,
            retries=False,
            preload_content=False,
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
        )
        if redirects and response.status in (301, 302, 303, 307, 308):
            target = urljoin(url, response.headers.get("Location", ""))
            if not response.headers.get("Location"):
                raise ValueError("Missing redirect location.")
            return fetch_document(
                target,
                timeout,
                accept=accept,
                allow_missing=allow_missing,
                xml=xml,
                redirects=redirects - 1,
                allowed_host=allowed_host,
                url_check=url_check,
            )
        if allow_missing and response.status in (404, 410):
            return ""
        if response.status != 200:
            raise DocumentHTTPError(response.status)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Compressed feeds are not supported.")
        chunks, size, deadline = [], 0, time.monotonic() + timeout
        while True:
            chunk = response.read(32768, decode_content=False)
            if not chunk:
                break
            size += len(chunk)
            if size > 2 * 1024 * 1024 or time.monotonic() > deadline:
                raise ValueError("Feed exceeded the size or time limit.")
            chunks.append(chunk)
        body = b"".join(chunks)
        if xml and (b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper()):
            raise ValueError("XML entities are not permitted.")
        return body.decode("utf-8-sig")
    finally:
        if response:
            response.close()
        pool.close()


def fetch_xml(url, timeout=10):
    return fetch_document(
        url,
        timeout,
        accept="application/rss+xml, application/atom+xml, application/xml, text/xml",
        xml=True,
    )
