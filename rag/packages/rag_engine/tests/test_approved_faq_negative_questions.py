"""Approved FAQ の否定の質問（その FAQ を当てたくない質問の例）の照合と保存（#4788）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_engine.knowledge.approved_faq import (
    MAX_APPROVED_FAQ_NEGATIVE_QUESTIONS,
    ApprovedFaqRecord,
    add_approved_faq_record,
    approved_faq_records_signature,
    build_approved_faq_semantic_index,
    load_approved_faq_records,
    normalize_approved_faq_negative_questions,
    rank_approved_faq_questions,
    suggest_approved_faq_questions,
)

QUESTION = "機器 A の電源を入れる手順は？"
NEGATIVE = "機器 A の電源を切る手順は？"


def _record(**changes: object) -> ApprovedFaqRecord:
    values: dict[str, object] = {
        "id": "faq-1",
        "question": QUESTION,
        "approved_answer": "合成の回答です。",
        "status": "approved",
        "negative_questions": (NEGATIVE,),
    }
    return ApprovedFaqRecord(**{**values, **changes})  # type: ignore[arg-type]


def test_question_equal_to_negative_question_excludes_the_faq() -> None:
    record = _record()
    # 句読点・空白・全角半角の違いだけの質問は、否定の質問と同じ文として扱う。
    ranking = rank_approved_faq_questions("機器Ａの電源を切る手順は", [record], min_score=0.1)

    assert ranking.suggestions == ()
    assert [item.record.id for item in ranking.negative_excluded] == ["faq-1"]
    assert ranking.negative_excluded[0].negative_question == NEGATIVE
    assert ranking.negative_excluded[0].negative_score == 1.0
    assert suggest_approved_faq_questions("機器Ａの電源を切る手順は", [record], min_score=0.1) == []


def test_question_closer_to_faq_question_is_kept() -> None:
    ranking = rank_approved_faq_questions(QUESTION, [_record()], min_score=0.1)

    assert [item.record.id for item in ranking.suggestions] == ["faq-1"]
    assert ranking.suggestions[0].score == 1.0
    # 否定の質問とも字面は近いが、FAQ の質問のほうが近いので外さない。
    assert 0.0 < ranking.suggestions[0].negative_score < 1.0
    assert ranking.negative_excluded == ()


def test_negative_question_below_min_score_does_not_exclude() -> None:
    record = _record(negative_questions=("別の話題の合成の文",))
    ranking = rank_approved_faq_questions("機器 A の電源", [record], min_score=0.1)

    assert [item.record.id for item in ranking.suggestions] == ["faq-1"]
    assert ranking.negative_excluded == ()


def test_only_the_faq_with_the_matching_negative_question_is_excluded() -> None:
    other = _record(id="faq-2", question="機器 A の電源について教えてください。", negative_questions=())
    ranking = rank_approved_faq_questions(NEGATIVE, [_record(), other], min_score=0.1)

    assert [item.record.id for item in ranking.suggestions] == ["faq-2"]
    assert [item.record.id for item in ranking.negative_excluded] == ["faq-1"]


def _embedding(text: str) -> list[float]:
    # 合成の埋め込み: 「止め」「切る」を含む文と、それ以外の文を別の向きに置く。
    return [0.2, 1.0] if ("止め" in text or "切る" in text) else [1.0, 0.2]


def test_semantic_match_to_negative_question_excludes_paraphrase(tmp_path: Path) -> None:
    record = _record()
    index = build_approved_faq_semantic_index(
        tmp_path / "semantic.json",
        [record],
        model="synthetic",
        dimensions=2,
        embedder=lambda texts, settings: [_embedding(text) for text in texts],
        settings=None,
    )
    # 否定の質問も index に入る（FAQ の質問と同じ尺度で比べる）。
    assert sorted(item.question for item in index.items) == sorted([QUESTION, NEGATIVE])

    paraphrase = "機器 A を止めたいときの操作"
    excluded = rank_approved_faq_questions(
        paraphrase,
        [record],
        semantic_index=index,
        semantic_query_embedding=_embedding(paraphrase),
        min_score=0.1,
    )
    kept = rank_approved_faq_questions(
        "機器 A を起動したい",
        [record],
        semantic_index=index,
        semantic_query_embedding=_embedding("機器 A を起動したい"),
        min_score=0.1,
    )

    assert excluded.suggestions == ()
    assert excluded.negative_excluded[0].negative_question == NEGATIVE
    assert excluded.negative_excluded[0].negative_score > 0.99
    assert [item.record.id for item in kept.suggestions] == ["faq-1"]
    assert kept.suggestions[0].match_method == "semantic"


def test_negative_min_score_controls_the_exclusion() -> None:
    record = _record()
    question = "機器 A の電源を切る手順を教えてほしい"
    strict = rank_approved_faq_questions(question, [record], min_score=0.1, negative_min_score=0.99)
    loose = rank_approved_faq_questions(question, [record], min_score=0.1, negative_min_score=0.3)

    assert [item.record.id for item in strict.suggestions] == ["faq-1"]
    assert [item.record.id for item in loose.negative_excluded] == ["faq-1"]


def test_signature_changes_with_negative_questions() -> None:
    assert approved_faq_records_signature([_record()]) != approved_faq_records_signature([_record(negative_questions=())])


def test_normalize_negative_questions_cleans_and_validates() -> None:
    cleaned = normalize_approved_faq_negative_questions(["  機器 A の電源を\n切る手順は？ ", "", NEGATIVE, "機器 B の手順"], [QUESTION])
    # 改行は空白にし、空白の違いだけの文は 1 つにする。
    assert cleaned == ("機器 A の電源を 切る手順は？", "機器 B の手順")

    with pytest.raises(ValueError, match="同じ文"):
        normalize_approved_faq_negative_questions(["機器Ａの電源を入れる手順は"], [QUESTION])
    with pytest.raises(ValueError, match=str(MAX_APPROVED_FAQ_NEGATIVE_QUESTIONS)):
        normalize_approved_faq_negative_questions(
            [f"合成の質問 {index}" for index in range(MAX_APPROVED_FAQ_NEGATIVE_QUESTIONS + 1)], [QUESTION]
        )


def test_add_record_saves_and_loads_negative_questions(tmp_path: Path) -> None:
    path = tmp_path / "faq.json"
    add_approved_faq_record(path, question=QUESTION, approved_answer="合成の回答です。", negative_questions=[NEGATIVE, NEGATIVE])

    (record,) = load_approved_faq_records(path)
    assert record.negative_questions == (NEGATIVE,)

    with pytest.raises(ValueError, match="同じ文"):
        add_approved_faq_record(path, question="別の合成の質問", approved_answer="合成の回答です。", negative_questions=["別の合成の質問"])
