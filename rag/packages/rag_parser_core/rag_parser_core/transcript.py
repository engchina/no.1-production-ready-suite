"""音声文字起こしの結果を `StructuredExtraction` へ再マップする共有 helper。

OCI AI Speech(backend の ``app.clients.oci_speech``)の転写結果を、ほかの解析と同じ schema・
同じ chunk / citation の挙動になる抽出へ決定論で再マップする。

core 本体の依存(pydantic)のみで完結し、oci などの重い依存は持たない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag_parser_core.extraction import DocumentElement, StructuredExtraction

TRANSCRIPT_DOCUMENT_TYPE = "文字起こし"


@dataclass(frozen=True)
class TranscriptSegment:
    """転写の 1 区間(タイムスタンプ付き)。"""

    text: str
    start: float | None = None
    end: float | None = None


def _format_timestamp(seconds: float | None) -> str | None:
    """秒を ``HH:MM:SS`` へ整形する(None はそのまま)。"""
    if seconds is None or seconds < 0:
        return None
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def build_transcript_extraction(
    *,
    text: str = "",
    segments: Sequence[TranscriptSegment] = (),
    language: str | None = None,
    backend: str = "oci_speech",
) -> StructuredExtraction:
    """転写テキスト/区間を `StructuredExtraction` へ再マップする。

    各 segment を 1 paragraph 要素にし、開始/終了秒・整形タイムスタンプを metadata に残す。
    raw_text は明示 text 優先、無ければ segment テキストの連結。
    """
    elements: list[DocumentElement] = []
    raw_parts: list[str] = []
    order = 0
    for segment in segments:
        cleaned = segment.text.strip()
        if not cleaned:
            continue
        metadata: dict[str, str | float] = {}
        if segment.start is not None:
            metadata["start_seconds"] = round(float(segment.start), 3)
            timestamp = _format_timestamp(segment.start)
            if timestamp is not None:
                metadata["timestamp"] = timestamp
        if segment.end is not None:
            metadata["end_seconds"] = round(float(segment.end), 3)
        if language:
            metadata["language"] = language
        elements.append(
            DocumentElement(
                kind="paragraph",
                text=cleaned,
                order=order,
                content_kind="transcript",
                source_parser=backend,
                metadata=metadata,
            )
        )
        raw_parts.append(cleaned)
        order += 1

    raw_text = text.strip() or "\n".join(raw_parts)
    artifacts: dict[str, str] = {"asr_backend": backend}
    if language:
        artifacts["asr_language"] = language
    return StructuredExtraction(
        raw_text=raw_text,
        document_type=TRANSCRIPT_DOCUMENT_TYPE,
        elements=elements,
        parser_artifacts=dict(artifacts),
    )
