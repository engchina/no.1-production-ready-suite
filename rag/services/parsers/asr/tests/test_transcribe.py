"""ASR 転写と remap のテスト(faster-whisper 非依存・transcriber 注入)。"""

from __future__ import annotations

from rag_parser_core.asr import TranscriptSegment, build_transcript_extraction

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
