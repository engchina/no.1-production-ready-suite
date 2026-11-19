"""上限内は関連度の順の全原文で 1 回生成し、上限を超えれば原文を外さずに分割読取へ渡す（#5098）。読取は決定論の stub。"""

import json
from dataclasses import replace

import pytest
from test_batched_evidence import body

from rag_engine.config import get_settings
from rag_engine.generation.evidence_selection import select_evidence
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded


def span(index, text, start=0):
    return {"evidence_id": f"E{index}", "source_id": f"s{index}", "text": text, "start": start, "end": start + len(text)}


def no_read(system, prompt, schema):
    pytest.fail("上限内では読取を呼ばない")


def select(spans, *, chars=1000, tokens=10000, read=no_read, **kwargs):
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    settings = replace(base, llm_providers=providers, evidence_budget_chars=chars, answer_max_input_tokens=tokens)
    return select_evidence(
        spans,
        settings,
        question="合成の質問",
        render=lambda values: json.dumps(values, ensure_ascii=False),
        evidence_text=lambda values: "".join(value["text"] for value in values),
        tag=list,
        system="合成の指示",
        schema_for=lambda _values: {"type": "object"},
        read=read,
        unusable_output=lambda error: False,
        **kwargs,
    )


def recording_reader(calls):
    """読取の各呼出しの入力の本文（行頭の番号を除く）を記録し、何も選ばない。"""

    def read(system, prompt, schema):
        calls.append([body(value) for value in json.loads(prompt)["evidence"]])
        return schema(findings=[])

    return read


def test_within_limit_passes_every_source_in_relevance_order_without_reading():
    low = span(0, "低い順位の原文。")
    high = span(1, "高い順位の原文。")
    selected, _, diagnostic = select([low, high, dict(high)], source_scores={"s0": 0.2, "s1": 0.9})
    assert selected == [high, low]
    assert diagnostic["selection"] == "relevance_within_budget"
    assert diagnostic["batch_count"] == 0
    assert diagnostic["duplicate_span_count"] == 1
    assert diagnostic["excluded_span_count"] == 0
    assert "reduction" not in diagnostic


def test_over_limit_reads_every_source_without_excluding_any():
    texts = [f"合成の根拠{index}。" + "補足の文。" * 80 for index in range(4)]
    calls: list[list[str]] = []
    _, _, diagnostic = select([span(index, text) for index, text in enumerate(texts)], read=recording_reader(calls))
    assert diagnostic["selection"] == "batched_reading"
    assert diagnostic["batch_count"] == len(calls) >= 2
    assert diagnostic["excluded_span_count"] == 0
    assert diagnostic["read_source_count"] == 4
    assert diagnostic["fits"] is True
    # どの原文も、どこかの組で全文を読む。
    read_texts = [text for call in calls for text in call]
    assert all(text in read_texts for text in texts)


def test_over_limit_splits_a_single_long_source_and_reads_all_of_it():
    original = span(0, "合成の原文です。" * 300, start=120)
    calls: list[list[str]] = []
    _, _, diagnostic = select([original], chars=1000, read=recording_reader(calls))
    assert diagnostic["batch_count"] == len(calls) >= 2
    assert "".join(text for call in calls for text in call) == original["text"]


def test_model_token_budget_is_checked_alongside_character_budget():
    calls: list[list[str]] = []
    _, _, diagnostic = select([span(0, "合成の本文。" * 300)], chars=100000, tokens=1800, read=recording_reader(calls))
    assert diagnostic["batch_count"] >= 2
    assert diagnostic["fits"] is True
    assert diagnostic["estimated_input_tokens"] <= diagnostic["available_input_tokens"]


def test_fixed_input_that_cannot_fit_is_a_technical_failure():
    with pytest.raises(GenerationInputBudgetExceeded) as error:
        select([span(0, "合成の本文。")], tokens=1)
    assert error.value.diagnostic["reason"] == "fixed_input_exceeds_limit"


def test_over_limit_reads_expansion_evidence_first_and_keeps_it_in_the_final_input():
    """#5162・#5098: 印の付いた根拠は最初の組で読み、読取が選ばなくても最終生成の入力に残す。並びは関連度の順。"""
    spans = [span(index, f"合成の根拠{index}。" + "補足の文。" * 80) for index in range(4)]
    scores = {"s0": 0.9, "s1": 0.5, "s2": 0.4}  # s3 は点数の無い検索拡張の根拠

    calls: list[list[str]] = []
    selected, _, _ = select(spans, source_scores=scores, protected_sources={"s3"}, read=recording_reader(calls))
    assert spans[3]["text"] in calls[0]
    assert [value["source_id"] for value in selected] == ["s0", "s3"]

    unprotected_calls: list[list[str]] = []
    unprotected, _, _ = select(spans, source_scores=scores, read=recording_reader(unprotected_calls))
    assert spans[3]["text"] not in unprotected_calls[0]
    assert [value["source_id"] for value in unprotected] == ["s0", "s1"]


