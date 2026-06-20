"""音声文字起こし(ASR)の共有 remap。

OCI AI Speech(backend の service backend)とローカル faster-whisper(parser マイクロ
サービス)の両方が、転写結果を **同一の `StructuredExtraction`** へ決定論で再マップするための
共有ヘルパ。これにより「どの転写経路でも同じ schema・同じ chunk/citation 挙動」を保証する。

core 本体の依存(pydantic)のみで完結し、faster-whisper / oci などの重い依存は持たない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag_parser_core.extraction import DocumentElement, StructuredExtraction

ASR_DOCUMENT_TYPE = "文字起こし"
ASR_TEMPLATE = "asr_transcript"


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
    backend: str = "asr",
) -> StructuredExtraction:
    """転写テキスト/区間を `StructuredExtraction` へ再マップする(OCI/ローカル共通)。

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
        document_type=ASR_DOCUMENT_TYPE,
        elements=elements,
        parser_artifacts=dict(artifacts),
    )
