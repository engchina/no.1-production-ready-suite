"""PII マスクの変換テスト。

Presidio/spaCy の日本語モデルに依存せずに、変換の経路(redactor の注入)と、行ごとの解析・置き換え
(解析器の差し替え)を検証する。実の日本語モデルを使うテストは、モデルが入っている環境だけで動く
(CI の venv は lock の依存だけでモデルを持たない)。値はすべて架空(example.com・555 の番号)。
"""

from __future__ import annotations

import importlib.util

import pytest
from presidio_analyzer import RecognizerResult
from rag_parser_core.source import SourceModality, SourceProfile

from app import converters
from app.converters import convert, placeholder, split_segments


def _convert(source: bytes, content_type: str = "text/plain", *, redactor=None, profile=None):
    return convert(source, content_type, "pii_redact", profile, redactor=redactor)


def _profile(modality: SourceModality, *, content_type: str = "") -> SourceProfile:
    return SourceProfile(
        original_file_name="note.txt",
        sanitized_file_name="note.txt",
        content_type=content_type,
        file_size_bytes=8,
        content_sha256="0" * 64,
        modality=modality,
        parser_profile="auto",
    )


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


def test_pdf_modality_is_not_text() -> None:
    outcome = _convert(
        b"%PDF-1.7", content_type="application/pdf", profile=_profile(SourceModality.PDF)
    )
    assert outcome.converted is False
    assert "pii_not_text" in outcome.warnings


def test_no_findings_returns_original_as_derived() -> None:
    # 検出 0 件でもマスクを実行した結果として派生物を返す(backend が取込を止めない。#2068)。
    outcome = _convert(b"no pii here", redactor=lambda t: (t, []))
    assert outcome.converted is True
    assert outcome.derived_bytes == b"no pii here"
    assert outcome.warnings == ("pii_no_findings",)


def test_redacts_and_keeps_counts_only() -> None:
    def redactor(text: str) -> tuple[str, list[str]]:
        return text.replace("山田太郎", placeholder("PERSON")), ["pii_redacted:PERSON=1"]

    outcome = _convert("山田太郎 からメール".encode(), redactor=redactor)
    assert outcome.converted is True
    assert outcome.converter_name == "pii_redact"
    assert outcome.derived_bytes is not None
    body = outcome.derived_bytes.decode("utf-8")
    assert body == "［氏名］ からメール"
    # warning は件数のみ(PII の値は含めない)。
    assert outcome.warnings == ("pii_redacted:PERSON=1",)
    assert all("山田" not in w for w in outcome.warnings)


def test_redactor_exception_passthrough() -> None:
    def boom(_t: str) -> tuple[str, list[str]]:
        raise RuntimeError("nlp error")

    outcome = _convert(b"some text", redactor=boom)
    assert outcome.converted is False
    assert "pii_redact_failed" in outcome.warnings


def test_too_large_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    # 上限を超えたら末尾を黙って捨てずに止める(#2068)。
    monkeypatch.setattr(converters, "MAX_CHARS", 10)

    def never(_t: str) -> tuple[str, list[str]]:
        raise AssertionError("redactor must not run")

    outcome = _convert(b"x" * 11, redactor=never)
    assert outcome.converted is False
    assert "pii_too_large" in outcome.warnings


def test_text_modality_routes_without_content_type() -> None:
    outcome = _convert(
        b"raw",
        content_type="",
        redactor=lambda t: (placeholder("EMAIL_ADDRESS"), ["pii_redacted:EMAIL_ADDRESS=1"]),
        profile=_profile(SourceModality.TEXT),
    )
    assert outcome.converted is True
    assert outcome.derived_content_type == "text/plain; charset=utf-8"


def test_html_modality_is_text() -> None:
    outcome = _convert(
        b"<p>raw</p>",
        content_type="text/html",
        redactor=lambda t: (t.replace("raw", placeholder("PERSON")), ["pii_redacted:PERSON=1"]),
        profile=_profile(SourceModality.HTML, content_type="text/html"),
    )
    assert outcome.converted is True
    assert outcome.derived_bytes == "<p>［氏名］</p>".encode()


def test_shift_jis_is_decoded_and_returned_as_utf8() -> None:
    # 短い Shift_JIS の日本語を別の言語の文字コードと取り違えない(#2068)。
    source = "担当者の佐藤一郎です。連絡先は ichiro.sato@example.com です。\n".encode("shift_jis")
    seen: list[str] = []

    def redactor(text: str) -> tuple[str, list[str]]:
        seen.append(text)
        return text.replace("佐藤一郎", placeholder("PERSON")), ["pii_redacted:PERSON=1"]

    outcome = _convert(source, content_type="text/plain; charset=shift_jis", redactor=redactor)
    assert seen == ["担当者の佐藤一郎です。連絡先は ichiro.sato@example.com です。\n"]
    assert outcome.derived_bytes is not None
    assert outcome.derived_bytes.decode("utf-8").startswith("担当者の［氏名］です。")
    assert outcome.derived_content_type == "text/plain; charset=utf-8"


