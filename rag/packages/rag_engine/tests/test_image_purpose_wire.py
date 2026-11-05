"""画像用途の送達と、実際に選択した記録Uの表示を合成データで確かめる。"""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from rag_engine.chunking.text_projection import IMAGE_TEXT_SOURCE_ROLES
from rag_engine.generation import batched_evidence, grounded
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.image_text_sources import image_purpose_sidecar


def source() -> dict[str, Any]:
    parts = [
        "原生本文は900です。",
        "測定画面\n値: 測定値=42\nボタン: 保存",
        "画面例\n値: 測定値=17\nボタン: 保存",
        "用途未記録\n値: 測定値=23",
    ]
    text = "\n".join(parts)
    rows = []
    offset = len(parts[0]) + 1
    for index, (part, purpose) in enumerate(zip(parts[1:], ("record", "example", "unknown"), strict=True)):
        rows.append(
            {
                "start": offset,
                "end": offset + len(part),
                "role": sorted(IMAGE_TEXT_SOURCE_ROLES)[0],
                "image_id": f"private-image-{index}",
                "source_record_id": f"private-record-{index}",
                "vision_image_purpose": purpose,
            }
        )
        offset += len(part) + 1
    return {
        "label": "E1",
        "evidence_id": "E1",
        "source_id": "source-a",
        "source": "synthetic.txt",
        "text": text,
        "page": 1,
        "tags": ["image_generated"],
        "origin": "image_extraction",
        "image_text_source_context": {
            "version": 1,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "scope": {"document_id": "private-doc", "recipe_id": "private-recipe", "chunk_set_id": "private-set"},
            "completeness": "partial",
            "source_signature": "private-signature",
            "ranges": rows,
            "native_text_ranges": [{"start": 0, "end": len(parts[0])}],
        },
    }


def snapshot(span: dict[str, Any]) -> EvidenceUnitSnapshot:
    return EvidenceUnitSnapshot.create(span, grounded.evidence_citation_units(span), grounded.evidence_unit_contract(span))


def test_generation_note_and_original_text_have_same_unit_purposes() -> None:
    span = source()
    old = deepcopy(span)
    snap = snapshot(span)
    sidecar = image_purpose_sidecar(snap)
    note = grounded._model_source_note(span, snap.units)
    assert "image_purposes=" + json.dumps(sidecar, ensure_ascii=False, separators=(",", ":")) in note
    rendered = grounded._rendered_evidence_span(span, units=snap.units, numbered=True)
    assert all(uid in rendered for row in sidecar for uid in row["unit_ids"])
    assert "private-" not in rendered
    assert all(span["text"][a:b] in rendered for a, b in snap.units)
    assert span == old


def test_batch_reading_sidecar_uses_actual_batch_unit_ids() -> None:
    span = source()
    snap = batched_evidence.reading_unit_catalog([span]).snapshots[0]
    shown = batched_evidence._labeled_text("B1", span)
    assert "image_purposes=" + json.dumps(image_purpose_sidecar(snap), ensure_ascii=False, separators=(",", ":")) in shown
    assert all(uid in shown for uid in snap.unit_ids)
    assert "private-" not in shown


def test_cross_boundary_and_old_contract_are_not_silently_relabelled() -> None:
    span = source()
    full = EvidenceUnitSnapshot.create(span, [(0, len(span["text"]))], grounded.IMAGE_CITATION_UNIT_CONTRACT)
    with pytest.raises(ValueError, match="境界"):
        image_purpose_sidecar(full)
    old = EvidenceUnitSnapshot.create(span, snapshot(span).units, grounded.CITATION_UNIT_CONTRACT)
    with pytest.raises(ValueError, match="版"):
        image_purpose_sidecar(old)


@pytest.mark.parametrize("purpose", ["record", "example", "unknown"])
def test_display_guard_uses_selected_unit_not_whole_mixed_span(purpose: str) -> None:
    span = source()
    snap = snapshot(span)
    ids = [uid for row in image_purpose_sidecar(snap) if row["purpose"] == purpose for uid in row["unit_ids"]]
    binding = SentUnitCatalog([snap]).bind(ids)[0]
    item = SimpleNamespace(quote=binding.text, citations=[SimpleNamespace(_unit_binding=binding)])
    entry = SimpleNamespace(item=item, span=span)
    displayed = grounded._displayed_quote(entry)
    assert ("測定値=" in displayed) is (purpose == "record")
    assert binding.text == item.quote


def test_record_span_without_authenticated_selection_does_not_bypass_guard() -> None:
    span = source()
    entry = SimpleNamespace(item=SimpleNamespace(quote=span["text"], citations=[]), span=span)
    assert "測定値=42" not in grounded._displayed_quote(entry)
    snap = snapshot(span)
    binding = SentUnitCatalog([snap]).bind(snap.unit_ids)[0]
    entry.item.citations = [SimpleNamespace(_unit_binding=binding)]
    assert "測定値=42" not in grounded._displayed_quote(entry)


