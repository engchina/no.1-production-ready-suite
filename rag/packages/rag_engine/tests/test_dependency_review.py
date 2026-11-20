"""分割された対応・条件を原文のまま共有し、補読の範囲と回数を制限する。"""

import json
from threading import Barrier, Lock, get_ident

import pytest
from pydantic import ValidationError
from test_batched_evidence import block, config, finding, span

from rag_engine.generation import batched_evidence as module
from rag_engine.generation.batched_evidence import prepare_batched_evidence
from rag_engine.generation.dependency_review import ReviewCatalog
from rag_engine.generation.input_budget import model_input_measurement
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline


def inputs(*, reverse=False, missing=False):
    values = [
        span(0, "A の接続先は X です。\n" + "補足。" * 700),
        span(1, "X の接続先は Y です。\n" + "補足。" * 700),
        span(2, ("Z の接続先は M です。\n" if missing else "Y の接続先は M です。\n") + "補足。" * 700),
        span(3, "独立した補足。\n" + "補足。" * 700),
    ]
    return list(reversed(values)) if reverse else values


def run(values, read, *, workers=1):
    return prepare_batched_evidence(
        values,
        config(batch_read_workers=workers),
        question="A から M までの接続の対応を説明してください。",
        render=block,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=read,
        unusable_output=lambda error: isinstance(error, ValidationError),
        select_dependencies=True,
    )


def response(schema, values, selected, source_ids=()):
    data = {"findings": [{"unit_ids": finding(value)["unit_ids"]} for value in values if value["source"] in selected]}
    if "source_ids" in schema.model_fields:
        data["source_ids"] = list(source_ids)
    return schema.model_validate(data)


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("workers", [1, 3], ids=["serial", "parallel"])
def test_shared_originals_recover_the_bridge_without_a_planning_call(reverse, workers):
    calls = []

    def read(system, prompt, schema):
        data = json.loads(prompt)
        calls.append(data)
        assert model_input_measurement(config(), system, prompt, schema.model_json_schema())["fits"]
        assert getattr(schema, "_select_only", False)
        assert "answer" not in schema.model_json_schema()["$defs"]["UnitBatchFinding"]["properties"]
        if "review_catalog" in data:
            ids = [item["id"] for item in data["review_catalog"] if item["source"] == "p1"]
            assert "A の接続先は X" in json.dumps(data["known_evidence"], ensure_ascii=False) or reverse
        else:
            ids = []
        selected = {"p0", "p2"}
        if "known_evidence" in data and "review_catalog" not in data:
            known = json.dumps(data["known_evidence"], ensure_ascii=False)
            assert "A の接続先は X" in known and "Y の接続先は M" in known
            selected = {"p1"}
        return response(schema, data["evidence"], selected, ids)

    selected, _, diagnostic = run(inputs(reverse=reverse), read, workers=workers)
    assert all(any(text in value["text"] for value in selected) for text in ("A の接続先は X", "X の接続先は Y", "Y の接続先は M"))
    assert not any(value.get("batch_answer") for value in selected)
    assert diagnostic["dependency_review"]["scope"] == "selected"
    assert diagnostic["dependency_review"]["status"] == "completed"
    assert len(calls) == diagnostic["batch_count"] == 5
    assert [value["source"] for value in calls[-1]["evidence"]] == ["p1"]
    assert [receipt["index"] for receipt in diagnostic["batches"]] == list(range(1, 6))
    assert all(receipt.get("dependency_review", {}).get("combined_evidence_chars", 0) <= 3500 for receipt in diagnostic["batches"])


def test_same_names_and_chunk_ids_do_not_cross_document_versions():
    original = [
        {**span(0, "合成 A。"), "source": "same.txt", "document_scope": ["document", "v1"]},
        {**span(0, "合成 B。"), "source": "same.txt", "document_scope": ["document", "v2"]},
    ]
    catalog = ReviewCatalog.build(original, [original[0]])
    assert len(catalog.entries) == 1
    assert catalog.bind(["R1"]) == frozenset({("document", "v2")})
    model = catalog.reading_model(module._reading_model(original, select_only=True))
    with pytest.raises(ValidationError):
        model.model_validate({"findings": [], "source_ids": ["unknown"]})


def test_current_batch_remains_eligible_for_later_review():
    value = span(0, "合成の前提。\n合成の条件。")
    known = module._slice(value, 0, len("合成の前提。"))
    assert ReviewCatalog.build([value], [known]).entries
    assert ReviewCatalog.build([value], [value]).entries == []


