"""最終検証の画像用途は原文位置だけに結び、引用本文を変えない。"""

import hashlib
from copy import deepcopy
from typing import Any, Literal

import pytest

from rag_engine.evaluation import answer_validation as validation
from rag_engine.evaluation.answer_eval import _evidence_fragments, model_request
from rag_engine.evaluation.image_text_sources import validation_image_system
from rag_engine.generation.answer_policy import DOCUMENT_IMAGE_VALUE_POLICY
from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY


def item(text: str = "本文8。\n記録４２。\n例９００。") -> dict[str, Any]:
    start = text.index("記録")
    end = text.index("\n", start)
    return {
        "id": "synthetic-c1",
        "source": "synthetic.txt",
        "text": text,
        "evidence_type": "ocr",
        "image_text_source_context": {
            "version": 1,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "scope": {"document_id": "synthetic-doc", "recipe_id": "synthetic-recipe", "chunk_set_id": "synthetic-set"},
            "completeness": "complete",
            "source_signature": "synthetic-signature",
            "ranges": [
                {
                    "start": start,
                    "end": end,
                    "role": "image_description",
                    "image_id": "synthetic-image1",
                    "source_record_id": "synthetic-element1",
                    "vision_image_purpose": "record",
                },
                {
                    "start": end + 1,
                    "end": len(text),
                    "role": "image_description",
                    "image_id": "synthetic-image2",
                    "source_record_id": "synthetic-element2",
                    "vision_image_purpose": "example",
                },
            ],
            "native_text_ranges": [{"start": 0, "end": start - 1}],
        },
    }


def test_raw_text_eid_and_width_preserved_with_only_public_sidecar() -> None:
    original = item()
    plain = {k: v for k, v in original.items() if k != "image_text_source_context"}
    fragments = list(_evidence_fragments([original]))
    old = list(_evidence_fragments([plain]))
    assert [(r["evidence_id"], r["text"], r["fragment_start"], r["fragment_end"]) for r in fragments] == [
        (r["evidence_id"], r["text"], r["fragment_start"], r["fragment_end"]) for r in old
    ]
    public = model_request({"evidence_items": fragments})["evidence_items"][0]
    assert public["text"] == original["text"]
    assert [r["purpose"] for r in public["image_purposes"]] == ["record", "example"]
    assert set(public["image_purposes"][0]) == {"start", "end", "purpose"}
    assert "image_text_source_context" not in public
    assert "scope" not in public
    assert "text_sha256" not in public
    assert all(not key.endswith("_id") for row in public["image_purposes"] for key in row)
    system = validation_image_system(validation.VALIDATION_SYSTEM_PROMPT, fragments)
    assert DOCUMENT_IMAGE_VALUE_POLICY not in system
    assert IMAGE_PURPOSE_POLICY in system
    assert "本文で明示された既定値" in system


@pytest.mark.parametrize("change", ["hash", "scope", "range", "missing"])
def test_invalid_context_cannot_supply_record(change: str) -> None:
    value = item()
    if change == "hash":
        value["image_text_source_context"]["text_sha256"] = "forged"
    elif change == "scope":
        value["image_text_source_context"]["scope"]["recipe_id"] = None
    elif change == "range":
        value["image_text_source_context"]["ranges"][0]["end"] = len(value["text"]) + 1
    else:
        value.pop("image_text_source_context")
    value["vision_image_purpose"] = "record"
    value["image_purposes"] = [{"purpose": "record"}]
    fragments = list(_evidence_fragments([value]))
    public = model_request({"evidence_items": fragments})["evidence_items"][0]
    assert "image_purposes" not in public
    assert validation_image_system(validation.VALIDATION_SYSTEM_PROMPT, fragments) == validation.VALIDATION_SYSTEM_PROMPT


def test_two_thousand_boundary_clips_ranges_without_changing_eid() -> None:
    value = item("原" * 1998 + "記録４２。\n例９００。")
    fragments = list(_evidence_fragments([value]))
    assert len(fragments) == 2
    plain = deepcopy(value)
    plain.pop("image_text_source_context")
    assert [f["evidence_id"] for f in fragments] == [f["evidence_id"] for f in _evidence_fragments([plain])]
    public = model_request({"evidence_items": fragments})["evidence_items"]
    assert public[0]["image_purposes"] == [{"start": 1998, "end": 2000, "purpose": "record"}]
    assert public[1]["image_purposes"][0] == {"start": 0, "end": 3, "purpose": "record"}
    assert "".join(row["text"] for row in public) == value["text"]


def test_native_input_keeps_existing_system_and_width_behavior() -> None:
    value = {"id": "native", "text": "ＡＢＣ１２３"}
    fragments = list(_evidence_fragments([value]))
    assert validation_image_system(validation.VALIDATION_SYSTEM_PROMPT, fragments) == validation.VALIDATION_SYSTEM_PROMPT
    assert model_request({"evidence_items": fragments})["evidence_items"][0]["text"] == "ABC123"


