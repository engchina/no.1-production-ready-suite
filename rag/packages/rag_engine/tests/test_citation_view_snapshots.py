"""引用の表示・提取と生応答の受管診断を、同じ版の合成入力で検証する。"""

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from citation_unit_support import selected_answer_payload

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedCitation, GroundedItem

LONG = "対象を選択します。" * 30 + "\n" + "結果を保存します。" * 30


def span(index: int = 1, text: str = LONG) -> dict[str, Any]:
    return {
        "evidence_id": f"stable-{index}",
        "label": f"E{index}",
        "source_id": f"parent-{index}",
        "source": "synthetic.pdf",
        "text": text,
        "function": ("synthetic.pdf", "対象操作"),
        "tags": [],
        "source_order": (0, index, 0),
        "child_ranges": [{"id": f"child-{index}", "start": 0, "end": len(text)}],
    }


def wire_value(evidence_id: str = "E1") -> dict[str, Any]:
    return {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [{"evidence_id": evidence_id, "target": "対象", "relation": "direct"}],
            }
        ],
        "items": [
            {
                "kind": "operation",
                "text": "対象の結果を確認します。",
                "request_id": "Q1",
                "citations": [{"evidence_id": evidence_id, "line_start": 2, "line_end": 2}],
            }
        ],
        "summary": "合成の回答です。",
        "confidence": "low",
    }


def run_round(spans: list[dict[str, Any]], prompt: str, parse: Any = None) -> grounded.Round:
    def fake(*args: Any, **kwargs: Any) -> Any:
        assert issubclass(args[3], generation_draft_wire_model())
        data = wire_value(spans[0]["label"])
        catalog = grounded.generation_unit_catalog(spans, sent_only=True)
        if not catalog.unit_ids:
            # 未送達の旧引用の再生を検査する。新 producer の空集合契約は別の回帰で検査する。
            return generation_draft_wire_model().model_validate(data)
        if getattr(args[3], "_uses_unit_ids", False):
            data = {**selected_answer_payload([catalog.snapshots[0].unit_ids[1]]), "request_coverage": data["request_coverage"]}
        return args[3].model_validate(data)

    return grounded.run_round(
        "対象の操作方法は？",
        spans,
        get_settings(environ={}, dotenv_path=None),
        prompt=prompt,
        image_paths=(),
        provider_id=None,
        parse_text=parse or fake,
        parse_images=lambda *_a, **_kw: pytest.fail("画像は不要"),
        audit_mode="none",
    )


def test_one_frozen_view_supplies_the_numbered_prompt_and_original_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = span()
    actual_units = grounded.citation_units
    calls = []

    def units(text: str, **kwargs: bool) -> list[tuple[int, int]]:
        calls.append(text)
        return actual_units(text, **kwargs)

    monkeypatch.setattr(grounded, "citation_units", units)
    prompt = grounded.format_functions([evidence])
    assert calls == [LONG]
    view = grounded._CITATION_VIEWS.get()[id(evidence)][1]
    assert view.rendered in prompt
    with pytest.raises(FrozenInstanceError):
        view.text = "別の版"  # type: ignore[misc]
    proposed = GroundedItem(kind="operation", text="対象の結果を確認します。", citations=[GroundedCitation(evidence_id="E1", lines="2")])
    cited, error = grounded.cited_quote(proposed, [evidence])
    begin, end = view.units[1]
    assert not error and cited.quote == view.text[begin:end].strip()
    assert cited.citations[0]._extraction["view_sha256"] == view.view_sha256
    assert cited.citations[0]._extraction["sent_view_status"] == "prepared"
    assert cited.citations[0]._extraction["prepared_view_sha256"] == view.view_sha256


def test_wire_conversion_preserves_private_raw_but_does_not_invent_missing_raw() -> None:
    value = generation_draft_wire_model().model_validate(wire_value())
    assert canonical_generation_draft(value)._provider_output_text is None
    value._provider_output_text = "{  合成の原始応答  }"
    canonical = canonical_generation_draft(value)
    assert canonical._provider_output_text == value._provider_output_text
    assert "合成の原始応答" not in canonical.model_dump_json()


def test_neighbor_text_caches_are_not_duplicated_into_citation_snapshots():
    import json

    from rag_engine.generation.citation_unit_ids import CHECK_ONLY_SPAN_KEYS, EvidenceUnitSnapshot

    original = span()
    baseline_prompt = grounded.evidence_block_text([original])
    baseline_ids = grounded.generation_unit_catalog([original]).unit_ids
    copied = {**original, **{key: ["合成の隣接本文。" * 100] * 100 for key in CHECK_ONLY_SPAN_KEYS}}
    assert grounded.evidence_block_text([copied]) == baseline_prompt
    catalog = grounded.generation_unit_catalog([copied])
    assert catalog.unit_ids == baseline_ids
    view = grounded._CITATION_VIEWS.get()[id(copied)][1]
    assert not CHECK_ONLY_SPAN_KEYS.intersection(json.loads(view.span_json))
    snapshot = EvidenceUnitSnapshot.create(copied, view.units, view.contract_version)
    assert not CHECK_ONLY_SPAN_KEYS.intersection(snapshot.span())
    assert snapshot.span()["text"] == original["text"]
    assert snapshot.span()["child_ranges"] == original["child_ranges"]
    copied["text"] = "変更後の別本文"
    assert snapshot.span()["text"] == original["text"]


def test_generation_tags_skip_unused_related_text_copies_without_changing_prompt(monkeypatch):
    values = [span(1), span(2, "結果を確認します。")]
    legacy = grounded.tag_spans(values)
    expected_prompt = grounded.evidence_block_text(legacy)
    expected_ids = grounded.generation_unit_catalog(legacy).unit_ids
    monkeypatch.setattr(grounded, "_same_function_scope", lambda *args: pytest.fail("不要な文書内の総当たり"))
    current = grounded.tag_spans(values, build_check_context=False)
    assert grounded.evidence_block_text(current) == expected_prompt
    assert grounded.generation_unit_catalog(current).unit_ids == expected_ids
    assert [item["text"] for item in current] == [item["text"] for item in values]
