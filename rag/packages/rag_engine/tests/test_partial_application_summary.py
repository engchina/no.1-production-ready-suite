"""不合格の摘要は残さず、公開済みの選択結果だけを原依存から回復する。"""

from dataclasses import replace
from unittest.mock import patch

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply
from audit_fixtures import legacy_audit_result, synthetic_explanation

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, AnswerRecord, synthesize_grounded_answer
from rag_engine.models.audit_wire import is_audit_wire

FIRST = "対象Aの値は28です。"
SECOND = "対象Bの値は12です。"
# 導出の公開門を確かめるため、原文の写しではない結果にする（写しは直接の記載になる）。
GOOD = "対象Aは28です。"
BAD = "対象Bの値は99です。"
KEPT = "対象Bは12です。"
QUESTION = "対象Aと対象Bの値をそれぞれ教えてください。"


def fixture(mode="detailed", decisions=(True, False), *, summary_supported=False, second=BAD):
    records = [
        AnswerRecord(
            id=f"synthetic-{i}",
            engine="docling",
            engine_label="Docling",
            page=1,
            seq_no=i + 1,
            category="Chunk",
            text=text,
            source="synthetic.txt",
            chunk_level="parent",
            chunk_id=f"synthetic-{i}",
            source_run_id="synthetic-run",
            metadata={"section_path": ["合成節"]},
        )
        for i, text in enumerate((FIRST, SECOND))
    ]
    context = AnswerContext(records=records, text="\n".join((FIRST, SECOND)))
    calls, rounds = [], []
    original = grounded.run_round

    def capture_round(*args, **kwargs):
        value = original(*args, **kwargs)
        rounds.append(value)
        return value

    def fixed(_system, _prompt, _settings, schema, **_kwargs):
        calls.append(schema.canonical_schema.__name__ if is_audit_wire(schema) else schema.__name__)
        if schema.__name__ == "ApplicationExplanation":
            return synthetic_explanation(schema, _prompt)
        if schema.__name__ == "GroundedDraftWire":
            spans = [span for span, _view in (grounded._CITATION_VIEWS.get() or {}).values()]
            catalog = grounded.generation_unit_catalog(spans, sent_only=True)
            units = {snapshot.span()["text"]: list(snapshot.unit_ids) for snapshot in catalog.snapshots}
            return schema.model_validate(
                {
                    "answer_parts": [
                        {
                            "request_id": "Q1",
                            "requirement": f"対象{label}の値",
                            "answer": {
                                "kind": "answer",
                                "basis": {"unit_ids": units[text]},
                                "reasoning": f"対象{label}の原文記載を使用する。",
                                "result": result,
                            },
                        }
                        for label, text, result in (("A", FIRST, GOOD), ("B", SECOND, second))
                    ],
                    "request_coverage": [
                        {
                            "request_id": "Q1",
                            "evidence_scope": [],
                        }
                    ],
                    "summary_part_indexes": [0, 1],
                    "confidence": "medium",
                }
            )
        reviews = [{"index": i, "valid": valid, "reason": "固定の支持判定"} for i, valid in enumerate(decisions)]
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, reviews, summary_supported=summary_supported, prompt=_prompt)
        value = {
            "goal_alignment": "aligned",
            "summary_supported": summary_supported,
            "reviews": [
                {"index": i, "support": "supported", "applicability": "matched", "condition": "", "reason": "固定観測"} for i in range(2)
            ],
            "request_reviews": [{"request_id": "Q1", "status": "addressed", "reason": "固定観測"}],
            "application_reviews": reviews,
        }
        return legacy_audit_result(value, schema)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("実API/DBを使わない合成回帰")

    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode=mode)
    with (
        patch.object(grounded, "run_round", capture_round),
        bind_dependencies(AnswerDependencies(forbidden, forbidden, fixed, forbidden, forbidden)),
    ):
        response = synthesize_grounded_answer(QUESTION, context, settings, image_prompt_mode="text_only").response
    return response, rounds[0], calls


@pytest.mark.parametrize("mode", ["detailed", "none"])
def test_valid_selected_application_survives_summary_with_its_citation(mode):
    response, current, calls = fixture(mode)
    assert response.answer_text.startswith(GOOD)
    assert BAD not in response.answer_text
    assert current.draft.summary == GOOD + "\n" + BAD
    assert current.draft.summary_application_indexes == [0, 1]
    summary = response.generation_trace["finalization"]["summary"]
    assert summary["citations"]["status"] == "linked"
    assert [link["application_index"] for link in summary["citations"]["links"]] == [0]
    # 監査なしの設定では、独立の監査の判定 1 回に、判定が確定した推導ごとの理由が付く。
    # どちらの設定でも、valid な推導 1 件の説明の段（#4393）を 1 回呼ぶ。
    assert len(calls) == (5 if mode == "none" else 3) and calls.count("ApplicationExplanation") == 1


def withheld_round():
    # run_round の元状態を使う。完成回答での複写とは別に、後段の変更を試す。
    _, current, _ = fixture()
    assert current.summary == grounded.answer_labels().neutral_summary
    return current