def test_reading_findings_keep_their_source_positions_in_the_final_input():
    """読取が選んだ範囲は番号からコードが切り出し、元の出典と絶対位置を保つ（#4090）。"""
    spans = [span(index, f"合成の根拠{index}は値{index}です。\n" + "補足の文。" * 80, start=50) for index in range(4)]

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        unit = values[0]["text"].split("| [", 1)[1].split("]", 1)[0]
        return schema.model_validate({"findings": [{"unit_ids": [unit], "answer": "合成の根拠の値です。"}]})

    selected, prompt, _ = select(spans, read=read)
    excerpts = [value for value in selected if value.get("batch_answer")]
    assert excerpts
    for value in excerpts:
        original = next(item for item in spans if item["source_id"] == value["source_id"])
        assert original["text"][value["start"] - 50 : value["end"] - 50] == value["text"]
    assert "回答候補（evidence_id は根拠のラベル）" in prompt


def document_span(index, document, text, *, first=False):
    """文書の範囲を持つ合成の根拠。first は文書の本文の先頭の chunk の根拠（#5240）。"""
    value = {**span(index, text), "document_scope": [document]}
    return {**value, "document_first_chunk": True} if first else value


def test_first_chunks_join_one_generation_and_are_not_repeated_when_evidence_has_them():
    """#5240: 先頭の chunk は主の根拠と一緒に渡す。主の根拠がその原文を含む文書には重ねて足さない。"""
    spans = [
        document_span(0, "a", "主の根拠A。"),
        document_span(1, "a", "先頭A。", first=True),
        document_span(2, "b", "先頭B。主の根拠B。"),
        document_span(3, "b", "先頭B。", first=True),
    ]
    selected, _, diagnostic = select(spans, source_scores={"s0": 0.9, "s2": 0.5})
    assert sorted(value["source_id"] for value in selected) == ["s0", "s1", "s2"]
    assert diagnostic["selection"] == "relevance_within_budget"
    assert diagnostic["first_chunk_document_count"] == 2
    assert diagnostic["first_chunk_covered_document_count"] == 1
    assert diagnostic["duplicate_span_count"] == 0


def test_every_batch_and_the_final_input_carry_the_first_chunk_of_their_documents():
    """#5240: 予算を超えたら分割読取にし、各組と最終生成に、その組の文書の先頭の chunk を必ず入れる（外さない）。"""
    main = [document_span(index, f"d{index}", f"合成の根拠{index}。" + "補足の文。" * 80) for index in range(3)]
    first = {f"d{index}": document_span(10 + index, f"d{index}", f"合成の文書{index}の前提。", first=True) for index in range(3)}
    calls: list[list[str]] = []
    selected, _, diagnostic = select([*main, *first.values()], read=recording_reader(calls))
    assert diagnostic["selection"] == "batched_reading"
    assert len(calls) >= 2
    for call in calls:
        for index, value in enumerate(main):
            if any(text and text in value["text"] for text in call if not text.startswith("合成の文書")):
                assert call.count(first[f"d{index}"]["text"]) == 1
    final_firsts = [value["text"] for value in selected if value.get("document_first_chunk")]
    documents = {tuple(value["document_scope"]) for value in selected if not value.get("document_first_chunk")}
    assert sorted(final_firsts) == sorted(first[key[0]]["text"] for key in documents)


def test_input_over_the_lower_bound_goes_to_batched_reading_without_a_whole_render(monkeypatch):
    """下限で文字予算を超える原文は、1 回の生成の全体を描画せずに分割読取へ渡す（#4993）。"""
    import rag_engine.generation.evidence_selection as module

    passed = {}

    def batched(spans, settings, **kwargs):
        passed.update(kwargs, spans=spans)
        return list(spans), "", {"fits": True}

    monkeypatch.setattr(module, "prepare_batched_evidence", batched)
    inputs = [span(i, "合成の本文です。" * 50) for i in range(4)]
    rendered = []
    _, _, diagnostic = select_evidence(
        inputs,
        get_settings(environ={}, dotenv_path=None),
        question="合成の質問",
        render=lambda values: rendered.append(len(values)) or json.dumps(values, ensure_ascii=False),
        evidence_text=lambda values: "".join(value["text"] for value in values),
        tag=list,
        system="合成の指示",
        schema_for=lambda _values: {"type": "object"},
        read=no_read,
        unusable_output=lambda error: False,
        evidence_size_lower_bound=lambda values, _stop: 10**9,
    )
    assert diagnostic["selection"] == "batched_reading"
    assert rendered == [0]
    assert passed["evidence_size_lower_bound"] is not None and len(passed["spans"]) == 4


