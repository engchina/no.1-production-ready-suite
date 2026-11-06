"""非資料の確認と結果を、送達する一組の原文単位として束縛する。"""

import copy
import importlib
import json

import pytest

from rag_engine.evaluation import answer_eval as ev
from rag_engine.evaluation import answer_validation as av
from rag_engine.evaluation.atomic_source_groups import KEY, trusted
from rag_engine.evaluation.final_validation_units import prepare_unit_request


def prepared(items):
    units = importlib.import_module("rag_engine.evaluation.final_validation_units")
    return units.prepare_unit_request({"evidence_items": list(ev._evidence_fragments(items))})


def test_premise_question_and_answer_atomic_including_negative_and_yes():
    for question, answer in [("ノードAですか。", "はい"), ("UTF-8ですか。", "いいえ、UTF-16LEです。")]:
        result = prepared(av.premise_items([{"question": question, "answer": answer}]))
        assert len(result.units) == 1
        unit = next(iter(result.units.values()))
        assert question in unit["text"] and answer in unit["text"]


def test_data_group_over2000_keeps_sql_and_result_together():
    sql = "SELECT " + ("synthetic_field, " * 180) + "tail FROM synthetic_table"
    assert 2000 < len(sql) < 4000
    result = '{"rows":[[17,4]],"truncated":false}'
    items = av.data_result_items([{"source": "合成照会", "question": "返却結果を教えて", "sql": sql, "result": result}])
    assert len(list(ev._evidence_fragments(items))) == 1
    units = prepared(items)
    assert len(units.units) == 1
    assert sql in next(iter(units.units.values()))["text"]
    assert result in next(iter(units.units.values()))["text"]


@pytest.mark.parametrize(
    "result",
    [
        '{"rows":[],"total":0,"truncated":false}',
        '{"rows":[[17]],"total":100,"truncated":true}',
        '{"status":"unanswerable","message":"対象未指定"}',
    ],
)
def test_empty_truncated_unanswerable_are_preserved(result):
    items = av.data_result_items([{"source": "合成照会", "sql": "SELECT 4 FROM synthetic_source", "result": result}])
    u = next(iter(prepared(items).units.values()))
    assert result in u["text"] and "SELECT 4" in u["text"]


def test_sql_claim_still_has_legitimate_source():
    sql = "SELECT 17 FROM synthetic_source"
    u = next(iter(prepared(av.data_result_items([{"sql": sql, "result": "4"}])).units.values()))
    assert sql in u["text"] and "結果: 4" in u["text"]


def test_native_document_and_public_marker_do_not_get_atomic_privilege():
    text = "利用者の答え: 値17です。\n実行した SQL: SELECT 4\n" + ("a" * 2100)
    for extra in [{}, {KEY: "premise"}, {KEY: "data"}]:
        p = prepared([{"id": "premise:1", "source": "合成文書", "text": text, **extra}])
        assert len(p.units) > 1
        assert not trusted({"text": text, **extra})


def test_json_snapshot_does_not_restore_trust_from_string():
    item = av.premise_items([{"question": "Q", "answer": "A"}])[0]
    assert trusted(item)
    restored = json.loads(json.dumps(item))
    assert not trusted(restored)
    # 実producerの再構成は許すが、保存metadata文字列だけから型を復活させない。
    assert trusted(av.premise_items([{"question": "Q", "answer": "A"}])[0])


def test_field_limit_omits_whole_group_not_truncated_text():
    item = av.data_result_items([{"sql": "s" * 4001, "result": "17"}])[0]
    assert item["text"] == "" and item["_atomic_source_omission"] == "producer_field_limit"
    premise = av.premise_items([{"question": "Q", "answer": "a" * 2001}])[0]
    assert premise["text"] == ""


def test_oversized_group_does_not_abort_unrelated_native_evidence():
    group = av.data_result_items([{"sql": "s" * 4001, "result": "17"}])[0]
    native = {"id": "native", "source": "合成文書", "text": "値は31です。"}
    chosen, truncated = av._fit_evidence({"question": "Q", "answer_passages": []}, list(ev._evidence_fragments([group, native])), 100000)
    assert truncated and len(chosen) == 1 and chosen[0]["id"] == "native"


def test_budget_omits_whole_group_and_keeps_later_native():
    group = av.data_result_items([{"sql": "s" * 3800, "result": "4"}])[0]
    native = {"id": "native", "text": "値31", "source": "合成文書"}
    fragments = list(ev._evidence_fragments([group, native]))
    fixed = {"question": "Q", "answer_passages": []}
    budget = next(n for n in range(1000, 100000, 100) if av._fit_evidence(fixed, [fragments[1]], n)[0])
    chosen, truncated = av._fit_evidence(fixed, fragments, budget)
    assert truncated and all(c["id"] != "data:1" for c in chosen) and any(c["id"] == "native" for c in chosen)


def test_recheck_preserves_actual_type_and_fullgroup():
    item = av.premise_items([{"question": "ノードAですか", "answer": "はい"}])[0]
    fragment = next(ev._evidence_fragments([item]))
    p = prepare_unit_request({"evidence_items": [copy.deepcopy(fragment)]})
    assert len(p.units) == 1 and "はい" in next(iter(p.units.values()))["text"]


def test_unanswered_premise_does_not_shift_existing_ids():
    items = av.premise_items([{"question": "未回答", "answer": ""}, {"question": "対象Aですか", "answer": "はい"}])
    assert len(items) == 1 and items[0]["id"] == "premise:1"


def test_native_reuse_is_unchanged_and_premise_id_not_silently_reused():
    from rag_engine.config import get_settings

    text = "装置Aの値は31です。"
    native = {"id": "native1", "source": "合成文書", "text": text}
    r = av.validate_answer_claims(
        "値を教えて", text, [native], get_settings(environ={}, dotenv_path=None), audited={av.audited_line_key(text): ["native1"]}
    )
    assert r["reused_passage_ids"] == ["A1"] and r["claim_checks"][0]["source_id"] == "native1"
    # 非資料はreadableな資料集合とは別。過去の文言だけで再利用しない。
    passages = [{"id": "A1", "text": text}]
    settled = av._settled_passages(text, passages, {av.audited_line_key(text): ["premise:1"]})
    assert settled["A1"] == ["premise:1"]
    assert not set(settled["A1"]) <= {"native1"}


def test_old_split_unit_cannot_be_reused_as_new_atomic_unit():
    item = av.premise_items([{"question": "対象ノードAですか", "answer": "はい"}])[0]
    current = prepared([item])
    untrusted_snapshot = json.loads(json.dumps(item))
    old_split = prepared([untrusted_snapshot])
    assert not set(current.units) & set(old_split.units)
    with pytest.raises(ValueError):
        current.bind([next(iter(old_split.units))])


def test_full_validation_keeps_unrelated_native_and_reports_omission():
    from rag_engine.config import get_settings

    text = "装置Aの値は31です。"
    native = {"id": "native1", "source": "合成文書", "text": text}
    result = av.validate_answer_claims(
        "装置Aの値を教えて",
        text,
        [native],
        get_settings(environ={}, dotenv_path=None),
        audited={av.audited_line_key(text): ["native1"]},
        data_results=[{"sql": "s" * 4001, "result": "4"}],
        caller_citations=[{"start": 0, "end": len(text), "chunk_ids": ["native1"]}],
    )
    assert result["claim_checks"][0]["status"] == "supported"
    assert result["evidence_truncated"] is True
    assert result["atomic_source_omissions"] == [{"source_id": "data:1", "reason": "producer_field_limit"}]
