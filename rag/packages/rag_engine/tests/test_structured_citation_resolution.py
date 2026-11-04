"""回答の引用元は ID と範囲だけで決まり、引用文から別の原文を探さない。"""

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

SOURCE = "合成の設定画面を開きます。\n合成の内容を入力して保存します。"


def span(identity="source-a", *, text=SOURCE):
    return {
        "evidence_id": identity,
        "label": "E1",
        "source_id": identity,
        "source": "synthetic.pdf",
        "function": ("synthetic.pdf", "合成操作"),
        "text": text,
    }


def proposal(identity, *, quote="", lines=""):
    return GroundedItem(kind="rule", text="合成の説明です。", citations=[GroundedCitation(evidence_id=identity, quote=quote, lines=lines)])


@pytest.mark.parametrize(
    "quote",
    [
        SOURCE,
        "合成の設定画面を開きます。合成の内容を入力し保存します。",
        "合成の設定画面を開きます。存在しない操作をします。",
        "合成の設定画面を開きます。 … 保存します。",
        "合成の設定画面を開きます。 / 合成の内容を入力して保存します。",
        "合成の内容を入力して保存します。\n合成の設定画面を開きます。",
    ],
)
@pytest.mark.parametrize("identity", ["", "missing"])
def test_even_exact_or_partial_text_never_recovers_a_missing_source_id(quote, identity):
    evidence = [span(), span("source-b")]
    draft = GroundedDraft(items=[proposal(identity, quote=quote)], summary="", confidence="low")
    checked, dropped = grounded.verify("合成の設定は？", draft, evidence)
    entry = checked[0]
    grounded.detach_unsourced(checked)
    assert entry.item.text == "合成の説明です。" and entry.span is None
    assert entry.citation_resolution["reason_code"] == "citation_id_invalid"
    assert entry.citation_resolution["requested_quote"] == quote
    assert not entry.confirmed
    assert all("nearest_quote" not in row and "miscopied" not in row for row in dropped)


def test_a_valid_id_extracts_its_own_original_and_ignores_stored_quote_text():
    evidence = [span("source-a"), {**span("source-b", text="別の原文です。"), "label": "E2"}]
    cited, error = grounded.cited_quote(proposal("source-b", quote=SOURCE), evidence)
    assert not error and cited.quote == "別の原文です。"
    assert grounded._citation_extraction(cited)["resolved_evidence_id"] == "source-b"


def test_duplicate_ids_do_not_choose_the_first_even_if_the_originals_are_identical():
    cited, error = grounded.cited_quote(proposal("source-a", quote=SOURCE), [span(), span()])
    assert error and not cited.quote
    assert grounded._citation_extraction(cited)["reason_code"] == "citation_id_ambiguous"


def test_discontinuous_units_are_extracted_separately_without_the_gap():
    text = "最初の原文です。\n中間の未選択の原文です。\n最後の原文です。"
    evidence = [span(text=text)]
    grounded.format_functions(evidence)
    catalog = grounded.generation_unit_catalog(evidence)
    assert len(catalog.unit_ids) == 3
    schema = grounded.generation_output_model("合成の設定は？", unit_catalog=catalog)
    output = schema.model_validate(
        {
            "items": [
                {"kind": "rule", "text": "合成の説明です。", "citations": [{"unit_ids": [catalog.unit_ids[0], catalog.unit_ids[2]]}]}
            ],
            "summary": "",
            "confidence": "low",
        }
    )
    draft = canonical_generation_draft(output, unit_catalog=catalog)
    checked, dropped = grounded.verify("合成の設定は？", draft, evidence)
    own = list(grounded.item_citations(checked[0]))
    assert not dropped
    assert [row.item.quote for row in own] == ["最初の原文です。", "最後の原文です。"]
    assert all("未選択" not in row.item.quote for row in own)
    assert all(row.citation_resolution["range_contract"] == "stable_unit_ids" for row in own)