def placed_span(index, document, path, text, *, relations=()):
    """文書の範囲と出典情報（文書名・章節、Wiki の関係）を持つ合成の根拠（#5500・#5512）。"""
    context = {"document_name": document, "section_path": list(path)}
    if relations:
        context["wiki_relations"] = [
            {"relation": "exception", "role_of": "other", "document_name": name, "section_path": list(other)} for name, other in relations
        ]
    return {**span(index, text), "source": document, "document_scope": [document], "source_context": context}


def related_inputs(*, related=True):
    """4 文書の根拠。related なら 1 番目（点数が最も高い）と 4 番目（最も低い）を Wiki の関係で結ぶ。"""
    relation = [("文書D", ["第2章"])] if related else []
    return [
        placed_span(0, "文書A", ["第1章"], "合成の根拠0。" + "補足の文。" * 80, relations=relation),
        placed_span(1, "文書B", ["第1章"], "合成の根拠1。" + "補足の文。" * 80),
        placed_span(2, "文書C", ["第1章"], "合成の根拠2。" + "補足の文。" * 80),
        placed_span(3, "文書D", ["第2章"], "合成の根拠3。" + "補足の文。" * 80),
    ]


RELATED_SCORES = {"s0": 0.9, "s1": 0.5, "s2": 0.4, "s3": 0.3}


def test_wiki_related_evidence_is_read_in_one_batch_and_kept_together_across_the_budget_boundary():
    """#5512: Wiki の関係で結ばれた根拠は、点数で予算の境界をまたぐ並びでも同じ組で読み、補う順でもまとめて残す。"""
    spans = related_inputs()
    calls: list[list[str]] = []
    selected, _, diagnostic = select(spans, source_scores=RELATED_SCORES, read=recording_reader(calls))
    assert diagnostic["selection"] == "batched_reading"
    assert spans[0]["text"] in calls[0] and spans[3]["text"] in calls[0]
    assert [value["source_id"] for value in selected] == ["s0", "s3"]

    # 関係が無ければ、同じ並びで別の組に分かれ、補う順は点数だけで決まる。
    unrelated_calls: list[list[str]] = []
    unrelated, _, _ = select(related_inputs(related=False), source_scores=RELATED_SCORES, read=recording_reader(unrelated_calls))
    assert spans[3]["text"] not in unrelated_calls[0]
    assert [value["source_id"] for value in unrelated] == ["s0", "s1"]


def test_unrelated_documents_keep_their_order_and_a_related_unit_takes_its_first_position():
    """#5512: 関係の無い文書の並びは変えない。単位は最も前の文書の位置に置き、単位の中は与えた順。"""
    from rag_engine.generation.document_inputs import related_documents

    documents = [[value] for value in related_inputs()]
    units = related_documents(documents)
    assert [[document[0]["source_id"] for document in unit] for unit in units] == [["s0", "s3"], ["s1"], ["s2"]]
    plain = [[value] for value in related_inputs(related=False)]
    assert related_documents(plain) == [[document] for document in plain]


def test_a_relation_whose_place_matches_several_documents_links_nothing():
    """#5512: 文書名と章節が同じ根拠が別の文書（別の版など）にもあれば、どちらとも結ばない。子の出典情報でも相手を特定する。"""
    from rag_engine.generation.document_inputs import related_spans

    spans = related_inputs()
    twin = {**placed_span(4, "文書D", ["第2章"], "同名の別の版の根拠。"), "document_scope": ["文書D", "v2"]}
    assert related_spans([*spans, twin]) == [[0], [1], [2], [3], [4]]
    child = {**spans[3], "source_context": {"document_name": "文書D", "section_path": [], "children": [spans[3]["source_context"]]}}
    assert related_spans([spans[0], spans[1], child]) == [[0, 2], [1]]


def test_without_wiki_relations_the_result_is_identical_to_document_and_source_units(monkeypatch):
    """#5512: wiki_relations が無い入力（Wiki を使わないプロファイル）は、文書ごと・原文ごとの単位と同じ組・同じ結果になる。"""
    import rag_engine.generation.batched_evidence as module

    def run():
        calls: list[list[str]] = []
        result = select(related_inputs(related=False), source_scores=RELATED_SCORES, read=recording_reader(calls))
        return calls, result

    current = run()
    monkeypatch.setattr(module, "related_documents", lambda documents: [[document] for document in documents])
    monkeypatch.setattr(module, "related_spans", lambda spans: [[index] for index in range(len(spans))])
    assert current == run()