def test_missing_context_keeps_existing_native_rendering() -> None:
    span = source()
    span.pop("image_text_source_context")
    assert image_purpose_sidecar(snapshot(span)) == []
    assert "image_purposes=" not in grounded._source_note(span)
    assert "image_purposes=" not in batched_evidence._labeled_text("B1", span)


def test_native_notes_do_not_create_new_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    span = source()
    span.pop("image_text_source_context")
    span["answer_context"] = "原文の補助情報"
    original = grounded._source_note(span)

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("原生補助情報のための追加snapshotは禁止")

    monkeypatch.setattr(EvidenceUnitSnapshot, "create", unexpected)
    assert grounded._source_note(span) == original
    assert grounded._scope_image_purposes(span) == {}


def test_scope_projection_and_generation_use_same_sidecar() -> None:
    span = source()
    snap = snapshot(span)
    assert grounded._scope_image_purposes(span) == {"image_purposes": image_purpose_sidecar(snap)}
    text = grounded.scope_evidence_text(span)
    assert all(uid in text for row in grounded._scope_image_purposes(span)["image_purposes"] for uid in row["unit_ids"])


def test_actual_canonical_citation_keeps_record_value() -> None:
    from citation_unit_support import selected_answer_payload

    from rag_engine.generation.citation_wire import canonical_generation_draft

    span = source()
    snap = snapshot(span)
    ids = next(row["unit_ids"] for row in image_purpose_sidecar(snap) if row["purpose"] == "record")
    catalog = SentUnitCatalog([snap])
    model = grounded.generation_output_model("合成の測定記録は何ですか。", unit_catalog=catalog)
    draft = canonical_generation_draft(model.model_validate(selected_answer_payload(ids)), unit_catalog=catalog)
    resolved, error = grounded.cited_quote(draft.items[0], [span])
    assert not error
    entry = grounded.CheckedItem(item=resolved, span=snap.span())
    assert "測定値=42" in grounded._displayed_quote(entry)
    assert entry.item.citations[0]._unit_binding.snapshot == snap


def test_shared_policy_is_once_per_image_request_and_native_unchanged() -> None:
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY, image_purpose_system, with_image_purpose_policy

    base = "## Step 1: 読む\n## Step 2: 判定\n## Step 3: 返す"
    assert image_purpose_system(base, [{"text": "原生本文"}]) == base
    policy = image_purpose_system(base, [source()])
    assert policy.count(IMAGE_PURPOSE_POLICY) == 1
    assert policy.index(IMAGE_PURPOSE_POLICY) < policy.index("## Step 3:")
    assert with_image_purpose_policy(policy) == policy
    assert grounded.GENERATE_SYSTEM_PROMPT.count(IMAGE_PURPOSE_POLICY) == 1
    assert grounded.PLANNED_GENERATE_SYSTEM_PROMPT.count(IMAGE_PURPOSE_POLICY) == 1
    assert with_image_purpose_policy(grounded.GENERATE_SYSTEM_PROMPT) == grounded.GENERATE_SYSTEM_PROMPT


