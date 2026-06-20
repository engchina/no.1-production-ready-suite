"""音声取込(ASR)の OCI→ローカル fallback 配線テスト。"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from rag_parser_core.asr import TranscriptSegment, build_transcript_extraction
from rag_parser_core.registry import ParserRegistryResult

from app.config import Settings
from app.rag.ingestion import IngestionPipeline


class _FakeSpeech:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self._payload = payload
        self.called = False

    async def transcribe(
        self, source_bytes: bytes, *, content_type: str, document_id: str
    ) -> dict[str, object] | None:
        self.called = True
        return self._payload


class _FakeParserService:
    def __init__(self, extraction: object | None) -> None:
        self._extraction = extraction
        self.called = False

    def runner(
        self, backend: str, source_bytes: bytes, source_profile: object, content_type: str
    ) -> ParserRegistryResult:
        self.called = True
        return ParserRegistryResult(extraction=cast(Any, self._extraction), parser_backend=backend)


def _pipeline(
    *, speech_payload: dict[str, object] | None, local_extraction: object | None
) -> tuple[IngestionPipeline, Any, Any]:
    speech = _FakeSpeech(speech_payload)
    pipeline = IngestionPipeline(
        vlm=cast(Any, object()),
        genai=cast(Any, object()),
        oracle=cast(Any, object()),
        object_storage=cast(Any, object()),
        document_understanding=cast(Any, object()),
        speech=cast(Any, speech),
        settings=Settings(rag_parser_asr_enabled=True),
    )
    parser_service = _FakeParserService(local_extraction)
    pipeline._parser_service = cast(Any, parser_service)
    return pipeline, speech, parser_service


def _transcribe(pipeline: IngestionPipeline) -> Any:
    return asyncio.run(
        pipeline._transcribe_audio(
            trace_id="t",
            document_id="doc-audio",
            source_bytes=b"audio-bytes",
            content_type="audio/mpeg",
            source_profile=None,
            cancel_checker=None,
        )
    )


def test_audio_uses_oci_speech_first() -> None:
    payload = build_transcript_extraction(
        segments=[TranscriptSegment("オーシーアイ", 0.0, 1.0)], language="ja", backend="oci_speech"
    ).model_dump()
    pipeline, speech, parser_service = _pipeline(speech_payload=payload, local_extraction=None)
    extraction = _transcribe(pipeline)
    assert extraction is not None
    assert "オーシーアイ" in extraction.raw_text
    assert speech.called is True
    # OCI が成功したのでローカル ASR は呼ばれない。
    assert parser_service.called is False


def test_audio_falls_back_to_local_whisper() -> None:
    local = build_transcript_extraction(
        segments=[TranscriptSegment("ローカル", 0.0, 1.0)], language="ja", backend="asr"
    )
    pipeline, speech, parser_service = _pipeline(speech_payload=None, local_extraction=local)
    extraction = _transcribe(pipeline)
    assert extraction is not None
    assert "ローカル" in extraction.raw_text
    assert speech.called is True
    assert parser_service.called is True


def test_audio_returns_none_when_all_unavailable() -> None:
    pipeline, _speech, parser_service = _pipeline(speech_payload=None, local_extraction=None)
    assert _transcribe(pipeline) is None
    assert parser_service.called is True


def test_audio_disabled_skips_transcription() -> None:
    speech = _FakeSpeech(None)
    pipeline = IngestionPipeline(
        vlm=cast(Any, object()),
        genai=cast(Any, object()),
        oracle=cast(Any, object()),
        object_storage=cast(Any, object()),
        document_understanding=cast(Any, object()),
        speech=cast(Any, speech),
        settings=Settings(rag_parser_asr_enabled=False),
    )
    pipeline._parser_service = cast(Any, _FakeParserService(None))
    assert _transcribe(pipeline) is None
    assert speech.called is False