@pytest.mark.parametrize(
    "mutation",
    ["missing_indexes", "legacy", "item_dependency", "joint_part", "unknown", "no_binding", "changed_quote", "empty_basis", "diagnostic"],
)
def test_unproven_dependencies_do_not_recover(mutation):
    current = withheld_round()
    if mutation == "missing_indexes":
        current.draft.summary_application_indexes = []
    elif mutation == "legacy":
        current.draft._composed_summary = False
    elif mutation == "item_dependency":
        current.draft.summary_item_indexes = [0]
    elif mutation == "joint_part":
        current.draft.answer_parts[0].application_indexes = [0, 1]
    elif mutation == "unknown":
        current.applications[0].assessment["status"] = "unknown"
    elif mutation == "no_binding":
        current.applications[0].entries[0].item.citations[0]._unit_binding = None
    elif mutation == "empty_basis":
        current.applications[0].entries = []
    elif mutation == "diagnostic":
        current.timing["semantic_audit_policy"] = {"mode": "diagnostic_only"}
        current.applications[0].assessment = {}
    elif mutation == "changed_quote":
        current.applications[0].entries[0].item.citations[0].quote = "別の原文"
    result = grounded.finalize_summary(current)
    assert result.summary == grounded.answer_labels().neutral_summary
    assert result.summary_projection is None


@pytest.mark.parametrize("stage", ["finalize", "link"])
@pytest.mark.parametrize(
    "mutation",
    [
        "unpublished",
        "removed",
        "text",
        "source",
        "version",
        "scope",
        "selection",
        "summary",
        "binding",
        "replaced_entry",
        "legacy",
        "diagnostic",
        "coverage",
        "request_status",
        "entries_replaced",
        "new_entry_object",
        "new_summary_reason",
        "part_request",
    ],
)
def test_later_dependency_changes_withhold_projection(stage, mutation):
    current = grounded.finalize_summary(withheld_round())
    assert current.summary == GOOD
    assert current.summary_projection is not None
    app = current.applications[0]
    basis = app.entries[0]
    if mutation == "unpublished":
        app.reject("後段で撤回")
    elif mutation == "removed":
        basis.remove("後段で撤回")
    elif mutation == "text":
        basis.span["text"] += "別の本文"
    elif mutation == "source":
        basis.span["source_id"] = "other-source"
    elif mutation == "version":
        basis.span["document_version"] = "other-version"
    elif mutation == "scope":
        basis.span["document_scope"] = ["other-document"]
    elif mutation == "selection":
        current.draft.summary_application_indexes.reverse()
    elif mutation == "summary":
        current.summary = "変更された摘要"
    elif mutation == "binding":
        basis.item.citations[0]._unit_binding = None
    elif mutation == "replaced_entry":
        current.checked = [replace(entry) if entry is basis else entry for entry in current.checked]
    elif mutation == "legacy":
        current.draft._composed_summary = False
    elif mutation == "diagnostic":
        current.timing["semantic_audit_policy"] = {"mode": "diagnostic_only"}
    elif mutation == "coverage":
        current.draft.request_coverage = []
    elif mutation == "request_status":
        current.missing_request_ids = ("Q1",)
    elif mutation == "entries_replaced":
        app.entries = [current.checked[1]]
    elif mutation == "new_entry_object":
        copied = replace(basis)
        current.checked.append(copied)
        app.entries = [copied]
    elif mutation == "new_summary_reason":
        current.summary_reason = "後段で摘要を保留"
    elif mutation == "part_request":
        current.draft.answer_parts[0].request_id = "Q2"
    result = grounded.finalize_summary(current) if stage == "finalize" else grounded.link_summary_evidence(current)
    assert result.summary == grounded.answer_labels().neutral_summary
    assert result.summary_projection is None
    assert BAD not in result.summary


def test_original_normal_summary_and_all_retained_dependencies_are_unchanged():
    response, current, calls = fixture(decisions=(True, True), summary_supported=True, second=KEPT)
    original = GOOD + "\n" + KEPT
    assert response.answer_text.startswith(original)
    assert current.summary == original
    assert current.summary_projection is None
    assert len(calls) == 4 and calls.count("ApplicationExplanation") == 2
    assert grounded.finalize_summary(current).summary == original


def test_no_valid_application_does_not_construct_summary():
    _, current, _ = fixture(decisions=(False, False))
    result = grounded.finalize_summary(current)
    assert result.summary == grounded.answer_labels().neutral_summary
    assert result.summary_projection is None


def test_all_retained_composed_summary_preserves_existing_behavior():
    _, current, _ = fixture(decisions=(True, True), second=KEPT)
    result = grounded.finalize_summary(current)
    assert result.summary == GOOD + "\n" + KEPT
    assert result.summary_projection is None


@pytest.mark.parametrize("key", ["document_id", "recipe_id", "chunk_set_id", "document_scope", "source_id", "document_version"])
def test_changed_source_before_projection_does_not_certify_new_scope(key):
    current = withheld_round()
    current.applications[0].entries[0].span[key] = ["other-document"] if key == "document_scope" else "other-source"
    result = grounded.finalize_summary(current)
    assert result.summary == grounded.answer_labels().neutral_summary
    assert result.summary_projection is None


def test_different_initial_application_dependency_is_not_recovered():
    current = withheld_round()
    current.applications[0].entries = [current.checked[1]]
    result = grounded.finalize_summary(current)
    assert result.summary == grounded.answer_labels().neutral_summary
    assert result.summary_projection is None
