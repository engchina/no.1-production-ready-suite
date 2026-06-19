"""PII マスクの変換テスト。

Presidio/spaCy 非依存に passthrough 経路と redactor 注入経路を検証する
(実 NER は重量モデルを要するため CI では injection で代替する)。
"""

from __future__ import annotations

from rag_parser_core.source import SourceModality, SourceProfile

from app.converters import convert


def _convert(source: bytes, content_type: str = "text/plain", *, redactor=None, profile=None):
    return convert(source, content_type, "pii_redact", profile, redactor=redactor)


def test_unsupported_profile_passthrough() -> None:
    outcome = convert(b"hello", "text/plain", "passthrough", None)
    assert outcome.converted is False
    assert outcome.converter_name == "passthrough"


def test_empty_source_passthrough() -> None:
    outcome = _convert(b"")
    assert outcome.converted is False
    assert "pii_empty" in outcome.warnings


def test_non_text_passthrough() -> None:
    outcome = _convert(b"\x89PNG\x00", content_type="image/png")
    assert outcome.converted is False
    assert "pii_not_text" in outcome.warnings


def test_no_findings_passthrough() -> None:
    # redactor が入力と同一を返す(検出 0 件)→ 派生物を作らず passthrough。
    outcome = _convert(b"no pii here", redactor=lambda t: (t, []))
    assert outcome.converted is False
    assert "pii_no_findings" in outcome.warnings


def test_redacts_and_keeps_counts_only() -> None:
    def redactor(text: str) -> tuple[str, list[str]]:
        return "<REDACTED> からメール", ["pii_redacted:PERSON=1"]

    outcome = _convert("山田太郎 からメール".encode(), redactor=redactor)
    assert outcome.converted is True
    assert outcome.converter_name == "pii_redact"
    assert outcome.derived_bytes is not None
    body = outcome.derived_bytes.decode("utf-8")
    assert "<REDACTED>" in body
    assert "山田太郎" not in body
    # warning は件数のみ(PII の値は含めない)。
    assert outcome.warnings == ("pii_redacted:PERSON=1",)
    assert all("山田" not in w for w in outcome.warnings)


def test_redactor_exception_passthrough() -> None:
    def boom(_t: str) -> tuple[str, list[str]]:
        raise RuntimeError("nlp error")

    outcome = _convert(b"some text", redactor=boom)
    assert outcome.converted is False
    assert "pii_redact_failed" in outcome.warnings


def test_text_modality_routes_without_content_type() -> None:
    profile = SourceProfile(
        original_file_name="note.txt",
        sanitized_file_name="note.txt",
        content_type="",
        file_size_bytes=8,
        content_sha256="0" * 64,
        modality=SourceModality.TEXT,
        parser_profile="auto",
    )
    outcome = _convert(
        b"raw",
        content_type="",
        redactor=lambda t: ("<REDACTED>", ["pii_redacted:EMAIL_ADDRESS=1"]),
        profile=profile,
    )
    assert outcome.converted is True
