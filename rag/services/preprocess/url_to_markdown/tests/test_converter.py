"""URL→Markdown 変換のテスト(ネットワーク非依存・fetcher/extractor 注入)。"""

from __future__ import annotations

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
