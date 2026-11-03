"""文書・版の境界と原文順、容量内の文書単位読取を合成データで検証する。"""

import json

from test_batched_evidence import body, finding, prepare, span

from rag_engine.generation.document_inputs import document_groups, order_document_spans
from rag_engine.generation.grounded import format_functions, generation_unit_catalog


def source(index, document, position, text, *, version="v1", section="section"):
    return {
        **span(index, text),
        "label": f"E{index}",
        "source": "same-name.txt",
        "document_scope": [document, version],
        "source_order": (position, position, 0),
        "function": (document, section),
        "tags": [],
        "page": position,
    }


def test_original_order_across_sections_preserves_selected_document_rank_and_unit_ids():
    values = [
        source(3, "a", 3, "後半。", section="x"),
        source(4, "b", 1, "別文書。"),
        source(2, "a", 2, "中盤。", section="y"),
        source(1, "a", 1, "前半。", section="x"),
    ]
    ordered = order_document_spans(values)
    assert [value["label"] for value in ordered] == ["E1", "E2", "E3", "E4"]
    assert set(generation_unit_catalog(values).unit_ids) == set(generation_unit_catalog(ordered).unit_ids)
    for model_view in (False, True):
        rendered = format_functions(values, model_view=model_view)
        positions = [rendered.index(f"[E{i}]") for i in range(1, 5)]
        assert positions == sorted(positions)
        for value in values:
            assert rendered.count(f"[/{value['label']}]") == 1


def test_same_name_documents_and_versions_are_separate_and_unknown_identity_is_not_guessed():
    values = [source(1, "a", 2, "A旧。"), source(2, "b", 1, "B。"), source(3, "a", 1, "A新。", version="v2")]
    assert len(document_groups(values)) == 3
    assert order_document_spans(values) == values
    unknown = [span(1, "一。"), span(2, "二。")]
    for value in unknown:
        value["source"] = "same-name.txt"
    assert len(document_groups(unknown)) == 2


def test_short_normal_generation_uses_document_order_without_additional_call():
    values = [source(2, "a", 2, "後半。"), source(3, "b", 1, "別。"), source(1, "a", 1, "前半。")]
    selected, prompt, diagnostic = prepare(values, lambda *args: None)
    assert [value["label"] for value in selected] == ["E1", "E2", "E3"]
    assert [value["id"] for value in json.loads(prompt)] == ["E1", "E2", "E3"]
    assert diagnostic["batch_count"] == 0


def test_whole_document_fits_next_batch_so_it_is_not_split_to_fill_previous_batch():
    values = [
        source(1, "a", 1, "甲" * 1200),
        source(3, "b", 2, "丙" * 1200),
        source(4, "c", 1, "丁" * 1200),
        source(2, "b", 1, "乙" * 1200),
    ]
    seen = []

    def read(system, prompt, schema):
        evidence = json.loads(prompt)["evidence"]
        seen.append([(row["document_group"], body(row)) for row in evidence])
        return schema.model_validate({"findings": [finding(row) for row in evidence]})

    selected, _, diagnostic = prepare(values, read)
    first_pass = [receipt for receipt in diagnostic["batches"] if receipt["pass_index"] == 1]
    assert [[doc["group"] for doc in receipt["documents"]] for receipt in first_pass] == [["D1"], ["D2"], ["D3"]]
    assert [text for _, text in seen[1]] == ["乙" * 1200, "丙" * 1200]
    assert all(
        not doc["continues_in_next"] and not doc["continued_from_previous"] for receipt in first_pass for doc in receipt["documents"]
    )
    assert [group[0]["document_scope"][0] for group in document_groups(selected)] == ["a", "b", "c"]


def test_oversized_document_is_read_completely_in_order_before_the_next_document():
    text = "前提。" + "補足。" * 1700 + "結末。"
    values = [source(1, "a", 1, text), source(2, "b", 1, "別の条件。")]
    seen = []

    def read(system, prompt, schema):
        evidence = json.loads(prompt)["evidence"]
        seen.extend((row["document_group"], body(row)) for row in evidence)
        return schema.model_validate({"findings": [finding(row) for row in evidence]})

    _, _, diagnostic = prepare(values, read)
    assert "".join(text for group, text in seen if group == "D1") == values[0]["text"]
    assert seen[-1][0] == "D2"
    first_pass = [receipt for receipt in diagnostic["batches"] if receipt["pass_index"] == 1]
    own = [doc for receipt in first_pass for doc in receipt["documents"] if doc["group"] == "D1"]
    assert len(own) > 1
    assert not own[0]["continued_from_previous"] and own[0]["continues_in_next"]
    assert own[-1]["continued_from_previous"] and not own[-1]["continues_in_next"]


def test_missing_source_order_retains_actual_order_without_inferring_from_page():
    values = [source(2, "a", 2, "後半。"), source(1, "a", 1, "前半。")]
    values[0].pop("source_order")
    assert order_document_spans(values) == values


def test_oversized_multi_chunk_document_keeps_chunk_sequence_across_batches():
    values = [source(3, "a", 3, "丙" * 1600), source(4, "b", 1, "別。"), source(1, "a", 1, "甲" * 1600), source(2, "a", 2, "乙" * 1600)]
    seen = []

    def read(system, prompt, schema):
        evidence = json.loads(prompt)["evidence"]
        seen.extend((row["document_group"], body(row)) for row in evidence)
        return schema.model_validate({"findings": [finding(row) for row in evidence]})

    prepare(values, read)
    assert "".join(text for group, text in seen if group == "D1") == "甲" * 1600 + "乙" * 1600 + "丙" * 1600
    assert seen[-1] == ("D2", "別。")
