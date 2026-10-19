from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from resource_fetch import ResourceFetchError, ResourceFetcher


def test_fetch_file_uri(tmp_path: Path) -> None:
    source = tmp_path / "rfc.txt"
    source.write_text("RFC test content", encoding="utf-8")

    resource = ResourceFetcher().fetch(source.as_uri())

    assert resource.content == b"RFC test content"
    assert resource.filename == "rfc.txt"
    assert resource.size == len(resource.content)
    assert resource.final_uri == source.as_uri()


def test_file_uri_rejects_remote_host() -> None:
    with pytest.raises(ResourceFetchError, match="local host"):
        ResourceFetcher().fetch("file://example.com/tmp/test.txt")


def test_unsupported_scheme_is_rejected() -> None:
    with pytest.raises(ResourceFetchError, match="unsupported URI scheme"):
        ResourceFetcher().fetch("gopher://example.com/rfc.txt")


def test_file_size_limit(tmp_path: Path) -> None:
    source = tmp_path / "large.txt"
    source.write_bytes(b"123456")

    with pytest.raises(ResourceFetchError, match="maximum size"):
        ResourceFetcher(max_bytes=5).fetch(source.as_uri())


def test_http_resource_metadata_and_content() -> None:
    response = MagicMock()
    response.headers.get.return_value = None
    response.headers.get_content_type.return_value = "text/plain"
    response.geturl.return_value = "https://example.test/final/rfc.txt"
    response.status = 200
    response.read.side_effect = [b"RFC content", b""]

    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False

    with (
        patch("resource_fetch.ResourceFetcher._validate_network_target"),
        patch("resource_fetch.build_opener") as build_opener,
    ):
        opener = MagicMock()
        opener.open.return_value = context
        build_opener.return_value = opener
        resource = ResourceFetcher().fetch("https://example.test/rfc.txt")

    opener.open.assert_called_once()
    assert resource.content == b"RFC content"
    assert resource.content_type == "text/plain"
    assert resource.filename == "rfc.txt"
    assert resource.status == 200
    assert resource.final_uri == "https://example.test/final/rfc.txt"


def test_content_disposition_filename_is_used() -> None:
    response = MagicMock()
    response.headers.get.side_effect = lambda name: (
        'attachment; filename="rfc-9110.pdf"'
        if name == "Content-Disposition"
        else None
    )
    response.headers.get_content_type.return_value = "application/pdf"
    response.geturl.return_value = "https://example.test/download"
    response.status = 200
    response.read.side_effect = [b"pdf", b""]

    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False

    with (
        patch("resource_fetch.ResourceFetcher._validate_network_target"),
        patch("resource_fetch.build_opener") as build_opener,
    ):
        opener = MagicMock()
        opener.open.return_value = context
        build_opener.return_value = opener
        resource = ResourceFetcher().fetch("https://example.test/download")

    assert resource.filename == "rfc-9110.pdf"


def test_network_resource_is_bounded() -> None:
    response = MagicMock()
    response.headers.get.return_value = None
    response.headers.get_content_type.return_value = "application/octet-stream"
    response.geturl.return_value = "https://example.test/big"
    response.status = 200
    response.read.side_effect = [b"12345", b"6"]

    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False

    with (
        patch("resource_fetch.ResourceFetcher._validate_network_target"),
        patch("resource_fetch.build_opener") as build_opener,
    ):
        opener = MagicMock()
        opener.open.return_value = context
        build_opener.return_value = opener
        with pytest.raises(ResourceFetchError, match="maximum size"):
            ResourceFetcher(max_bytes=5).fetch("https://example.test/big")


def test_network_uri_rejects_credentials() -> None:
    with pytest.raises(ResourceFetchError, match="credentials"):
        ResourceFetcher().fetch("https://user:secret@example.test/rfc.txt")


@pytest.mark.parametrize(
    "uri",
    [
        "http://127.0.0.1/rfc.txt",
        "https://localhost/rfc.txt",
        "ftp://192.168.1.10/rfc.txt",
    ],
)
def test_network_uri_rejects_non_public_targets(uri: str) -> None:
    with pytest.raises(ResourceFetchError, match="non-public"):
        ResourceFetcher().fetch(uri)


def test_network_error_does_not_echo_uri() -> None:
    with patch.object(ResourceFetcher, "_validate_network_target"), patch(
        "resource_fetch.build_opener"
    ) as build_opener:
        opener = MagicMock()
        opener.open.side_effect = URLError("connection failed")
        build_opener.return_value = opener

        with pytest.raises(
            ResourceFetchError, match="failed to fetch network resource"
        ) as exc:
            ResourceFetcher().fetch("https://user:secret@example.test/rfc.txt")

        assert "user:secret" not in str(exc.value)


def test_empty_uri_is_rejected() -> None:
    with pytest.raises(ResourceFetchError, match="must not be empty"):
        ResourceFetcher().fetch("   ")



def test_network_uri_rejects_shared_address_space() -> None:
    shared_address = [(None, None, None, None, ("100.64.0.1", 443))]
    with patch("resource_fetch.socket.getaddrinfo", return_value=shared_address):
        with pytest.raises(ResourceFetchError, match="non-public"):
            ResourceFetcher()._validate_network_target("https://example.test/resource")
