"""原文の確認と画像用途の帰属を、実送達のbound範囲で分けて表示する。"""

from copy import deepcopy

import pytest
from test_image_purpose_wire import snapshot, source

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.image_text_sources import image_purpose_sidecar
from rag_engine.models.llm import GroundedCitation, GroundedItem


def entry_for(purposes):
    span = source()
    snap = snapshot(span)
    ids = [uid for row in image_purpose_sidecar(snap) if row["purpose"] in purposes for uid in row["unit_ids"]]
    bindings = SentUnitCatalog([snap]).bind(ids)
    entries = []
    for binding in bindings:
        cited = GroundedCitation(evidence_id="E1", quote=binding.text)
        cited._unit_binding = binding
        entries.append(grounded.CheckedItem(GroundedItem(kind="rule", text=binding.text, citations=[cited]), span=span))
    return entries


def test_example_original_is_preserved_and_explicitly_attributed():
    [entry] = entry_for({"example"})
    before = entry.item.model_dump()
    output = grounded.render_answer("", [entry])
    assert "画面例・説明用" in output.text
    assert entry.item.text in output.text
    assert "ボタン: 保存" in output.text
    assert entry.item.model_dump() == before
    assert len(output.citation_lines) == 1


def test_unknown_is_not_promoted_by_other_record_range():
    [entry] = entry_for({"unknown"})
    assert "画像の用途が未記録" in grounded.render_answer("", [entry]).text


def test_record_has_no_additional_purpose_annotation():
    [entry] = entry_for({"record"})
    text = grounded.render_answer("", [entry]).text
    assert "画面例・説明用" not in text and "画像の用途が未記録" not in text
    assert entry.item.text in text


def test_mixed_bound_range_has_local_annotation_without_rewriting_original():
    entries = entry_for({"record", "example", "unknown"})
    text = grounded.render_answer("", entries).text
    assert "画面例・説明用" in text and "画像の用途が未記録" in text
    assert text.index("測定値=42") < text.index("画面例・説明用") < text.index("測定値=17")
    assert text.index("測定値=17") < text.index("画像の用途が未記録") < text.index("測定値=23")
    for entry in entries:
        assert entry.item.citations[0]._unit_binding.text == entry.item.text


def test_native_text_without_image_scope_is_unchanged():
    text = "原生の説明では保存ボタンを押します。"
    span = {"evidence_id": "E1", "label": "E1", "source": "synthetic.txt", "page": 1, "text": text}
    snap = EvidenceUnitSnapshot.create(span, [(0, len(text))], grounded.CITATION_UNIT_CONTRACT)
    [bound] = SentUnitCatalog([snap]).bind(snap.unit_ids)
    citation = GroundedCitation(evidence_id="E1", quote=text)
    citation._unit_binding = bound
    entry = grounded.CheckedItem(GroundedItem(kind="rule", text=text, citations=[citation]), span=span)
    assert grounded.render_answer("", [entry]).text == "資料により確認できた事項\n\n・" + text + "\n根拠：synthetic.txt p.1"


def test_purpose_notes_are_structural_but_original_claims_remain_auditable():
    from rag_engine.evaluation.answer_eval import _answer_passages, _judged_passages

    [entry] = entry_for({"example"})
    text = grounded.render_answer("", [entry]).text
    for split in (_answer_passages, _judged_passages):
        passages = split(text)
        assert all("画面例・説明用" not in row["text"] for row in passages)
        assert any("測定値=17" in row["text"] for row in passages)


def test_custom_notes_are_display_only_and_not_a_semantic_pattern():
    from rag_parser_core.language import bind_pack, builtin_pack, resolve

    from rag_engine.evaluation.answer_eval import _answer_passages

    original = builtin_pack()
    custom = resolve(
        original, {"answer_labels": {"image_example_source_note": "合成の帰属表示A", "image_unknown_source_note": "合成の帰属表示B"}}
    )
    assert custom.ingest_hash == original.ingest_hash
    with bind_pack(custom):
        [entry] = entry_for({"example"})
        text = grounded.render_answer("", [entry]).text
        assert "合成の帰属表示A" in text and "画面例・説明用" not in text
        assert all("合成の帰属表示A" not in row["text"] for row in _answer_passages(text))


