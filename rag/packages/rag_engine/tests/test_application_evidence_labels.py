"""推導の文に混ざった、送った根拠の表示 ID を出典の名前に置き換える（#4626）。

実環境の記録（合成の資料・合成の質問）で、モデルが推導の方法に書いた表示 ID の形をそのまま使う。送った根拠に
対応する ID だけを置き換え、対応の無い ID は内部の番号の漏れとして従来どおり推導を外す。LLM は stub だけ。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_row_conditions_answering import TABLE_TEXT, answer

from rag_engine.generation import grounded
from rag_engine.generation.applications import check_applications
from rag_engine.generation.citation_locator import EVIDENCE_LABEL, replace_evidence_labels
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.models.llm import GroundedApplication, GroundedDraft

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "row_conditions_recorded.json").read_text(encoding="utf-8"))

# 実環境の記録の推導の方法（2 件 × 1 回目と是正の 2 回目）。E2 = 表、E3 = 区分の規則（どちらも送った根拠）。
RECORDED_REASONING = [
    "属性A〜Hが有であるため、E2 の表にある A1〜A8 の点数を合計する。点数はそれぞれ 2,3,1,4,2,1,3,2 点であり、合計は 18 点になる。"
    "属性I が無なので A9 の 5 点は加算しない。E3 の区分規則に従い、合計点 18 は 10〜19 点の範囲に該当し、区分は B である。",
    "E2 の表に示された属性A〜H が有である各行の点数を合計すると、2 + 3 + 1 + 4 + 2 + 1 + 3 + 2 = 18 点になる。属性I が無であるため、"
    "行 A9 の 5 点は加算しない。E3 の区分規則によれば、合計点 10〜19 点は区分 B に該当する。",
    "属性A〜Hの各行の点数を合計すると、2+3+1+4+2+1+3+2 = 18点となります。区分の規則（E3）によれば、10〜19点は区分Bに該当します。",
    "属性A〜Hが有であることから、表E2の各行の点数を合計すると 2+3+1+4+2+1+3+2 = 18 点となります。",
    "区分の規則(E3)によれば、合計点が10〜19点の範囲にある場合は区分Bに該当します。",
]


def sent_catalog() -> SentUnitCatalog:
    """送った根拠 2 件（表と区分の規則）の目録。表示 ID は E2・E3。"""
    table = {**FIXTURE["catalog_snapshots"][0]["span"], "label": "E2", "source": "synthetic-table.md", "page": 1}
    rule = {**FIXTURE["catalog_snapshots"][1]["span"], "label": "E3", "source": "synthetic-rule.md", "page": 2}
    return SentUnitCatalog([EvidenceUnitSnapshot.create(span, [(0, len(span["text"]))], "explicit_units_v5") for span in (table, rule)])


@pytest.mark.parametrize("reasoning", RECORDED_REASONING, ids=[f"recorded-{n}" for n in range(len(RECORDED_REASONING))])
def test_recorded_reasoning_ids_become_source_names(reasoning: str) -> None:
    sources = grounded.sent_evidence_sources(sent_catalog())
    replaced = replace_evidence_labels(reasoning, sources, codes=set())
    assert not EVIDENCE_LABEL.search(replaced)
    for label, name in sources.items():
        assert (label in reasoning) == (name in replaced)


def test_bracketed_label_keeps_the_bracket() -> None:
    sources = {"E3": "synthetic-rule.md p.2"}
    assert replace_evidence_labels("区分の規則（E3）によれば", sources, codes=set()) == "区分の規則（synthetic-rule.md p.2）によれば"
    assert replace_evidence_labels("表E3の行", sources, codes=set()) == "表「synthetic-rule.md p.2」の行"


def test_unsent_ids_and_document_codes_are_kept() -> None:
    sources = {"E2": "synthetic-table.md p.1"}
    # 送っていない根拠の ID は残す（漏れとして扱う）。
    assert replace_evidence_labels("E9 の表を当てはめる。", sources, codes=set()) == "E9 の表を当てはめる。"
    # 資料の本文にある同じ字のコードは資料の記載なので残す。
    assert replace_evidence_labels("コード E2 の行", sources, codes={"E2"}) == "コード E2 の行"


def _items() -> dict[int, Any]:
    span = {"evidence_id": "E-table", "text": TABLE_TEXT, "label": "E2"}
    entry = SimpleNamespace(
        item=SimpleNamespace(kind="rule", applies="matched", text="t", quote="t", condition=""),
        span=span,
        quote_only=False,
        removed=False,
        state="",
    )
    return {0: entry}


def _draft(reasoning: str) -> GroundedDraft:
    return GroundedDraft(
        items=[],
        summary="",
        confidence="low",
        applications=[
            GroundedApplication(request_id="Q1", item_indexes=[0], reasoning=reasoning, result="合計点は 18 点、区分は B です。")
        ],
    )


def test_replaced_derivation_passes_the_internal_label_check() -> None:
    draft = grounded.with_application_sources(_draft(RECORDED_REASONING[0]), sent_catalog())
    [entry] = check_applications("属性A〜Hは有です。属性Iは無です。", draft.applications, _items())
    assert entry.internal_label_issues == [] and not any("内部の番号" in reason for reason in entry.reasons)
    assert "synthetic-table.md p.1" in entry.application.reasoning and "synthetic-rule.md p.2" in entry.application.reasoning


def test_unsent_id_is_still_a_leak() -> None:
    draft = grounded.with_application_sources(_draft("E9 の表の点数を合計する。"), sent_catalog())
    [entry] = check_applications("属性A〜Hは有です。属性Iは無です。", draft.applications, _items())
    assert entry.internal_label_issues == ["E9"] and not entry.published


def test_draft_without_ids_is_unchanged() -> None:
    draft = _draft("表の点数を合計する。")
    assert grounded.with_application_sources(draft, sent_catalog()) is draft


# ---------------------------------------------------------------- 回答の経路（stub）


def _derivation_reasoning(reasoning: str) -> Any:
    def generated(eid: str) -> dict[str, Any]:
        quote = TABLE_TEXT.splitlines()[2]
        value = {
            "summary": "",
            "summary_item_indexes": [],
            "items": [{"kind": "rule", "request_id": "Q1", "text": quote, "citations": [{"evidence_id": eid, "quote": quote}]}],
            "confidence": "high",
        }
        value["applications"] = [
            {
                "request_id": "Q1",
                "premises": ["前営業日に行った"],
                "item_indexes": [0],
                "reasoning": reasoning,
                "result": "割合は 50% です。",
            }
        ]
        return value

    return generated


def test_answer_with_a_sent_label_in_the_derivation_needs_no_correction() -> None:
    result, model = answer(_derivation_reasoning("E1 の表の行を当てはめる。"))
    trace = result.generation_trace
    assert [row["accepted"] for row in trace["rounds"]] == [True]
    [application] = trace["applications"]
    assert not EVIDENCE_LABEL.search(application["reasoning"]) and "synthetic-period.md" in application["reasoning"]
    assert not any("内部の番号" in reason for reason in application["reasons"])
    assert "E1" not in result.answer_text


def test_answer_with_an_unsent_label_still_corrects() -> None:
    result, _model = answer(_derivation_reasoning("E9 の表の行を当てはめる。"))
    rounds = result.generation_trace["rounds"]
    assert len(rounds) == 2
    assert any("内部の番号『E9』" in reason for application in result.generation_trace["applications"] for reason in application["reasons"])