def test_initial_audit_and_figure_recheck_use_same_scoped_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    fragments = list(_evidence_fragments([item()]))
    catalog = {f["evidence_id"]: f for f in fragments}
    passages = [{"id": "P1", "text": "記録値は42です。"}]
    fixed = {"question": "資料の記録値は何ですか。", "answer_text": passages[0]["text"]}
    calls = []

    def stop(system: str, inputs: dict[str, Any], *_a: Any, **_kw: Any) -> Any:
        calls.append((system, model_request(inputs)))
        raise RuntimeError("synthetic_stop_before_model")

    monkeypatch.setattr(validation, "_parse_checked", stop)
    with pytest.raises(RuntimeError, match="synthetic_stop"):
        validation._audit_batch(passages, fixed, fragments, catalog, None, None, 100000)
    claim = validation.ValidationClaimCheck(
        answer_quote=passages[0]["text"],
        answer_passage_id="P1",
        evidence_id=fragments[0]["evidence_id"],
        source_id="synthetic-c1",
        status="supported",
        reason="合成形式",
    )
    validation._recheck_figure_supported([claim], fixed, passages, fragments, catalog, None, None)
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1]["evidence_items"] == calls[1][1]["evidence_items"]
    assert all(IMAGE_PURPOSE_POLICY in system and DOCUMENT_IMAGE_VALUE_POLICY not in system for system, _ in calls)


def test_budget_counts_sidecar_and_does_not_strip_it_to_fit() -> None:
    fragments = list(_evidence_fragments([item()]))
    fixed = {"question": "合成の質問", "answer_passages": []}
    chosen, cut = validation._fit_evidence(fixed, fragments, 10**6)
    assert not cut and chosen == fragments
    chosen, cut = validation._fit_evidence(fixed, fragments, 100)
    assert cut and chosen == []


def test_reference_and_classification_rechecks_keep_same_public_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fragments = list(_evidence_fragments([item()]))
    catalog = {f["evidence_id"]: f for f in fragments}
    passages = [{"id": "P1", "text": "記録値は42です。"}, {"id": "P2", "text": "例の値は900です。"}]
    fixed = {"question": "資料の記録値は何ですか。", "answer_text": "記録値は42です。\n例の値は900です。"}
    calls = []

    def stop(system: str, inputs: dict[str, Any], *_a: Any, **_kw: Any) -> Any:
        calls.append((system, model_request(inputs)))
        raise RuntimeError("synthetic_stop_before_model")

    monkeypatch.setattr(validation, "_parse_checked", stop)

    def claim(status: Literal["unassessed", "citation_error", "supported"], **fields: Any) -> Any:
        return validation.ValidationClaimCheck(
            answer_quote=passages[0]["text"],
            answer_passage_id="P1",
            source_id="synthetic-c1",
            evidence_id=fragments[0]["evidence_id"],
            status=status,
            reason="合成形式",
            **fields,
        )

    mismatch = claim("unassessed", classification_check=validation.CLASSIFICATION_NON_CLAIM_MISMATCH)
    validation._reaudit_misclassified_passages([mismatch], fixed, passages, fragments, catalog, None, None)
    unresolved = claim("citation_error")
    resolved = claim("supported").model_copy(update={"answer_passage_id": "P2"})
    validation._recheck_unreferenced_passages([unresolved, resolved], fixed, passages, fragments, catalog, None, None)
    bad_quote = claim("citation_error", audit_status="supported", reference_check=validation.REFERENCE_QUOTE_UNMATCHED)
    validation._reaudit_reference_failures(
        [bad_quote], fixed, passages, fragments, catalog, None, None, enforce=lambda values, _passages: values
    )
    assert len(calls) == 3
    for system, inputs in calls:
        assert IMAGE_PURPOSE_POLICY in system
        assert DOCUMENT_IMAGE_VALUE_POLICY not in system
        own = inputs["evidence_items"][0]
        assert own["text"] == item()["text"]
        assert [r["purpose"] for r in own["image_purposes"]] == ["record", "example"]
        assert "image_text_source_context" not in own


def test_unknown_range_stays_unknown_in_public_contract() -> None:
    value = item()
    value["image_text_source_context"]["ranges"][0]["vision_image_purpose"] = "unknown"
    public = model_request({"evidence_items": list(_evidence_fragments([value]))})["evidence_items"][0]
    assert public["image_purposes"][0]["purpose"] == "unknown"
    assert public["image_purposes"][1]["purpose"] == "example"


@pytest.mark.parametrize("parent_has_context", [False, True])
def test_scoped_image_child_is_not_dropped_by_equal_parent_text(parent_has_context: bool) -> None:
    child = item()
    child["id"] = "child"
    parent = deepcopy(child) if parent_has_context else {"text": child["text"]}
    parent["id"] = "parent"
    if parent_has_context:
        parent["image_text_source_context"]["scope"]["chunk_set_id"] = "other-set"
        parent["image_text_source_context"]["ranges"][0]["vision_image_purpose"] = "unknown"
    parent["children"] = [child]
    fragments = list(_evidence_fragments([parent]))
    assert [row["id"] for row in fragments] == ["parent", "child"]
    own = model_request({"evidence_items": [fragments[1]]})["evidence_items"][0]
    assert own["image_purposes"][0]["purpose"] == "record"
    assert own["parent_id"] == "parent"


def test_equal_native_child_still_uses_existing_deduplication() -> None:
    parent = {"id": "parent", "text": "同じ原生本文", "children": [{"id": "child", "text": "同じ原生本文"}]}
    assert [row["id"] for row in _evidence_fragments([parent])] == ["parent"]
