"""Small, model-neutral URI resource fetcher for Claude Ensemble.

The fetcher retrieves resources only. Parsing, indexing, Graph updates, and
model calls belong to downstream consumers.

Network fetching performs a public-address preflight. Private, loopback,
link-local, reserved, multicast, and unspecified addresses are rejected for
both the initial URI and HTTP(S)/FTP redirects. The fetcher disables ambient
HTTP proxy configuration and is intended for trusted callers; it does not
claim to prevent DNS-rebinding races between preflight and connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
    url2pathname,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_SUPPORTED_SCHEMES = frozenset({"file", "ftp", "http", "https"})
_NETWORK_SCHEMES = frozenset({"ftp", "http", "https"})


class ResourceFetchError(RuntimeError):
    """Raised when a URI cannot be fetched safely."""


@dataclass(frozen=True)
class Resource:
    """Fetched resource and transport metadata."""

    uri: str
    content: bytes
    content_type: str | None
    filename: str | None
    size: int
    status: int | None = None
    final_uri: str | None = None


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect before urllib follows it."""

    def __init__(self, fetcher: ResourceFetcher) -> None:
        super().__init__()
        self._fetcher = fetcher

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._fetcher._validate_network_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ResourceFetcher:
    """Fetch file, FTP, HTTP, and HTTPS resources with bounded reads."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        user_agent: str = "Claude-Ensemble-ResourceFetcher/1.0",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._opener = build_opener(
            ProxyHandler({}),
            _SafeRedirectHandler(self),
        )

    def fetch(self, uri: str) -> Resource:
        """Fetch a URI and return its bytes plus transport metadata."""
        if not uri or not uri.strip():
            raise ResourceFetchError("URI must not be empty")

        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        if scheme not in _SUPPORTED_SCHEMES:
            raise ResourceFetchError(
                f"unsupported URI scheme: {parsed.scheme or '<none>'}"
            )

        if scheme == "file":
            return self._fetch_file(uri, parsed)

        self._validate_network_target(uri)
        return self._fetch_network(uri)

    def _fetch_file(self, uri: str, parsed) -> Resource:
        if parsed.netloc.lower() not in ("", "localhost"):
            raise ResourceFetchError("file URI must refer to the local host")

        if parsed.query or parsed.fragment:
            raise ResourceFetchError("file URI must not contain a query or fragment")

        # url2pathname performs the platform-specific percent decoding.
        # Do not call unquote first, or encoded percent sequences can be decoded
        # twice on platforms where url2pathname also unquotes.
        path = Path(url2pathname(parsed.path)).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise ResourceFetchError("cannot resolve local file path") from exc

        if not path.is_file():
            raise ResourceFetchError(f"file does not exist: {path}")

        try:
            with path.open("rb") as source:
                content = source.read(self.max_bytes + 1)
        except OSError as exc:
            raise ResourceFetchError(f"cannot read file: {path}") from exc

        if len(content) > self.max_bytes:
            raise ResourceFetchError(
                f"resource exceeds maximum size of {self.max_bytes} bytes"
            )

        return Resource(
            uri=uri,
            content=content,
            content_type=None,
            filename=path.name or None,
            size=len(content),
            final_uri=uri,
        )

    def _validate_network_target(self, uri: str) -> None:
        """Reject credentials and non-public network destinations."""
        try:
            parsed = urlparse(uri)
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname
        except ValueError as exc:
            raise ResourceFetchError("invalid network URI") from exc

        if scheme not in _NETWORK_SCHEMES:
            raise ResourceFetchError(
                f"network fetch does not allow URI scheme: {scheme or '<none>'}"
            )
        if not hostname:
            raise ResourceFetchError("network URI must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ResourceFetchError("network URI credentials are not supported")

        try:
            addresses = {
                info[4][0]
                for info in socket.getaddrinfo(
                    hostname, parsed.port, type=socket.SOCK_STREAM
                )
            }
        except (OSError, ValueError) as exc:
            raise ResourceFetchError("could not resolve network host") from exc

        if not addresses:
            raise ResourceFetchError("network URI host has no addresses")

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ResourceFetchError(
                    "network host resolved to an invalid address"
                ) from exc
            # is_global also rejects shared address space (100.64.0.0/10)
            # and other non-global ranges not covered by individual flags.
            if not ip.is_global:
                raise ResourceFetchError(
                    "network URI resolves to a non-public address"
                )

    def _fetch_network(self, uri: str) -> Resource:
        request = Request(uri, headers={"User-Agent": self.user_agent})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > self.max_bytes:
                        raise ResourceFetchError(
                            f"resource exceeds maximum size of {self.max_bytes} bytes"
                        )

                content = self._read_bounded(response)
                content_type = response.headers.get_content_type()
                filename = _filename_from_headers(
                    response.headers.get("Content-Disposition")
                )
                if not filename:
                    filename = Path(urlparse(response.geturl()).path).name or None

                status = getattr(response, "status", None)
                final_uri = response.geturl()

                return Resource(
                    uri=uri,
                    content=content,
                    content_type=content_type,
                    filename=filename,
                    size=len(content),
                    status=status,
                    final_uri=final_uri,
                )
        except ResourceFetchError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            raise ResourceFetchError("failed to fetch network resource") from exc

    def _read_bounded(self, response) -> bytes:
        chunks: list[bytes] = []
        remaining = self.max_bytes

        while remaining:
            chunk = response.read(min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)

        if remaining == 0:
            extra = response.read(1)
            if extra:
                raise ResourceFetchError(
                    f"resource exceeds maximum size of {self.max_bytes} bytes"
                )

        return b"".join(chunks)


def _filename_from_headers(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None

    match = re.search(
        r"""filename\s*=\s*(?:"([^"]+)"|([^;\s]+))""",
        content_disposition,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1) or match.group(2)
