"""回答スタイル公開前契約の決定論テスト。"""

import pytest

from app.rag.generation_contract import (
    NO_RELEVANT_EVIDENCE_ANSWER,
    GenerationContractViolation,
    validate_generation_contract,
)

ALLOWED = {"policy.pdf#chunk-1", "guide.md#chunk-2"}
CONTEXT = """[policy.pdf#chunk-1]
申請期限は7月31日です。

---

[guide.md#chunk-2]
承認者は部門長です。"""


@pytest.mark.parametrize(
    ("profile", "answer"),
    [
        ("grounded_concise", "申請期限は7月31日です。"),
        ("detailed_cited", "申請期限は7月31日です。[policy.pdf#chunk-1]"),
        ("strict_extractive", "申請期限は7月31日です。"),
        (
            "structured_json",
            '{"answer":"申請期限は7月31日です。","evidence":["期限"],'
            '"sources":["policy.pdf#chunk-1"]}',
        ),
        (
            "bilingual_ja_en",
            "申請期限は7月31日です。\nEnglish summary: The deadline is July 31.",
        ),
        ("inline_cited", "申請期限は7月31日です。[policy.pdf#chunk-1]"),
        ("custom", "指定された独自形式の回答"),
    ],
)
def test_generation_profiles_accept_valid_answers(profile: str, answer: str) -> None:
    assert validate_generation_contract(
        profile=profile,  # type: ignore[arg-type]
        answer=answer,
        context=CONTEXT,
        allowed_source_ids=ALLOWED,
    )


@pytest.mark.parametrize(
    ("profile", "answer", "code"),
    [
        ("detailed_cited", "引用なしの段落です。", "missing_paragraph_citation"),
        (
            "detailed_cited",
            "偽の引用です。[unknown.pdf#chunk-x]",
            "unknown_citation",
        ),
        ("strict_extractive", "申請期限は8月31日です。", "extractive_sentence_not_in_context"),
        (
            "structured_json",
            '{"answer":"回答","evidence":[],"sources":["unknown#chunk-x"]}',
            "unknown_citation",
        ),
        ("bilingual_ja_en", "日本語だけです。", "missing_english_summary"),
        ("inline_cited", "申請期限は7月31日です。", "missing_inline_citation"),
    ],
)
def test_generation_profiles_reject_contract_violations(
    profile: str,
    answer: str,
    code: str,
) -> None:
    with pytest.raises(GenerationContractViolation) as captured:
        validate_generation_contract(
            profile=profile,  # type: ignore[arg-type]
            answer=answer,
            context=CONTEXT,
            allowed_source_ids=ALLOWED,
        )
    assert code in captured.value.codes


def test_strict_extractive_accepts_fixed_no_evidence_answer() -> None:
    assert (
        validate_generation_contract(
            profile="strict_extractive",
            answer=NO_RELEVANT_EVIDENCE_ANSWER,
            context="",
            allowed_source_ids=set(),
        )
        == NO_RELEVANT_EVIDENCE_ANSWER
    )


@pytest.mark.parametrize(
    ("profile", "answer", "code"),
    [
        ("strict_extractive", "[policy.pdf#chunk-1]", "extractive_sentence_empty"),
        ("inline_cited", "[policy.pdf#chunk-1]", "inline_sentence_empty"),
        (
            "bilingual_ja_en",
            "申請期限は7月31日です。\nEnglish summary: 申請期限は7月31日です。",
            "english_summary_not_english",
        ),
    ],
)
def test_generation_profiles_reject_contentless_format_shells(
    profile: str,
    answer: str,
    code: str,
) -> None:
    with pytest.raises(GenerationContractViolation) as captured:
        validate_generation_contract(
            profile=profile,  # type: ignore[arg-type]
            answer=answer,
            context=CONTEXT,
            allowed_source_ids=ALLOWED,
        )

    assert code in captured.value.codes
