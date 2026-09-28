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


# ファイル名に空白・括弧・読点を含む出典(#276)。
SPACED_ALLOWED = {"就業規則 (2024年版).pdf#3f2a9c", "経費精算,FAQ.docx#c-1"}
SPACED_CONTEXT = """[Evidence 1 | mid | optional | 就業規則 (2024年版).pdf#3f2a9c]
申請期限は7月31日です。

---

[Evidence 2 | mid | optional | 経費精算,FAQ.docx#c-1]
承認者は部門長です。"""


@pytest.mark.parametrize(
    ("profile", "answer"),
    [
        (
            "detailed_cited",
            "申請期限は7月31日です。[Evidence 1 | 就業規則 (2024年版).pdf#3f2a9c]\n\n"
            "承認者は部門長です。[Evidence 2 | 経費精算,FAQ.docx#c-1]",
        ),
        ("inline_cited", "申請期限は7月31日です。[就業規則 (2024年版).pdf#3f2a9c]"),
        ("inline_cited", "承認者は部門長です。[経費精算,FAQ.docx#c-1]"),
        ("strict_extractive", "申請期限は7月31日です。[就業規則 (2024年版).pdf#3f2a9c]"),
    ],
)
def test_source_ids_with_spaces_and_brackets_are_accepted(profile: str, answer: str) -> None:
    """空白・括弧・読点を含むファイル名の出典も許可 ID として照合する。"""
    assert validate_generation_contract(
        profile=profile,  # type: ignore[arg-type]
        answer=answer,
        context=SPACED_CONTEXT,
        allowed_source_ids=SPACED_ALLOWED,
    )


@pytest.mark.parametrize(
    ("profile", "answer"),
    [
        # 許可 ID を末尾に含むだけの別ファイル名。
        ("inline_cited", "申請期限は7月31日です。[旧就業規則 (2024年版).pdf#3f2a9c]"),
        # 許可 ID の chunk_id を前方一致で含むだけの別 chunk。
        ("inline_cited", "申請期限は7月31日です。[就業規則 (2024年版).pdf#3f2a9c0]"),
        ("detailed_cited", "申請期限は7月31日です。[Evidence 9 | other.pdf#x-1]"),
    ],
)
def test_lookalike_source_ids_are_still_rejected(profile: str, answer: str) -> None:
    with pytest.raises(GenerationContractViolation) as captured:
        validate_generation_contract(
            profile=profile,  # type: ignore[arg-type]
            answer=answer,
            context=SPACED_CONTEXT,
            allowed_source_ids=SPACED_ALLOWED,
        )
    assert "unknown_citation" in captured.value.codes


def test_detailed_cited_ignores_heading_only_paragraphs() -> None:
    """見出しだけの段落には出典を要求しない。本文の段落は引き続き出典が必要。"""
    answer = (
        "## 申請期限\n\n申請期限は7月31日です。[Evidence 1 | policy.pdf#chunk-1]\n\n"
        "### 承認\n\n承認者は部門長です。[Evidence 2 | guide.md#chunk-2]"
    )
    assert validate_generation_contract(
        profile="detailed_cited",
        answer=answer,
        context=CONTEXT,
        allowed_source_ids=ALLOWED,
    )
    with pytest.raises(GenerationContractViolation) as captured:
        validate_generation_contract(
            profile="detailed_cited",
            answer="## 申請期限\n\n申請期限は7月31日です。",
            context=CONTEXT,
            allowed_source_ids=ALLOWED,
        )
    assert "missing_paragraph_citation" in captured.value.codes
