"""RAPTOR 再帰要約索引(ICLR 2024)の共有ロジック。

leaf chunk を再帰的に cluster + 要約して多層级の summary node を作り、leaf と summary の
両方を索引できるようにする。長文書の主題レベル質問と細部質問の双方に強くなる。

クラスタリングと summary chunk の構築は **決定論**(順序ベース)で core に置き、要約本文の生成は
``summarizer``(OCI Enterprise AI 等)を注入する。要約が得られない/失敗した cluster は安全に
skip し、最低でも leaf chunk はそのまま残す(opt-in・安全縮退)。

`hierarchical_parent_child` chunking の build 時拡張として ingestion 側から呼ぶ。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rag_pipeline_core.chunking import Chunk

# summarizer: 結合テキスト -> 要約(None/空は当該 cluster を skip)。
Summarizer = Callable[[str], Awaitable[str | None]]

RAPTOR_SUMMARY_STRATEGY = "raptor_summary"


def cluster_chunks(chunks: list[Chunk], cluster_size: int) -> list[list[Chunk]]:
    """chunk を順序ベースで ``cluster_size`` ごとの cluster へ決定論で分割する。"""
    size = max(2, cluster_size)
    return [chunks[i : i + size] for i in range(0, len(chunks), size)]


def build_summary_chunk(
    cluster: list[Chunk], summary_text: str, *, level: int, index: int
) -> Chunk:
    """1 cluster の要約を summary chunk(metadata 付き)へ構築する。"""
    text = summary_text.strip()
    start = cluster[0].start_offset
    end = cluster[-1].end_offset
    child_indices = ",".join(str(c.index) for c in cluster)
    metadata = {
        "raptor_summary": True,
        "raptor_level": level,
        "raptor_child_count": len(cluster),
        "raptor_child_indices": child_indices,
        "chunk_strategy": RAPTOR_SUMMARY_STRATEGY,
        "content_kind": "summary",
    }
    return Chunk(text=text, index=index, start_offset=start, end_offset=end, metadata=metadata)


async def build_raptor_summaries(
    chunks: list[Chunk],
    *,
    summarizer: Summarizer,
    cluster_size: int = 5,
    max_levels: int = 2,
) -> list[Chunk]:
    """leaf chunk から再帰要約 tree を構築し、leaf + 全 summary node を返す。

    各 level で cluster ごとに ``summarizer`` を呼び、得られた要約を次 level の入力にする。
    要約 0 件・cluster 1 個へ収束したら停止。全 level で要約が得られなければ leaf のみ返す。
    """
    if len(chunks) < 2 or max_levels < 1:
        return chunks
    summaries: list[Chunk] = []
    current = chunks
    next_index = max((c.index for c in chunks), default=-1) + 1
    for level in range(1, max_levels + 1):
        clusters = cluster_chunks(current, cluster_size)
        if len(clusters) < 1:
            break
        level_summaries: list[Chunk] = []
        for cluster in clusters:
            joined = "\n\n".join(c.text for c in cluster if c.text.strip())
            if not joined:
                continue
            try:
                summary = await summarizer(joined)
            except Exception:  # noqa: BLE001 - 要約失敗は当該 cluster を skip して継続
                summary = None
            if not summary or not summary.strip():
                continue
            level_summaries.append(
                build_summary_chunk(cluster, summary, level=level, index=next_index)
            )
            next_index += 1
        if not level_summaries:
            break
        summaries.extend(level_summaries)
        current = level_summaries
        if len(level_summaries) < 2:
            break
    return [*chunks, *summaries]
