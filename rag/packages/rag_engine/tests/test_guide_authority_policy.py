"""ガイドの業務範囲と資料の適用性を分離する採用契約。意味判定は合成oracle。"""

import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from audit_fixtures import legacy_audit_to_wire_fixture
from citation_unit_support import canonical_fixture
from openai.lib._parsing._responses import type_to_text_format_param

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering, grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.generation.confirmed_context import confirmed_premise_records
from rag_engine.generation.guide_authority import authoritative_guide_inputs
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedDraft, GroundedItem, GroundedItemReview, GroundedRequestReview

QUESTION = "対象の設定を複製する手順は？"
CORRECT = "対象を選び、複製ボタンを押してから参照元を選択します。"
REVERSED = "対象を選び、参照元を選択してから複製ボタンを押します。"
SWAPPED = "参照元を選び、複製ボタンを押してから設定対象を選択します。"
RELATED = "画面の状態と一覧の件数を確認できます。"
SCOPE: list[dict[str, Any]] = [
    {"step_id": "copy", "required": True, "depends_on": []},
    {"step_id": "finish", "required": True, "depends_on": ["copy"]},
    {"step_id": "alternative", "required": False, "depends_on": []},
]
CANDIDATES: dict[int, list[dict[str, Any]]] = {
    0: [
        {
            "step_id": "copy",
            "association": "source_only",
            "matched_evidence_ids": ["stable-a"],
            "candidate_requirements": [{"id": "copy:0", "text": "対象、複製操作、参照元を順に説明する"}],
        }
    ],
}


def assessment(*, support: str = "supported", source: str | None = "matched", source_condition: str = "") -> GroundedAudit:
    return GroundedAudit.model_validate(
        {
            "reviews": [
                {
                    "index": 0,
                    "support": support,
                    "applicability": "not_applicable",
                    "reason": "合成の範囲観察",
                    "source_applicability": source,
                    "source_condition": source_condition,
                }
            ],
            "summary_supported": False,
            "goal_alignment": "partial",
        }
    )


def checked(text: str = CORRECT, *, evidence: str = CORRECT) -> grounded.CheckedItem:
    span = {
        "evidence_id": "stable-a",
        "label": "E1",
        "source_id": "child-a",
        "source": "synthetic.pdf",
        "text": evidence,
        "function": ("synthetic.pdf", "合成操作"),
        "tags": [],
    }
    proposal = GroundedItem(
        kind="operation",
        text=text,
        request_id="Q1",
        applies="matched",
        citations=[GroundedCitation(evidence_id="stable-a", quote=evidence)],
    )
    entries, dropped = grounded.verify(QUESTION, GroundedDraft(items=[proposal], summary="", confidence="low"), [span])
    assert not dropped and len(entries) == 1
    return entries[0]


def apply(entry: grounded.CheckedItem, raw: GroundedAudit) -> GroundedAudit:
    result = grounded.applicability_policy(bind_guide_authority(raw, SCOPE, CANDIDATES))
    grounded.apply_audit([entry], result)
    return result


def test_authoritative_inputs_keep_required_steps_without_material_and_override_legacy_business_plan() -> None:
    scope = [
        {
            "step_id": "copy",
            "title": "コードが選択した操作",
            "required": True,
            "depends_on": [],
            "requirements": [{"id": "copy:0", "text": "対象と順序の説明", "kind": "completion"}],
        },
        {
            "step_id": "missing",
            "title": "資料未取得の必須操作",
            "required": True,
            "depends_on": ["copy"],
            "requirements": [{"id": "missing:0", "text": "終了の説明", "kind": "completion"}],
        },
        {"step_id": "unresolved", "title": "未確定の分岐", "required": False, "depends_on": [], "requirements": []},
    ]
    previous_steps = [("outside", "旧計画の対象外操作", ["unrelated-source"]), ("copy", "旧見出し", ["actual-source"])]
    previous_contracts = [
        {"step_id": "outside", "requirements": [{"id": "outside:0", "text": "対象外"}]},
        {"step_id": "copy", "depends_on": ["outside"], "requirements": [{"id": "obsolete", "text": "旧計画の内容"}]},
    ]
    before = deepcopy((scope, previous_steps, previous_contracts))
    steps, optional, contracts = authoritative_guide_inputs(scope, previous_steps, previous_contracts)
    assert steps == (
        ("copy", "コードが選択した操作", ["actual-source"]),
        ("missing", "資料未取得の必須操作", ()),
        ("unresolved", "未確定の分岐", ()),
    )
    assert optional == ("unresolved",)
    assert [row["step_id"] for row in contracts] == ["copy", "missing", "unresolved"]
    assert contracts[0]["requirements"] == scope[0]["requirements"] and contracts[0]["depends_on"] == []
    assert contracts[1]["requirements"] == scope[1]["requirements"] and contracts[1]["depends_on"] == ["copy"]
    assert all(not {"evidence_ids", "source_ids"} & row.keys() for row in contracts)
    assert {identity for _step, _title, sources in steps for identity in sources} == {"actual-source"}
    assert before == (scope, previous_steps, previous_contracts)

