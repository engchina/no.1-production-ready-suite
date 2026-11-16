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
