"""URL→Markdown 前処理マイクロサービスの変換実装。

原本(URL を 1 行 1 件で並べたテキスト)を受け取り、各 URL の本文を取得して
boilerplate を除去した Markdown へ再マップし、`ConvertResponse` を返す。Firecrawl 的な
「Web ページ→クリーン Markdown」をローカル OSS(trafilatura)だけで実現し、外部 SaaS は
一切呼ばない(確定スタック非抵触)。

セキュリティ:
- ``http`` / ``https`` のみ許可。
- 名前解決した IP が loopback / private / link-local / reserved の場合は **SSRF 対策**で拒否。
- 取得サイズ・URL 件数に上限を設ける。

未対応 profile・URL 抽出 0 件・全件失敗のときは passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# 1 回の変換で取得する URL の上限(内部サービス・timeout 付きの想定)。
_MAX_URLS = 20
# 1 URL あたりの取得サイズ上限(10 MiB)。
_MAX_FETCH_BYTES = 10 * 1024 * 1024

# fetcher: url -> HTML 文字列。差し替え可能にしてテストをネットワーク非依存にする。
Fetcher = Callable[[str], str]
# extractor: HTML -> Markdown(None=本文抽出不可)。
Extractor = Callable[[str], str | None]
# url_guard: url -> 取得可否(SSRF 対策)。テストでは差し替え可能。
UrlGuard = Callable[[str], bool]


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
    *,
    fetcher: Fetcher | None = None,
    extractor: Extractor | None = None,
    url_guard: UrlGuard | None = None,
) -> ConvertOutcome:
    """選択プリセットで変換する。url_to_markdown 以外・失敗・空は passthrough へ縮退する。"""
    if preprocess_profile != "url_to_markdown":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _url_to_markdown(
        source_bytes,
        fetcher=fetcher or _default_fetcher,
        extractor=extractor or _default_extractor,
        url_guard=url_guard or _is_safe_url,
    )


def _parse_urls(source_bytes: bytes) -> list[str]:
    """原本テキストから http(s) URL を 1 行 1 件で抽出する(重複除去・上限適用)。"""
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = source_bytes.decode("utf-8", errors="replace")
    urls: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
        if len(urls) >= _MAX_URLS:
            break
    return urls


def _is_safe_url(url: str) -> bool:
    """SSRF 対策: http(s) かつ解決先 IP が公開アドレスのときだけ True。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None)
    except OSError:
        return False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _url_to_markdown(
    source_bytes: bytes, *, fetcher: Fetcher, extractor: Extractor, url_guard: UrlGuard
) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="url_empty")
    urls = _parse_urls(source_bytes)
    if not urls:
        return ConvertOutcome.passthrough(reason="url_no_targets")

    warnings: list[str] = []
    sections: list[str] = []
    for url in urls:
        if not url_guard(url):
            warnings.append(f"url_blocked:{urlparse(url).hostname or url}")
            continue
        try:
            html = fetcher(url)
        except Exception:  # noqa: BLE001 - 取得失敗は当該 URL を skip して継続
            warnings.append(f"url_fetch_failed:{urlparse(url).hostname or url}")
            continue
        markdown = extractor(html) if html else None
        if not markdown or not markdown.strip():
            warnings.append(f"url_extract_empty:{urlparse(url).hostname or url}")
            continue
        sections.append(f"## Source: {url}\n\n{markdown.strip()}")

    if not sections:
        # 取得 0 件でも、どの URL がなぜ失敗したかの per-url warnings は保持する。
        return ConvertOutcome(
            converted=False,
            converter_name="passthrough",
            converter_version="v1",
            warnings=(*warnings, "url_all_failed"),
        )

    derived = ("\n\n".join(sections) + "\n").encode("utf-8")
    return ConvertOutcome(
        converted=True,
        converter_name="url_to_markdown",
        converter_version="v1",
        derived_bytes=derived,
        derived_content_type="text/markdown; charset=utf-8",
        warnings=tuple(warnings),
    )


def _default_fetcher(url: str) -> str:
    """httpx でページ HTML を取得する(サイズ上限・リダイレクト追従)。"""
    import httpx

    with httpx.Client(
        follow_redirects=True,
        timeout=20.0,
        headers={"User-Agent": "production-ready-rag/url-to-markdown"},
    ) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > _MAX_FETCH_BYTES:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _default_extractor(html: str) -> str | None:
    """trafilatura で本文を Markdown 抽出する(失敗時 None)。"""
    import trafilatura

    return trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=True,
        with_metadata=False,
    )
