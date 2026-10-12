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
import re
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# 1 回の変換で取得する URL の上限(内部サービス・timeout 付きの想定)。
_MAX_URLS = 20
# 1 URL あたりの取得サイズ上限(10 MiB)。
_MAX_FETCH_BYTES = 10 * 1024 * 1024
# リダイレクト追従の上限(SSRF: 各 hop を再検証するため httpx 任せにせず自前で追従する)。
_MAX_REDIRECTS = 5

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


def _default_fetcher(url: str, *, transport: object | None = None) -> str:
    """httpx でページ HTML を取得する(サイズ上限・SSRF 安全なリダイレクト追従)。

    SSRF 対策: リダイレクトは httpx 任せ(``follow_redirects=True``)にせず自前で追従し、
    初手だけでなく **各 hop を ``_is_safe_url`` で再検証** する。``follow_redirects=True`` は
    検証を挟まず公開 URL から内部 IP(metadata 等)へ飛ばされ得るため使わない
    (リポジトリ全体が ``follow_redirects=False`` 規約)。``transport`` はテスト注入用。

    残存リスク: ``getaddrinfo``(guard)と httpx の connect の間に残る DNS rebinding の窓。
    metadata 等 link-local は各 hop の ``_is_safe_url`` で遮断済みのため、実証可能な攻撃
    (リダイレクト→内部 IP)は塞いである。この経路を「外向きの任意 URL 受付」へ広げる、
    または同一ネットワークに private な機微 API が居る構成になったら、``_resolve_safe_ip(host)``
    で検証済み IP へ connect を固定し Host/TLS SNI を元ホストに保つ IP 固定 transport へ更新する。
    """
    import httpx

    current = url
    with httpx.Client(
        follow_redirects=False,
        timeout=20.0,
        headers={"User-Agent": "production-ready-rag/url-to-markdown"},
        transport=transport,
    ) as client:
        for _hop in range(_MAX_REDIRECTS + 1):
            # ponytail: 各 hop の接続直前に再解決+再検証する。getaddrinfo と httpx の名前
            # 解決の間に残る DNS rebinding の窓は許容(完全 pin は IP 固定 transport が必要)。
            if not _is_safe_url(current):
                raise RuntimeError("url_blocked_redirect")
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError("url_redirect_without_location")
                    current = str(httpx.URL(current).join(location))
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_FETCH_BYTES:
                        break
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace")
    raise RuntimeError("url_too_many_redirects")


def _default_extractor(html: str) -> str | None:
    """trafilatura で本文を Markdown 抽出する(失敗時 None)。"""
    import trafilatura

    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=True,
        with_metadata=False,
    )
    return None if markdown is None else unescape_trafilatura_markdown(markdown)


# trafilatura 2 が Markdown の出力で本文に足すエスケープ(#1544)。RAG はこの Markdown を
# 描画せず生の文字列として分割・索引するため、`element_id` が `element\_id` になると
# chunk の本文・全文検索の索引・引用にバックスラッシュが入る。trafilatura にエスケープを
# 止める設定は無いので、ここで戻す。
# - 本文の `* _ ` ~ [ ] <`: もとからあったバックスラッシュを倍にしたうえで 1 つ足す(2k+1 個)。
# - 段落・一覧の項目・引用の先頭の `# > - +` と `1.` / `1)`: 1 つ足す。`\#` だけは残す(戻すと
#   `rag_parser_core` の `MARKDOWN_HEADING` が見出しと読み、元のページに無い章節ができる)。
# - 表のセルの `|` は `\|` になるが、列の区切りと区別するために残す。
# - コード(fence の block と inline のコード)の中はエスケープされないので触らない。
_INLINE_ESCAPED_CHARS = frozenset("*_`~[]<")
# 行頭のインデントと、一覧・引用の印(入れ子を含む)。その後ろが段落・項目の本文の先頭。
_BLOCK_PREFIX = r"^((?:[ \t]*(?:[-+*]|\d{1,9}[.)]|>)[ \t]+)*[ \t]*)"
_BLOCK_MARKER_ESCAPED_RE = re.compile(_BLOCK_PREFIX + r"\\(>|[-+](?=[ \t]|$))")
_BLOCK_ORDERED_ESCAPED_RE = re.compile(_BLOCK_PREFIX + r"(\d{1,9})\\([.)])(?=[ \t]|$)")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def unescape_trafilatura_markdown(markdown: str) -> str:
    """trafilatura 2 が足した Markdown のエスケープを戻す。

    コードの中・表のセルの `\\|`・行頭の `\\#` は残す。
    """
    lines: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        match = _FENCE_RE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            lines.append(line)
            continue
        if match:
            fence = match.group(1)
            lines.append(line)
            continue
        line = _BLOCK_MARKER_ESCAPED_RE.sub(r"\1\2", line)
        line = _BLOCK_ORDERED_ESCAPED_RE.sub(r"\1\2\3", line)
        lines.append(_unescape_inline(line))
    return "\n".join(lines)


def _unescape_inline(line: str) -> str:
    """1 行の本文の `\\_` などを戻す。inline のコード(対になる `` ` `` の間)はそのまま残す。"""
    if "\\" not in line:
        return line
    out: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if char == "\\":
            end = index
            while end < length and line[end] == "\\":
                end += 1
            run = end - index
            if end < length and line[end] in _INLINE_ESCAPED_CHARS and run % 2 == 1:
                # 2k+1 個のバックスラッシュ + 記号 → 元の k 個 + 記号
                # (trafilatura の `_escape_run` の逆)。
                out.append("\\" * (run // 2) + line[end])
                index = end + 1
            else:
                out.append(line[index:end])
                index = end
            continue
        if char == "`":
            end = index
            while end < length and line[end] == "`":
                end += 1
            closing = _find_code_span_end(line, end, end - index)
            if closing is None:
                out.append(line[index:end])
                index = end
            else:
                out.append(line[index:closing])
                index = closing
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _find_code_span_end(line: str, start: int, ticks: int) -> int | None:
    """`start` 以降で、同じ長さの `` ` `` の並びが閉じる位置の直後を返す(無ければ None)。"""
    index = start
    length = len(line)
    while index < length:
        if line[index] != "`":
            index += 1
            continue
        end = index
        while end < length and line[end] == "`":
            end += 1
        if end - index == ticks:
            return end
        index = end
    return None
