"""URL→Markdown 変換のテスト(ネットワーク非依存・fetcher/extractor 注入)。"""

from __future__ import annotations

import socket

import httpx
import pytest

from app import converters
from app.converters import convert


def _convert(source: bytes, *, fetcher=None, extractor=None, url_guard=None):
    return convert(
        source,
        "text/plain",
        "url_to_markdown",
        None,
        fetcher=fetcher,
        extractor=extractor,
        url_guard=url_guard,
    )


def test_unsupported_profile_passthrough() -> None:
    outcome = convert(b"https://example.com", "text/plain", "passthrough", None)
    assert outcome.converted is False
    assert outcome.converter_name == "passthrough"


def test_empty_source_passthrough() -> None:
    outcome = _convert(b"")
    assert outcome.converted is False
    assert "url_empty" in outcome.warnings


def test_no_targets_passthrough() -> None:
    outcome = _convert(b"# comment only\n\n")
    assert outcome.converted is False
    assert "url_no_targets" in outcome.warnings


def test_converts_fetched_html_to_markdown() -> None:
    # SSRF guard は名前解決に依存するため、guard を注入してネットワーク非依存にする。
    fetched: dict[str, str] = {}

    def fetcher(url: str) -> str:
        fetched[url] = "<html><body><p>hello</p></body></html>"
        return fetched[url]

    def extractor(html: str) -> str | None:
        return "# Title\n\nhello"

    outcome = _convert(
        b"https://example.com/doc\n",
        fetcher=fetcher,
        extractor=extractor,
        url_guard=lambda _u: True,
    )
    assert outcome.converted is True
    assert outcome.converter_name == "url_to_markdown"
    assert outcome.derived_content_type == "text/markdown; charset=utf-8"
    assert outcome.derived_bytes is not None
    body = outcome.derived_bytes.decode("utf-8")
    assert "## Source: https://example.com/doc" in body
    assert "hello" in body


def test_blocks_private_and_non_http_urls() -> None:
    def fetcher(url: str) -> str:  # pragma: no cover - blocked 前提で呼ばれない
        raise AssertionError("blocked URL must not be fetched")

    # private(127.0.0.1) と file:// は SSRF guard で拒否され、全件失敗で passthrough。
    outcome = _convert(
        b"http://127.0.0.1/secret\nfile:///etc/passwd\n", fetcher=fetcher, extractor=lambda h: "x"
    )
    assert outcome.converted is False
    assert any(w.startswith("url_blocked") for w in outcome.warnings)


def test_fetch_failure_is_skipped() -> None:
    def fetcher(url: str) -> str:
        raise RuntimeError("boom")

    outcome = _convert(
        b"https://example.com/doc\n",
        fetcher=fetcher,
        extractor=lambda h: "x",
        url_guard=lambda _u: True,
    )
    assert outcome.converted is False
    assert any(w.startswith("url_fetch_failed") for w in outcome.warnings)


# ---- SSRF: _default_fetcher のリダイレクト再検証 / rebinding ガード ----
# リテラル IP を使い getaddrinfo をローカル解決に留めてネットワーク非依存にする。
_PUBLIC_IP = "93.184.216.34"  # is_global
_PUBLIC_IP_ALT = "8.8.8.8"  # is_global
_INTERNAL_META = "169.254.169.254"  # link-local(クラウド metadata)


def test_default_fetcher_blocks_redirect_to_internal_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == _PUBLIC_IP:
            return httpx.Response(
                302, headers={"location": f"http://{_INTERNAL_META}/latest/meta-data/"}
            )
        raise AssertionError("内部 IP へは接続してはならない")

    with pytest.raises(RuntimeError, match="url_blocked_redirect"):
        converters._default_fetcher(
            f"http://{_PUBLIC_IP}/", transport=httpx.MockTransport(handler)
        )


def test_default_fetcher_caps_redirect_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"http://{_PUBLIC_IP_ALT}/next"})

    with pytest.raises(RuntimeError, match="url_too_many_redirects"):
        converters._default_fetcher(
            f"http://{_PUBLIC_IP}/", transport=httpx.MockTransport(handler)
        )


def test_default_fetcher_follows_safe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": f"http://{_PUBLIC_IP_ALT}/final"})
        return httpx.Response(200, content=b"<html><body><p>ok</p></body></html>")

    body = converters._default_fetcher(
        f"http://{_PUBLIC_IP}/start", transport=httpx.MockTransport(handler)
    )
    assert "ok" in body


def test_is_safe_url_blocks_dns_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    # 公開風ホスト名が内部 IP に解決される rebinding を guard が拒否すること。
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port or 0))]

    monkeypatch.setattr(converters.socket, "getaddrinfo", fake_getaddrinfo)
    assert converters._is_safe_url("http://totally-public.example/") is False