def test_system_quotes_use_the_selected_coordinates_when_the_text_is_repeated():
    evidence = span(text=SOURCE + "\n" + SOURCE)
    start = len(SOURCE) + 1
    entry = grounded._quote_item(evidence, start, start + len(SOURCE), "合成の補完")
    proof = entry.citation_resolution
    assert entry.item.quote == SOURCE
    assert proof["method"] == "system_char_range"
    assert proof["char_start"] == str(start) and proof["char_end"] == str(start + len(SOURCE))
    assert proof["snapshot_id"] and proof["source_id"] == "source-a"
    assert grounded._citation_extraction(entry.item) == proof
    # 後続 round は原版と範囲を保持し、新しい表示番号や同文の別出典へ付け替えない。
    copied = grounded.carry_over([], [entry])[0]
    assert copied.carried and copied.citation_resolution == proof
    copied.citation_resolution["char_start"] = "0"
    assert entry.citation_resolution["char_start"] == str(start)
    assert copied.span["text"] == evidence["text"]
    assert copied.span["source_id"] == evidence["source_id"]


def test_system_range_outside_the_source_is_rejected():
    with pytest.raises(ValueError, match="範囲外"):
        grounded._quote_item(span(), 0, len(SOURCE) + 1, "合成の補完")


def test_feedback_cannot_rebind_to_the_same_text_in_a_different_source_or_version():
    original = span()
    ref = grounded._source_reference(original)
    assert grounded._current_label(ref, [{**original, "label": "E9"}]) == "E9"
    assert grounded._current_label(ref, [span("source-b")]) == ""
    assert grounded._current_label(ref, [{**original, "document_version": "new"}]) == ""
    assert grounded._current_label(ref, [original, original]) == ""


def test_long_system_selection_retains_the_full_range_separately_from_display_text():
    text = "合成の原文です。" * 100
    entry = grounded._quote_item(span(text=text), 0, len(text), "合成の補完")
    assert entry.item.quote == text
    assert len(entry.item.text) == 600
    assert entry.citation_resolution["char_end"] == str(len(text))


def test_deduplicating_display_does_not_discard_explicit_same_text_sources():
    first = grounded._quote_item(span(), 0, len(SOURCE), "合成の補完")
    second = grounded._quote_item(span("source-b"), 0, len(SOURCE), "合成の補完")
    merged = grounded.without_redundant_quotes([first, second])
    assert len(merged) == 1
    assert {row.span["source_id"] for row in grounded.item_citations(merged[0])} == {"source-a", "source-b"}
    first = grounded._quote_item(span(), 0, len(SOURCE), "合成の補完")
    carried = grounded.carry_over([first], [second])
    assert {row.span["source_id"] for row in grounded.item_citations(carried[0])} == {"source-a", "source-b"}


def test_same_text_in_another_source_is_not_counted_as_a_guide_step_citation():
    first = grounded._quote_item(span(), 0, len(SOURCE), "合成の補完")
    first.quote_only = False
    first.item = first.item.model_copy(update={"applies": "matched"})
    statuses, _ = grounded.guide_step_assignment([("step-b", "合成の操作", ["source-b"])], [first], [span("source-b")])
    assert statuses[0]["evidence_flow"]["cited_evidence_ids"] == []


@pytest.mark.parametrize("selected", [0, 1])
def test_real_generation_schema_selects_one_of_identical_sources_by_unit_id(selected):
    evidence = [span("source-a"), {**span("source-b"), "label": "E2"}]
    grounded.format_functions(evidence)
    catalog = grounded.generation_unit_catalog(evidence)
    schema = grounded.generation_output_model("合成の設定は？", unit_catalog=catalog)
    chosen = catalog.snapshots[selected].unit_ids[0]
    output = schema.model_validate(
        {"items": [{"kind": "rule", "text": "合成の説明です。", "citations": [{"unit_ids": [chosen]}]}], "summary": "", "confidence": "low"}
    )
    draft = canonical_generation_draft(output, unit_catalog=catalog)
    checked, dropped = grounded.verify("合成の設定は？", draft, evidence)
    assert not dropped
    assert checked[0].span["source_id"] == evidence[selected]["source_id"]
    assert checked[0].citation_resolution["selected_unit_ids"] == '["' + chosen + '"]'


def test_valid_reference_source_is_preserved_without_promoting_its_semantic_support():
    first = grounded._quote_item(span(), 0, len(SOURCE), "合成の補完")
    second = grounded._quote_item(span("source-b"), 0, len(SOURCE), "合成の補完")
    second.marks = [{"code": "unsupported", "label": "根拠で確かめられない", "detail": ""}]
    grounded._merge_citation_provenance(first, second)
    sources = list(grounded.item_citations(first))
    assert len(sources) == 2
    assert sources[1].state == "unverified"
    assert sources[1].span["source_id"] == "source-b"
