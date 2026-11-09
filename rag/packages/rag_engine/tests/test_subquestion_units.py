"""丸数字・(1) などで並べた小問の要求単位と、小問ごとの根拠の確保・回答の見出し（#2399）。外部 I/O と LLM は使わない。"""

from __future__ import annotations

import re

import pytest
from rag_parser_core.language import bind_pack, builtin_pack, resolve

from rag_engine.generation import grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.models.llm import GroundedCitation, GroundedItem
from rag_engine.retrieval.context_builder import ContextParentEvidence
from rag_engine.retrieval.request_coverage import (
    TargetedRetrieval,
    select_new_parents,
)
from rag_engine.retrieval.task_contract import request_units, subquestion_markers

PREAMBLE = "システムAの画面Bについて教えてください。"
LINE_QUESTION = PREAMBLE + "\n①受注の登録方法は？\n②請求書の再発行方法は？\n③承認の担当は誰ですか？"


# ---- 小問の分割 ----------------------------------------------------------------------------------


def test_line_start_subquestions_become_request_units_with_preamble() -> None:
    units = request_units(LINE_QUESTION)

    assert [(u["id"], u["subquestion"], u["text"]) for u in units] == [
        ("Q1", "①", "①受注の登録方法は？"),
        ("Q2", "②", "②請求書の再発行方法は？"),
        ("Q3", "③", "③承認の担当は誰ですか？"),
    ]
    # 小問の前の共通の前置きは各小問に付く。
    assert all(u["preamble"] == PREAMBLE for u in units)


def test_subquestions_written_in_one_sentence_are_split() -> None:
    units = request_units("画面Bの件で①受注の登録方法②請求書の再発行方法を教えてください")

    assert [u["text"] for u in units] == ["①受注の登録方法", "②請求書の再発行方法を教えてください"]
    assert all(u["preamble"] == "画面Bの件で" for u in units)


@pytest.mark.parametrize(
    ("question", "labels"),
    [
        ("次の点を教えてください。(1)受注の登録方法 (2)請求書の再発行方法", ["(1)", "(2)"]),
        ("次の点を教えてください。（１）受注の登録方法（２）請求書の再発行方法", ["（１）", "（２）"]),
        ("1) 受注の登録方法\n2) 請求書の再発行方法", ["1)", "2)"]),
        ("問1 受注の登録方法は？ 問2 請求書の再発行方法は？", ["問1", "問2"]),
    ],
    ids=["paren", "fullwidth-paren", "number-paren-line", "question-number"],
)
def test_numbered_subquestion_forms(question: str, labels: list[str]) -> None:
    assert [label for _, label in subquestion_markers(question)] == labels
    assert [u["subquestion"] for u in request_units(question) if u.get("subquestion")] == labels


@pytest.mark.parametrize(
    "question",
    [
        "①と②の違いは何ですか？",
        "手順の③を教えてください。",
        "③の欄には何を入力しますか？",
        "①～③の手順のあとに何をしますか？",
        "②受注の登録方法は？",  # 1 から始まらない
        "①受注の登録方法は？③請求書の再発行方法は？",  # 続かない
        "受注番号10)を入力した後の確認は？2)の画面は？",  # 数で始まる型は区切りの後だけ
    ],
    ids=["compare-steps", "single-step", "field-number", "range", "not-from-one", "gap", "no-boundary"],
)
def test_step_number_references_are_not_subquestions(question: str) -> None:
    assert subquestion_markers(question) == []
    assert not any(u.get("subquestion") for u in request_units(question))


@pytest.mark.parametrize(
    "question",
    [
        "受注の登録方法は？請求書の再発行方法は？",
        "先月から担当が変わった。受注の登録方法を教えてください。",
        "業務と種別を追加したい。",
        "取消済み伝票を変更して再発行したい。方法と影響を教えてください。",
        "画面の①の欄の意味は？",
        "①から③の手順で登録しましたがエラーになります。\n原因を教えてください。",
    ],
    ids=["two-questions", "background", "parallel", "chained-facets", "field-reference", "step-range"],
)
def test_questions_without_subquestion_markers_keep_the_same_units(question: str) -> None:
    units = request_units(question)

    # 小問の印の無い質問は、原文全体を 1 つの単位にする。
    assert units == [{"id": "Q1", "text": question}]


def test_subquestion_markers_come_from_the_language_pack_not_step_markers() -> None:
    # 資料の手順の印（markers.circled_numbers）を変えても、小問の印は変わらない。
    with bind_pack(resolve(builtin_pack(), {"markers": {"circled_numbers": ["⑴-⒇"]}})):
        assert [label for _, label in subquestion_markers(LINE_QUESTION)] == ["①", "②", "③"]
    # 小問の印から丸数字を外せば、丸数字の質問は小問に分けない。
    with bind_pack(resolve(builtin_pack(), {"question_cues": {"subquestion_circled_numbers": ["⑴-⒇"]}})):
        assert subquestion_markers(LINE_QUESTION) == []
    # 言及の語は言語資源。「①は」を言及にしなければ「①は…②は…」を小問にする。
    question = "①は受注の登録方法、②は請求書の再発行方法を教えて"
    assert subquestion_markers(question) == []
    with bind_pack(resolve(builtin_pack(), {"question_cues": {"subquestion_reference_followers": ["の"]}})):
        assert [label for _, label in subquestion_markers(question)] == ["①", "②"]


# ---- 小問ごとの根拠の確保 ----------------------------------------------------------------------

_VOCABULARY = re.compile("受注|登録|請求書|再発行|承認|担当|システムA|画面B")


def split(text: str) -> list[str]:
    return _VOCABULARY.findall(text)


def record(uid: str, text: str) -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="manual.pdf",
        source_run_id="source",
        chunk_id=uid,
        chunk_uid=f"run:{uid}",
        chunk_level="parent",
    )


def parent(uid: str, text: str) -> ContextParentEvidence:
    return ContextParentEvidence(record=record(uid, text), role="synthesis_parent", reason="top")


def context(*parents: ContextParentEvidence) -> AnswerContext:
    return AnswerContext(records=[p.record for p in parents], text="x", evidence_tree=tuple(parents))


def test_select_new_parents_keeps_shared_cap_for_missing_requests() -> None:
    sub = TargetedRetrieval("Q2", ("a",), tuple(parent(f"a{n}", "x") for n in range(3)), chunk_cap=1)
    missing = TargetedRetrieval("Q5", ("b",), tuple(parent(f"b{n}", "x") for n in range(3)))

    selected = select_new_parents([sub, missing], set(), max_chunks=2)

    assert [(rid, p.record.chunk_uid) for rid, p in selected] == [("Q2", "run:a0"), ("Q5", "run:b0"), ("Q5", "run:b1")]


# ---- 回答の見出し ---------------------------------------------------------------------------------


def _entry(request_id: str, text: str, page: int) -> grounded.CheckedItem:
    span = {"evidence_id": f"E{page}", "source": "manual.pdf", "page": page, "function": ("doc", "f"), "document_scope": ["doc"]}
    return grounded.CheckedItem(
        GroundedItem(
            kind="rule", text=text, citations=[GroundedCitation(evidence_id="", quote=text)], request_id=request_id, applies="matched"
        ),
        span=span,
    )
