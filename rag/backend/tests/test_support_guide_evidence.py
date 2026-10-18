"""業務ガイドの分からなかった条件を、回答の根拠の画面の図の項目の種類で決める（#2404）。

照合 → rag_engine に渡す条件 → 根拠の観測で決める → 回答の記録の業務ガイドの要約、を
LLM・OCI なしで確かめる。
業務ガイド・項目名は合成の値だけを使う。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from rag_engine.generation import form_field_kinds
from rag_engine.generation.answer_models import AnswerContext

from app.rag.answer_engine import build_engine_settings
from app.rag.support_guide_evidence import (
    EVIDENCE_CONDITIONS_KEY,
    evidence_condition_candidates,
    guide_evidence_conditions,
    with_evidence_conditions,
)
from app.rag.support_guide_runtime import apply_guide_to_diagnostics, match_guide
from app.schemas.support_guide import SupportGuideContent
from tests.test_oci_enterprise_ai import _oci_settings


def field_guide(
    *, source: str = "document", aliases: dict[str, list[str]] | None = None
) -> SupportGuideContent:
    """項目を直す手順が項目の種類で分かれる、合成の業務ガイド。"""
    return SupportGuideContent.model_validate(
        {
            "title": "項目の値を直す",
            "goal": {"expected_result": "項目の値が直る", "match_terms": ["項目の値"]},
            "conditions": [
                {
                    "id": "field_kind",
                    "label": "項目の種類",
                    "allowed_values": ["選択の項目", "入力の項目"],
                    "source": source,
                    "unknown_handling": "branch",
                    "value_aliases": aliases
                    if aliases is not None
                    else {"選択の項目": ["選択"], "入力の項目": ["自由入力"]},
                },
            ],
            "steps": [
                {"id": "code", "title": "コードの登録で選択肢を直す"},
                {"id": "edit", "title": "画面で値を書き換える"},
            ],
            "branches": [
                {
                    "id": "b1",
                    "when": {"condition_id": "field_kind", "values": ["選択の項目"]},
                    "goto_step": "code",
                },
                {
                    "id": "b2",
                    "when": {"condition_id": "field_kind", "values": ["入力の項目"]},
                    "goto_step": "edit",
                },
            ],
        }
    )


QUESTION = "区分コードの項目の値を直したい"


def _match(content: SupportGuideContent | None = None) -> Any:
    match = match_guide([("g-field", 1, content or field_guide())], QUESTION)
    assert match is not None
    return match


def _guide_settings(match: Any) -> dict[str, Any]:
    """search.py が回答の設定に入れる業務ガイドの要約と同じ形。"""
    return {**match.summary(), EVIDENCE_CONDITIONS_KEY: evidence_condition_candidates(match)}


def _context(kind: str) -> AnswerContext:
    record = SimpleNamespace(
        chunk_uid="c1",
        id="c1",
        metadata={
            "image_evidence": [
                {
                    "visual_structure": {
                        "form_fields": [
                            {"name": "区分コード", "value": "", "state": "", "kind": kind}
                        ]
                    }
                }
            ]
        },
    )
    # 照合が読むのは chunk_uid・id・metadata だけなので、AnswerRecord の代わりに軽い値で渡す。
    return AnswerContext(records=[cast(Any, record)], text="")


def _diagnostics(guide: dict[str, Any], kind: str) -> dict[str, Any]:
    """rag_engine の回答の記録（form_field_kinds）と回答の構造。"""
    conditions = guide_evidence_conditions(guide)
    observed = form_field_kinds.question_field_kinds(QUESTION, _context(kind))
    resolved = form_field_kinds.resolve_conditions(observed, conditions)
    return {
        "outcome": "answered",
        "envelope": {"outcome": "answered", "conditions": []},
        "form_field_kinds": form_field_kinds.trace_payload(observed, resolved),
    }


def test_candidates_carry_unknown_enum_conditions_with_aliases() -> None:
    match = _match()
    assert match.decision == "branch"
    candidates = evidence_condition_candidates(match)
    assert candidates == [
        {
            "id": "field_kind",
            "label": "項目の種類",
            "options": [
                {"value": "選択の項目", "aliases": ["選択"]},
                {"value": "入力の項目", "aliases": ["自由入力"]},
            ],
        }
    ]
    assert guide_evidence_conditions({EVIDENCE_CONDITIONS_KEY: candidates}) == (
        ("field_kind", "項目の種類", (("選択の項目", ("選択",)), ("入力の項目", ("自由入力",)))),
    )
    assert guide_evidence_conditions(None) == ()
    assert guide_evidence_conditions({EVIDENCE_CONDITIONS_KEY: "x"}) == ()


def test_known_condition_is_not_a_candidate() -> None:
    # 質問に選択肢の語が出て分かっている条件は、根拠で決め直さない。
    match = match_guide([("g-field", 1, field_guide(source="user"))], QUESTION + "（選択の項目）")
    assert match is not None
    assert evidence_condition_candidates(match) == []


def test_engine_settings_carry_evidence_conditions(tmp_path: Path) -> None:
    settings = _oci_settings()
    assert build_engine_settings(settings, output_dir=tmp_path).evidence_conditions == ()
    settings.rag_support_guide = _guide_settings(_match())
    assert build_engine_settings(settings, output_dir=tmp_path).evidence_conditions == (
        ("field_kind", "項目の種類", (("選択の項目", ("選択",)), ("入力の項目", ("自由入力",)))),
    )


def test_field_kind_in_evidence_makes_condition_known() -> None:
    guide = _guide_settings(_match())
    diagnostics = _diagnostics(guide, "select")
    resolved_guide = with_evidence_conditions(guide, diagnostics)
    apply_guide_to_diagnostics(diagnostics, resolved_guide)
    summary = diagnostics["guide"]
    assert summary["decision"] == "answer"
    assert summary["unknown_conditions"] == []
    assert summary["known_conditions"] == [
        {
            "id": "field_kind",
            "label": "項目の種類",
            "value": "選択の項目",
            "source": "document",
            "state": "known",
        }
    ]
    # 分岐の条件が決まったので、回答は条件付きにしない。
    assert diagnostics["envelope"]["outcome"] == "answered"
    assert _diagnostics(guide, "text")["form_field_kinds"]["resolved_conditions"][0]["value"] == (
        "入力の項目"
    )


def test_unknown_field_kind_keeps_branch() -> None:
    guide = _guide_settings(_match())
    diagnostics = _diagnostics(guide, "unknown")
    assert diagnostics["form_field_kinds"]["resolved_conditions"] == []
    resolved_guide = with_evidence_conditions(guide, diagnostics)
    apply_guide_to_diagnostics(diagnostics, resolved_guide)
    assert diagnostics["guide"]["decision"] == "branch"
    assert diagnostics["envelope"]["outcome"] == "conditional"
    assert diagnostics["envelope"]["conditions"] == ["項目の種類"]


def test_condition_without_kind_options_is_not_resolved() -> None:
    # 選択肢が項目の種類の語に当たらない条件（言い換えも無い）は、根拠の種類で決めない。
    guide = _guide_settings(_match(field_guide(aliases={})))
    diagnostics = _diagnostics(guide, "select")
    assert diagnostics["form_field_kinds"]["resolved_conditions"] == []
    assert with_evidence_conditions(guide, diagnostics)["decision"] == "branch"
