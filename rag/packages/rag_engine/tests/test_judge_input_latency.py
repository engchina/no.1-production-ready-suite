"""判定の仕事（監査・CRAG の評価）の時間の短縮 (#1528) の検証。

監査の推論の量、CRAG の評価の入力の圧縮、CRAG の評価を曖昧な帯だけで呼ぶ条件、章節の拡張が読む範囲（文書全体）まで
読んだ後の評価し直しの省略を、LLM と DB を置き換えて確かめる。並列でも直列でも通るように、共有の状態を持たない。
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import rag_engine.generation.answering as generation
from rag_engine.config import get_settings
from rag_engine.generation import crag_support
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.models.llm import CragRetrievalGradeOutput
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence

# ---- 監査の推論の量 ---------------------------------------------------------------------------------------


# ---- CRAG の評価の入力の圧縮 ---------------------------------------------------------------------------------

RESERVE = AnswerRecord(
    id="p2",
    chunk_id="local-p2",
    chunk_uid="p2",
    engine="docling",
    engine_label="Docling",
    page=1,
    seq_no=1,
    category="Chunk",
    text=(
        "点検の 5 営業日前までに、総務部にサーバー室の入室の予約をします。点検の 3 営業日前までに、"
        "点検の日時をサーバー室を使う部署に知らせます。"
    ),
    source="点検要領.pdf",
    chunk_level="parent",
    metadata={"section_path": ["サーバー室の定期点検要領", "第 2 章 点検の前の準備"]},
)
REPORT = replace(
    RESERVE,
    id="p12",
    chunk_id="local-p12",
    chunk_uid="p12",
    text="点検の記録は、点検の日から 3 営業日以内に情報システム部長へ提出します。",
    metadata={"section_path": ["サーバー室の定期点検要領", "第 6 章 点検の後の作業"]},
)


def _parent(uid: str, text: str, *, source: str = "規程.pdf", metadata: dict | None = None) -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        chunk_id=f"local-{uid}",
        chunk_uid=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        chunk_level="parent",
        parent_chunk_id="",
        metadata={"section_path": ["規程", "第 2 章"], **(metadata or {})},
    )


def _tree_context(count: int) -> AnswerContext:
    tree = []
    for n in range(count):
        parent = _parent(f"p{n}", f"経費精算の申請の規則 {n}。申請は月末までに行います。")
        child = replace(
            parent,
            id=f"c{n}",
            chunk_id=f"local-c{n}",
            chunk_uid=f"c{n}",
            chunk_level="child",
            parent_chunk_uid=f"p{n}",
            parent_chunk_id=f"local-p{n}",
        )
        tree.append(
            ContextParentEvidence(
                record=parent,
                role="synthesis_parent",
                reason="retrieved",
                children=(ContextChildEvidence(record=child, role="retrieved_anchor", reason="retrieved", retrieval_rank=n + 1),),
            )
        )
    records = [item.record for item in tree]
    return AnswerContext(records=records, text="\n".join(r.text for r in records), evidence_tree=tuple(tree))


def _candidates_json(prompt: str) -> str:
    begin, end = crag_support.UNTRUSTED_CRAG_CANDIDATES_BEGIN, crag_support.UNTRUSTED_CRAG_CANDIDATES_END
    return prompt.split(begin + "\n", 1)[1].split("\n" + end, 1)[0]


def test_compact_prompt_keeps_only_chunk_uid_and_needed_fields_without_spaces():
    context = _tree_context(15)
    question = "経費精算の申請の期限は？"
    full = crag_support._build_crag_grade_prompt(question, question, [question], context)
    compact = crag_support._build_crag_grade_prompt(question, question, [question], context, compact=True, max_candidates=12)

    shown = json.loads(_candidates_json(compact))
    # 生成に渡す親の数（12）にそろえ、ID は chunk_uid だけ（局所 ID・引用の文字列・子の entry を載せない）。
    assert [item["chunk_uid"] for item in shown] == [f"p{n}" for n in range(12)]
    assert all(item["anchor_uids"] == [f"c{n}"] for n, item in enumerate(shown))
    assert set(shown[0]) == {"chunk_uid", "source", "page", "section", "category", "text", "anchor_uids"}
    for removed in ("local-p0", "image_id", "parent_chunk_id", "citation", "context_children", "seq_no"):
        assert removed not in compact
    assert "\n  " not in _candidates_json(compact) and ", " not in _candidates_json(compact).replace("、", "")
    # 従来の入力は変えない（30 件まで・indent=2・全欄）。
    assert len(json.loads(_candidates_json(full))) == 15 and "\n  " in _candidates_json(full)
    assert len(compact) < len(full) // 2


def test_compact_input_keeps_exclusion_and_relevant_candidates():
    question = "申請の締切を教えてください"
    other = _parent("x1", "サーバー室の空調の温度は 25 度以下に保ちます。")
    records = (RESERVE, REPORT, other)
    tree = tuple(ContextParentEvidence(record=item, role="synthesis_parent", reason="retrieved") for item in records)
    context = AnswerContext(records=list(records), text="\n".join(r.text for r in records), evidence_tree=tree)
    output = CragRetrievalGradeOutput(
        candidate_verdicts=[
            {"chunk_uid": "p2", "relevant": True, "reason": "関連"},
            {"chunk_uid": "p12", "relevant": True, "reason": "関連"},
            {"chunk_uid": "x1", "relevant": False, "reason": "話題が別"},
        ],
        aspect_checks=[
            {
                "aspect": "business_object",
                "status": "supported",
                "source_ids": ["p2"],
                "evidence": [{"source_id": "p2", "quote": RESERVE.text}],
                "reason": "関連",
            },
        ],
        reason="足りる",
        sufficient=True,
        confidence=0.9,
        rewritten_query="",
    )
    settings = SimpleNamespace(crag_compact_input=True)
    with patch.object(generation, "parse_text_response", return_value=output) as parse:
        attempt = generation._grade_crag_retrieval(1, question, question, [question], context, settings, max_candidates=12)
    assert "\n  " not in parse.call_args.args[1]
    # 候補の除外（relevant=false）と関連の候補は詰めた入力でも同じ。
    assert attempt.rejected_chunk_ids == ("x1",)
    assert set(attempt.relevant_chunk_ids) == {"p2", "p12"}


# ---- CRAG の評価の候補の短い ID (#1864) -------------------------------------------------------------------------


def test_short_candidate_ids_replace_chunk_uids_in_the_compact_prompt():
    context = _tree_context(15)
    question = "経費精算の申請の期限は？"
    aliases: dict[str, str] = {}
    prompt = crag_support._build_crag_grade_prompt(
        question, question, [question], context, compact=True, max_candidates=12, id_aliases=aliases
    )
    shown = json.loads(_candidates_json(prompt))
    # 候補は並びの順に C1…、検索で当たった子は親の ID に枝番を付ける。短い ID → chunk_uid を返す。
    assert [item["chunk_uid"] for item in shown] == [f"C{n + 1}" for n in range(12)]
    assert all(item["anchor_uids"] == [f"C{n + 1}-1"] for n, item in enumerate(shown))
    assert aliases["C1"] == "p0" and aliases["C1-1"] == "c0" and aliases["C12"] == "p11"
    assert '"p0"' not in _candidates_json(prompt) and '"c0"' not in _candidates_json(prompt)
    # 候補の本文・欄は短い ID のとき以外と同じ（ID だけが違う）。
    plain_prompt = crag_support._build_crag_grade_prompt(question, question, [question], context, compact=True, max_candidates=12)
    plain = json.loads(_candidates_json(plain_prompt))
    assert [{**item, "chunk_uid": "", "anchor_uids": []} for item in shown] == [
        {**item, "chunk_uid": "", "anchor_uids": []} for item in plain
    ]


def test_short_candidate_ids_map_back_to_chunk_uids_in_the_grade():
    question = "申請の締切を教えてください"
    other = _parent("x1", "サーバー室の空調の温度は 25 度以下に保ちます。")
    records = (RESERVE, REPORT, other)
    tree = tuple(ContextParentEvidence(record=item, role="synthesis_parent", reason="retrieved") for item in records)
    context = AnswerContext(records=list(records), text="\n".join(r.text for r in records), evidence_tree=tree)
    # 評価器は短い ID（C1 = RESERVE、C2 = REPORT、C3 = x1）で返す。
    output = CragRetrievalGradeOutput(
        candidate_verdicts=[
            {"chunk_uid": "C1", "relevant": True, "reason": "関連"},
            {"chunk_uid": "C2", "relevant": True, "reason": "関連"},
            {"chunk_uid": "C3", "relevant": False, "reason": "話題が別"},
        ],
        aspect_checks=[
            {
                "aspect": "business_object",
                "status": "supported",
                "source_ids": ["C1"],
                "evidence": [{"source_id": "C1", "quote": RESERVE.text}],
                "reason": "関連",
            },
        ],
        reason="足りる",
        sufficient=True,
        confidence=0.9,
        rewritten_query="",
        recovery_anchor_ids=["C2"],
    )
    settings = SimpleNamespace(crag_compact_input=True, crag_short_candidate_ids=True)
    with patch.object(generation, "parse_text_response", return_value=output) as parse:
        attempt = generation._grade_crag_retrieval(1, question, question, [question], context, settings, max_candidates=12)
    assert f'"chunk_uid":"{REPORT.chunk_uid}"' not in parse.call_args.args[1]
    assert '"chunk_uid":"C2"' in parse.call_args.args[1]
    # 短い ID は chunk_uid に戻るので、候補の除外・関連の候補・観点の根拠は長い ID のときと同じ。
    assert attempt.rejected_chunk_ids == ("x1",)
    assert set(attempt.relevant_chunk_ids) == {"p2", "p12"}
    assert [item["source_ids"] for item in attempt.aspect_checks if item["aspect"] == "business_object"] == [["p2"]]
    assert attempt.recovery_anchor_ids == ("p12",)
    assert [verdict["chunk_uid"] for verdict in attempt.candidate_verdicts] == ["p2", "p12", "x1"]


def test_short_candidate_ids_are_off_by_default_in_the_engine():
    assert get_settings(environ={}, dotenv_path=None).crag_short_candidate_ids is False
    on = get_settings(environ={"RAG_ENGINE_CRAG_SHORT_CANDIDATE_IDS": "1"}, dotenv_path=None)
    assert on.crag_short_candidate_ids is True


# ---- CRAG の評価を曖昧な帯だけで呼ぶ -----------------------------------------------------------------------------

QUESTION = "経費精算の申請の期限と承認者を教えてください"
