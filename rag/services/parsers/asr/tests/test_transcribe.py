"""ASR 転写と remap のテスト(faster-whisper 非依存・transcriber 注入)。"""

from __future__ import annotations

import asyncio

import pytest
from rag_parser_core.asr import TranscriptSegment, build_transcript_extraction

import app.main as main_module
from app.transcribe import transcribe


def test_transcribe_uses_injected_transcriber() -> None:
    captured: dict[str, str] = {}

    def fake(path: str) -> tuple[str, list[TranscriptSegment], str | None]:
        captured["path"] = path
        return (
            "こんにちは 世界",
            [TranscriptSegment("こんにちは", 0.0, 1.2), TranscriptSegment("世界", 1.2, 2.0)],
            "ja",
        )

    text, segments, language = transcribe(b"fake-audio", suffix=".mp3", transcriber=fake)
    assert text == "こんにちは 世界"
    assert [s.text for s in segments] == ["こんにちは", "世界"]
    assert language == "ja"
    # 一時ファイルが渡され、拡張子が保持される。
    assert captured["path"].endswith(".mp3")


def test_build_transcript_extraction_remap() -> None:
    extraction = build_transcript_extraction(
        text="",
        segments=[
            TranscriptSegment("最初の発話", 0.0, 3.0),
            TranscriptSegment("次の発話", 3.0, 6.5),
        ],
        language="ja",
        backend="asr",
    )
    # raw_text は segment 連結、要素はタイムスタンプ metadata 付き。
    assert "最初の発話" in extraction.raw_text and "次の発話" in extraction.raw_text
    assert extraction.document_type == "文字起こし"
    assert len(extraction.elements) == 2
    first = extraction.elements[0]
    assert first.content_kind == "transcript"
    assert first.metadata["start_seconds"] == 0.0
    assert first.metadata["timestamp"] == "00:00:00"
    assert first.metadata["language"] == "ja"
    assert extraction.parser_artifacts["asr_language"] == "ja"


def test_build_transcript_extraction_skips_empty_segments() -> None:
    extraction = build_transcript_extraction(
        segments=[TranscriptSegment("  "), TranscriptSegment("有効")],
    )
    assert len(extraction.elements) == 1
    assert extraction.elements[0].text == "有効"


def test_parse_runs_transcribe_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    used_worker = False

    async def fake_to_thread(func, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal used_worker
        used_worker = True
        return func(*args, **kwargs)

    def fake_transcribe(
        audio_bytes: bytes,
        *,
        suffix: str = ".bin",
    ) -> tuple[str, list[TranscriptSegment], str | None]:
        assert audio_bytes == b"fake-audio"
        assert suffix == ".mp3"
        return "hello", [TranscriptSegment("hello", 0.0, 1.0)], "en"

    monkeypatch.setattr(main_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(main_module, "transcribe", fake_transcribe)

    class FakeUpload:
        filename = "a.mp3"

        async def read(self) -> bytes:
            return b"fake-audio"

    response = asyncio.run(
        main_module.parse(
            FakeUpload(),  # type: ignore[arg-type]
            content_type="audio/mpeg",
        )
    )

    assert response.extraction is not None
    assert response.extraction.raw_text == "hello"
    assert used_worker is True
