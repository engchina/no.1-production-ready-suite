"""回答の引用元は ID と範囲だけで決まり、引用文から別の原文を探さない。"""

import pytest
from citation_unit_support import selected_answer_payload

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
    output = schema.model_validate(selected_answer_payload([catalog.unit_ids[0], catalog.unit_ids[2]]))
    draft = canonical_generation_draft(output, unit_catalog=catalog)
    checked, dropped = grounded.verify("合成の設定は？", draft, evidence)
    own = [citation for entry in checked for citation in grounded.item_citations(entry)]
    assert not dropped
    assert [row.item.quote for row in own] == ["最初の原文です。", "最後の原文です。"]
    assert all("未選択" not in row.item.quote for row in own)
    assert all(row.citation_resolution["range_contract"] == "stable_unit_ids" for row in own)


@pytest.mark.parametrize("selected", [0, 1])
def test_real_generation_schema_selects_one_of_identical_sources_by_unit_id(selected):
    evidence = [span("source-a"), {**span("source-b"), "label": "E2"}]
    grounded.format_functions(evidence)
    catalog = grounded.generation_unit_catalog(evidence)
    schema = grounded.generation_output_model("合成の設定は？", unit_catalog=catalog)
    chosen = catalog.snapshots[selected].unit_ids[0]
    output = schema.model_validate(selected_answer_payload([chosen], evidence_ids=(evidence[selected]["label"],)))
    draft = canonical_generation_draft(output, unit_catalog=catalog)
    checked, dropped = grounded.verify("合成の設定は？", draft, evidence)
    assert not dropped
    assert checked[0].span["source_id"] == evidence[selected]["source_id"]
    assert checked[0].citation_resolution["selected_unit_ids"] == '["' + chosen + '"]'