def test_actual_scope_parse_receives_units_purposes_and_shared_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.config import get_settings
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY

    class Captured(BaseException):
        pass

    span = {**source(), "function": ("synthetic.txt", ""), "document_scope": ["synthetic-doc", "v1"]}
    monkeypatch.setattr(grounded, "uses_separate_scope_plan", lambda question: True)
    captured: dict[str, Any] = {}

    def intercept(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        captured.update(system=system, prompt=json.loads(prompt), schema=schema.model_json_schema())
        raise Captured()

    with pytest.raises(Captured):
        grounded.run_round(
            "合成の記録値は何ですか。",
            [span],
            get_settings(environ={}, dotenv_path=None),
            prompt="合成の入力",
            image_paths=[],
            provider_id=None,
            parse_text=intercept,
            parse_images=lambda *args, **kwargs: pytest.fail("画像呼び出しは禁止"),
            audit_mode=grounded.AUDIT_MODE_NONE,
        )
    evidence = captured["prompt"]["evidence"][0]
    assert evidence["image_purposes"] == image_purpose_sidecar(snapshot(span))
    assert all(uid in evidence["text"] for row in evidence["image_purposes"] for uid in row["unit_ids"])
    assert captured["system"].count(IMAGE_PURPOSE_POLICY) == 1
    assert "private-" not in json.dumps(captured, ensure_ascii=False)


def test_actual_batch_read_receives_policy_and_purpose_data() -> None:
    from dataclasses import replace

    from rag_engine.config import get_settings
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY

    class Captured(BaseException):
        pass

    span = source()
    captured: dict[str, Any] = {}

    def intercept(system: str, prompt: str, schema: Any) -> Any:
        captured.update(system=system, prompt=json.loads(prompt), schema=schema.model_json_schema())
        raise Captured()

    def render(values: Any) -> str:
        return json.dumps([row["text"] for row in values], ensure_ascii=False)

    with pytest.raises(Captured):
        batched_evidence.prepare_batched_evidence(
            [span],
            replace(get_settings(environ={}, dotenv_path=None), evidence_budget_chars=80),
            question="合成の記録値は何ですか。",
            render=render,
            evidence_text=render,
            system="合成の生成指示",
            schema={"type": "object"},
            read=intercept,
            unusable_output=lambda error: False,
        )
    assert captured["system"].count(IMAGE_PURPOSE_POLICY) == 1
    assert any("image_purposes=" in row["text"] for row in captured["prompt"]["evidence"])
    assert "private-" not in json.dumps(captured, ensure_ascii=False)


def test_image_origin_definition_does_not_override_unit_purpose() -> None:
    policy = grounded.GENERATE_SYSTEM_PROMPT
    assert "image_generated: 画像からの生成説明・OCR" in policy
    definition = next(line for line in policy.splitlines() if "image_generated:" in line)
    assert "必須根拠にはならない" not in definition
    assert "「例示値と実データ」に従う" in definition


def test_actual_range_repair_receives_same_source_sidecar_and_policy() -> None:
    from test_citation_range_repair import run_first

    from rag_engine.config import get_settings
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY

    class Captured(BaseException):
        pass

    span = {**source(), "function": ("synthetic.txt", "合成の操作"), "document_scope": ["synthetic-doc", "v1"]}
    grounded.format_functions([span])
    first = run_first([span])
    captured: dict[str, Any] = {}

    def intercept(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        captured.update(system=system, prompt=json.loads(prompt.split("\n", 1)[1]), schema=schema.model_json_schema())
        raise Captured()

    with pytest.raises(Captured):
        grounded.run_round(
            "合成の記録値は何ですか。",
            [span],
            get_settings(environ={}, dotenv_path=None),
            prompt="合成の入力",
            image_paths=[],
            provider_id=None,
            parse_text=intercept,
            parse_images=lambda *args, **kwargs: pytest.fail("画像呼び出しは禁止"),
            previous=first.checked,
            previous_draft=first.draft,
            previous_dropped=first.dropped,
            audit_mode=grounded.AUDIT_MODE_NONE,
        )
    sent = captured["prompt"]["citation_sources"][0]
    assert sent["image_purposes"] == image_purpose_sidecar(snapshot(span))
    assert captured["system"].count(IMAGE_PURPOSE_POLICY) == 1
    assert all(uid in sent["text"] for row in sent["image_purposes"] for uid in row["unit_ids"])


@pytest.mark.parametrize("with_images", [True, False])
@pytest.mark.parametrize("mode", [grounded.AUDIT_MODE_DETAILED, grounded.AUDIT_MODE_STANDARD])
def test_requested_full_audit_receives_same_shared_policy(mode: str, with_images: bool) -> None:
    from citation_unit_support import selected_answer_payload

    from rag_engine.config import get_settings
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY
    from rag_engine.models.audit_wire import is_audit_wire

    class Captured(BaseException):
        pass

    span = {**source(), "function": ("synthetic.txt", "合成の操作"), "document_scope": ["synthetic-doc", "v1"]}
    if not with_images:
        span.pop("image_text_source_context")
        span.update(tags=[], origin="document_text")
    grounded.format_functions([span])
    captured: dict[str, Any] = {}

    def intercept(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        if not is_audit_wire(schema):
            snap = snapshot(span)
            ids = (
                next(row["unit_ids"] for row in image_purpose_sidecar(snap) if row["purpose"] == "record")
                if with_images
                else [snap.unit_ids[0]]
            )
            return schema.model_validate(selected_answer_payload(ids))
        captured.update(system=system, prompt=json.loads(prompt), schema=schema.model_json_schema())
        raise Captured()

    with pytest.raises(Captured):
        grounded.run_round(
            "合成の記録値は何ですか。",
            [span],
            get_settings(environ={}, dotenv_path=None),
            prompt=grounded.evidence_block_text([span]),
            image_paths=[],
            provider_id=None,
            parse_text=intercept,
            parse_images=lambda *args, **kwargs: pytest.fail("画像呼び出しは禁止"),
            audit_mode=mode,
        )
    assert captured["system"].count(IMAGE_PURPOSE_POLICY) == int(with_images)
    sent = captured["prompt"]["documents"][0]["evidence"][0]
    if with_images:
        assert sent["image_purposes"] == image_purpose_sidecar(snapshot(span))
        assert all(uid in sent["text"] for row in sent["image_purposes"] for uid in row["unit_ids"])
    else:
        assert "image_purposes" not in sent
        assert captured["system"] == grounded.audit_system_prompt(detailed=mode == grounded.AUDIT_MODE_DETAILED)