@pytest.mark.parametrize("failure", ["budget", "invalid_plan"], ids=["budget", "invalid_plan"])
def test_unavailable_plan_falls_back_to_pending_originals(monkeypatch, failure):
    measure = module.model_input_measurement
    calls = []

    def limited(settings, system, prompt, schema, **kwargs):
        result = measure(settings, system, prompt, schema, **kwargs)
        return {**result, "fits": False} if failure == "budget" and '"review_catalog"' in prompt else result

    monkeypatch.setattr(module, "model_input_measurement", limited)

    def read(system, prompt, schema):
        data = json.loads(prompt)
        calls.append(data)
        if "review_catalog" in data:
            assert failure == "invalid_plan"
            return schema.model_validate({"findings": [], "source_ids": ["unknown"]})
        selected = {"p0", "p2"} if "known_evidence" not in data else {"p1"}
        return response(schema, data["evidence"], selected)

    selected, _, diagnostic = run(inputs(), read)
    assert diagnostic["dependency_review"]["scope"] == "all_pending"
    assert any("X の接続先は Y" in value["text"] for value in selected)
    assert all(value["pass_index"] <= 2 for value in diagnostic["batches"])
    assert any("known_evidence" in data for data in calls[4:])


def test_empty_plan_adds_no_review_call():
    calls = []

    def read(system, prompt, schema):
        data = json.loads(prompt)
        calls.append(data)
        return response(schema, data["evidence"], {"p0", "p2"})

    _, _, diagnostic = run(inputs(), read)
    assert len(calls) == 4
    assert diagnostic["dependency_review"]["status"] == "not_needed"


def test_final_batch_waits_for_parallel_results_and_context_is_request_local():
    barrier, lock = Barrier(2), Lock()
    threads = set()

    def read(system, prompt, schema):
        data = json.loads(prompt)
        if data["evidence"][0]["source"] in {"p0", "p1"} and "known_evidence" not in data:
            with lock:
                threads.add(get_ident())
            barrier.wait(timeout=5)
        if "review_catalog" in data:
            assert "A の接続先は X" in json.dumps(data["known_evidence"], ensure_ascii=False)
            assert "Y の接続先は M" in json.dumps(data["known_evidence"], ensure_ascii=False)
        return response(schema, data["evidence"], {"p0", "p2"})

    run(inputs(), read, workers=2)
    assert len(threads) == 2


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "cancel"])
def test_review_obeys_the_deadline_and_cancellation(monkeypatch, cancel):
    calls = []
    deadline = Deadline()
    monkeypatch.setattr(module, "remaining_seconds", lambda: 600 if len(calls) < 4 or cancel else 100)

    def read(system, prompt, schema):
        data = json.loads(prompt)
        calls.append(data)
        if "review_catalog" in data:
            if cancel:
                deadline.cancel()
            return response(schema, data["evidence"], {"p0", "p2"}, [item["id"] for item in data["review_catalog"]])
        return response(schema, data["evidence"], {"p0", "p2"})

    with bind_deadline(deadline):
        if cancel:
            with pytest.raises(DeadlineExceeded):
                run(inputs(), read)
        else:
            selected, _, diagnostic = run(inputs(), read)
            assert selected and diagnostic["stop_reason"] == "deadline"
            assert diagnostic["dependency_review"]["status"] == "deadline"
    assert len(calls) == 4


def test_short_input_does_not_add_a_reader_or_planner():
    selected, _, diagnostic = run([span(0, "合成の原文。")], lambda *args: pytest.fail("不要な呼出し"))
    assert selected and diagnostic["batch_count"] == 0


def test_no_room_for_a_complete_chunk_keeps_already_selected_originals():
    values = inputs()
    values[1].update(document_scope=["document", "v1"], document_first_chunk=True)

    def read(system, prompt, schema):
        data = json.loads(prompt)
        findings = []
        for value in data["evidence"]:
            if value["source"] == "p0":
                units = finding(value, last=len(value["text"].splitlines()))["unit_ids"]
                findings.append({"unit_ids": units})
        selected = [item["id"] for item in data.get("review_catalog", []) if item["source"] == "p1"]
        return schema.model_validate({"findings": findings, **({"source_ids": selected} if "source_ids" in schema.model_fields else {})})

    selected, _, diagnostic = run(values, read)
    assert any(value["text"] == values[0]["text"] for value in selected)
    assert diagnostic["fits"]
    assert diagnostic["dependency_review"]["status"] == "budget_exhausted"


def test_review_cannot_add_more_calls_than_the_preceding_reading():
    def read(system, prompt, schema):
        data = json.loads(prompt)
        findings = []
        for value in data["evidence"]:
            if value["source"] == "p0" and "known_evidence" not in data:
                findings.append({"unit_ids": finding(value, last=len(value["text"].splitlines()))["unit_ids"]})
        return schema.model_validate(
            {
                "findings": findings,
                **({"source_ids": list(item["id"] for item in data["review_catalog"])} if "source_ids" in schema.model_fields else {}),
            }
        )

    selected, _, diagnostic = run(inputs(), read)
    review = diagnostic["dependency_review"]
    assert selected and diagnostic["fits"]
    assert review["status"] == "call_limit"
    assert review["calls"] == review["call_limit"]
    assert diagnostic["batch_count"] == 2 * review["call_limit"]