def test_placeholder_is_not_markup() -> None:
    # `<…>` は Markdown / HTML の解析でタグとして捨てられるため使わない(#2068)。
    for entity in (*converters.PLACEHOLDER_LABELS, "UNKNOWN"):
        value = placeholder(entity)
        assert value.startswith("［")
        assert value.endswith("］")
        assert "<" not in value
        assert ">" not in value
    assert placeholder("UNKNOWN") == "［個人情報］"


def test_split_segments_keeps_text_and_line_ends() -> None:
    text = "1 行目\r\n2 行目\n\n" + "あ" * 25_000 + "\n末尾"
    segments = split_segments(text)
    assert "".join(segments) == text
    assert all(len(segment) <= 10_000 for segment in segments)
    assert segments[0] == "1 行目\r\n"


def test_split_segments_prefers_punctuation() -> None:
    text = "あ" * 9_000 + "。" + "い" * 3_000
    segments = split_segments(text)
    assert segments[0] == "あ" * 9_000 + "。"
    assert "".join(segments) == text


class _FakeBatchAnalyzer:
    """行ごとに渡された本文から、固定の語を見つける解析器(日本語モデルの代わり)。"""

    def __init__(self, spans: dict[str, str]) -> None:
        self.spans = spans
        self.texts: list[str] = []

    def analyze_iterator(self, texts, language, batch_size=1, **kwargs):
        results = []
        for text in texts:
            self.texts.append(text)
            found = []
            for word, entity in self.spans.items():
                start = text.find(word)
                if start >= 0:
                    found.append(
                        RecognizerResult(
                            entity_type=entity, start=start, end=start + len(word), score=0.85
                        )
                    )
            results.append(found)
        return results


def _with_fake_engines(monkeypatch: pytest.MonkeyPatch, spans: dict[str, str]):
    from presidio_anonymizer import AnonymizerEngine

    fake = _FakeBatchAnalyzer(spans)
    monkeypatch.setattr(converters, "_engines", lambda: (fake, AnonymizerEngine()))
    return fake


def test_default_redactor_analyzes_each_line_and_keeps_line_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _with_fake_engines(
        monkeypatch,
        {"taro@example.com": "EMAIL_ADDRESS", "03-5555-0123": "PHONE_NUMBER", "##": "PERSON"},
    )
    text = "## 連絡先\n\n- メール: taro@example.com\n- 電話: 03-5555-0123\n| 列 | 値 |\n"
    redacted, warnings = converters._default_redactor(text)
    assert redacted == (
        "## 連絡先\n\n- メール: ［メールアドレス］\n- 電話: ［電話番号］\n| 列 | 値 |\n"
    )
    # 空行は解析に渡さず、各行は改行を含めずに渡す(範囲が改行をまたがない)。
    assert fake.texts == [
        "## 連絡先",
        "- メール: taro@example.com",
        "- 電話: 03-5555-0123",
        "| 列 | 値 |",
    ]
    # 記号だけの範囲(`##`)は誤りとして捨て、件数にも数えない。
    assert warnings == ["pii_redacted:EMAIL_ADDRESS=1", "pii_redacted:PHONE_NUMBER=1"]
    assert all("example.com" not in warning for warning in warnings)


# 日本語モデルは `spacy download` で入れる(形態素解析の sudachipy も同時に入る)。
def test_default_redactor_serializes_shared_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """共有の解析器は thread-safe でないため、解析は lock の中で行う(#2068)。"""
    fake = _with_fake_engines(monkeypatch, {"taro@example.com": "EMAIL_ADDRESS"})
    held: list[bool] = []
    original = fake.analyze_iterator

    def analyze_iterator(texts, language, batch_size=1, **kwargs):
        held.append(converters._ENGINE_LOCK.locked())
        return original(texts, language, batch_size=batch_size, **kwargs)

    monkeypatch.setattr(fake, "analyze_iterator", analyze_iterator)
    converters._default_redactor("メール: taro@example.com\n")
    assert held == [True]


_HAS_JA_MODEL = all(
    importlib.util.find_spec(name) is not None for name in ("ja_core_news_lg", "sudachipy")
)


@pytest.mark.skipif(not _HAS_JA_MODEL, reason="日本語モデル ja_core_news_lg が入っていない環境")
def test_real_engine_masks_contact_and_card_and_keeps_markdown() -> None:
    text = (
        "## 体制\n"
        "- 担当: 山田 太郎\n"
        "- メール: taro.yamada@example.com\n"
        "- 電話: 090-5555-0199\n"
        "- カード番号: 4111 1111 1111 1111\n"
        "\n"
        "| 項目 | 金額 |\n"
        "|---|---|\n"
        "| 部品 | 300,000円 |\n"
    )
    outcome = _convert(text.encode(), content_type="text/markdown")
    assert outcome.derived_bytes is not None
    body = outcome.derived_bytes.decode("utf-8")
    for value in ("山田", "taro.yamada", "5555-0199", "4111 1111 1111 1111"):
        assert value not in body
    assert "［カード番号］" in body
    assert body.count("\n") == text.count("\n")
    assert body.startswith("## 体制\n")
    assert "| 部品 | 300,000円 |" in body