def test_annotation_keeps_citation_coordinates_and_selected_original(monkeypatch):
    from rag_engine.generation.image_source_display import ImageSourceDisplayPart

    entries = entry_for({"record", "example", "unknown"})
    annotated = grounded.render_answer("", entries)
    monkeypatch.setattr(grounded, "image_source_display_parts", lambda binding, text, span: (ImageSourceDisplayPart(text),))
    plain = grounded.render_answer("", entries)
    assert annotated.citation_lines == plain.citation_lines
    assert len(annotated.citation_lines) == 1
    assert all(entry.item.quote == entry.item.citations[0]._unit_binding.text for entry in entries)


def test_damaged_context_cannot_restore_record_purpose():
    [entry] = entry_for({"record"})
    span = deepcopy(entry.span)
    span["image_text_source_context"]["text_sha256"] = "invalid"
    entry.span = span
    assert "画像の用途が未記録" in grounded.render_answer("", [entry]).text


def test_forged_top_level_setting_cannot_override_example_scope():
    [entry] = entry_for({"example"})
    entry.span["vision_image_purpose"] = "record"
    assert "画面例・説明用" in grounded.render_answer("", [entry]).text


def test_old_unit_contract_keeps_image_purpose_unknown():
    [entry] = entry_for({"record"})
    old = EvidenceUnitSnapshot.create(entry.span, snapshot(entry.span).units, grounded.CITATION_UNIT_CONTRACT)
    ids = [old.unit_ids[i] for i, (start, end) in enumerate(old.units) if entry.item.text == entry.span["text"][start:end]]
    # 画像本文が複数単位なら対応する既知の原文位置だけを選ぶ。
    if not ids:
        current = entry.item.citations[0]._unit_binding
        ids = list(old.unit_ids[current.first : current.last + 1])
    [bound] = SentUnitCatalog([old]).bind(ids)
    entry.item.citations[0]._unit_binding = bound
    assert "画像の用途が未記録" in grounded.render_answer("", [entry]).text


def test_native_unit_in_mixed_scope_has_no_image_note():
    span = source()
    snap = snapshot(span)
    image_ids = {u for row in image_purpose_sidecar(snap) for u in row["unit_ids"]}
    [bound] = SentUnitCatalog([snap]).bind([u for u in snap.unit_ids if u not in image_ids])
    cited = GroundedCitation(evidence_id="E1", quote=bound.text)
    cited._unit_binding = bound
    entry = grounded.CheckedItem(GroundedItem(kind="rule", text=bound.text, citations=[cited]), span=span)
    text = grounded.render_answer("", [entry]).text
    assert bound.text in text and "画面例・説明用" not in text and "画像の用途が未記録" not in text


@pytest.mark.parametrize("key", ["image_example_source_note", "image_unknown_source_note"])
def test_missing_new_display_label_is_a_contract_error_without_compatibility_fill(key):
    import json
    from importlib import resources

    from rag_parser_core.language import (
        LanguagePackError,
        build_pack,
        complete_compatible_pack,
        pack_from_payload,
    )

    raw = json.loads(resources.files("rag_parser_core.language").joinpath("packs", "ja.json").read_text())
    del raw["answer_labels"][key]
    saved = deepcopy(raw)
    assert key not in complete_compatible_pack(raw)["answer_labels"]
    with pytest.raises(LanguagePackError):
        build_pack(raw)
    with pytest.raises(LanguagePackError):
        pack_from_payload({"snapshot": raw})
    assert raw == saved


@pytest.mark.parametrize("quote_only", [False, True])
def test_normal_display_transforms_do_not_drop_mixed_purpose_attribution(quote_only):
    [entry] = entry_for({"record", "example"})
    entry.item = entry.item.model_copy(update={"text": " ".join(entry.item.text.split())})
    entry.quote_only = quote_only
    original = entry.item.model_dump()
    text = grounded.render_answer("", [entry]).text
    assert "画面例・説明用" in text
    assert entry.item.model_dump() == original
    if not quote_only:
        assert entry.item.text in text


def test_split_original_separators_do_not_create_empty_display_bullets():
    entries = entry_for({"record", "example"})
    rendered = grounded.render_answer("", entries).text
    assert "・\n" not in rendered
    for entry in entries:
        assert entry.item.citations[0]._unit_binding.text == entry.item.text
