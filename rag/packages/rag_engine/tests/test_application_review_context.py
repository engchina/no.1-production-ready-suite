"""引用していない同表の行と別資料の区分規則も推導校正へ渡す。"""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from application_verdict_stub import audit_reply

from rag_engine.config import get_settings
from rag_engine.generation.application_review import application_documents, review_applications
from rag_engine.generation.applications import CheckedApplication
from rag_engine.models.llm import ApplicationVerdictAudit, GroundedApplication

TABLE = "| 条件 | 点 |\n| 属性A=有 | 2 |\n| 属性B=有 | 3 |"
CLASSIFICATION = "0〜2点は区分A、3〜5点は区分Bです。"
QUESTION = "属性Aは有、属性Bは未確定です。確定した合計と区分を教えてください。"


def spans() -> list[dict[str, Any]]:
    return [
        {"evidence_id": "E1", "source_id": "s1", "source": "synthetic-a.txt", "document_scope": ["doc-a", "v1"], "text": TABLE, "page": 1},
        {
            "evidence_id": "E2",
            "source_id": "s2",
            "source": "synthetic-b.txt",
            "document_scope": ["doc-b", "v1"],
            "text": CLASSIFICATION,
            "page": 2,
        },
    ]


def candidate() -> CheckedApplication:
    quote = "| 属性A=有 | 2 |"
    entry = SimpleNamespace(
        item=SimpleNamespace(kind="rule", applies="matched", text=quote, quote=quote),
        span=spans()[0],
        quote_only=False,
        removed=False,
        state="",
    )
    return CheckedApplication(
        GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="未確定の属性Bは加算せず2点とする。", result="合計2点、区分A"),
        [entry],
    )


def test_same_partial_quote_cannot_hide_unknown_row_and_classification() -> None:
    entry = candidate()
    seen: list[dict[str, Any]] = []

    def parse(system: str, prompt: str, settings: Any, schema: type[Any], **options: Any) -> Any:
        if schema is not ApplicationVerdictAudit:
            return audit_reply(schema, reason="未確定の行があるため合計と区分は確定しません。")
        data = json.loads(prompt)
        seen.append(data)
        # 修正前はitemsの抜粋だけでvalidになる再生。固定の資料全体があると未確定行のゼロ扱いを認めない。
        complete = data.get("documents") == application_documents(spans())
        return audit_reply(schema, [{"index": 0, "valid": not complete}], summary_supported=False, prompt=prompt)

    trace = review_applications(
        QUESTION,
        [entry],
        evidence_spans=spans(),
        existing_audit=None,
        audit_requested=False,
        parse_text=parse,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    assert trace["calls"] == 2 and len(seen) == 1
    assert seen[0]["items"][0]["quote"] == "| 属性A=有 | 2 |"
    assert seen[0]["documents"][0]["evidence"][0]["text"] == TABLE
    assert seen[0]["documents"][1]["evidence"][0]["text"] == CLASSIFICATION
    assert not entry.published and entry.assessment["status"] == "invalid"


def test_context_absence_is_unknown_without_model_call() -> None:
    entry = candidate()
    trace = review_applications(
        QUESTION,
        [entry],
        existing_audit=None,
        audit_requested=False,
        parse_text=lambda *a, **kw: pytest.fail("原文範囲がない場合は校正を依頼しない"),
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    assert trace["calls"] == 0 and trace["reason"] == "evidence_context_missing"
    assert not entry.published and entry.assessment["status"] == "unknown"


def test_full_context_budget_overflow_does_not_retry_with_partial_quote() -> None:
    entry = candidate()
    full = spans()
    full[1]["text"] = CLASSIFICATION * 1000
    config = replace(get_settings(environ={}, dotenv_path=None), answer_max_input_tokens=100)
    trace = review_applications(
        QUESTION,
        [entry],
        evidence_spans=full,
        existing_audit=None,
        audit_requested=False,
        parse_text=lambda *a, **kw: pytest.fail("全原文が予算外なら送信しない"),
        settings=config,
        provider_id=None,
    )
    assert trace["calls"] == 0 and trace["error_type"] == "GenerationInputBudgetExceeded"
    assert not entry.published and entry.assessment["status"] == "unknown"


def test_document_groups_preserve_versions_and_source_order_without_truncation() -> None:
    full = spans()
    first = {**full[0], "source_order": [1], "text": "先頭"}
    last = {**full[0], "source_order": [2], "text": "末尾" * 20000}
    other_version = {**first, "document_scope": ["doc-a", "v2"], "text": "別版"}
    documents = application_documents([last, full[1], first, other_version])
    assert [doc["document_scope"] for doc in documents] == [["doc-a", "v1"], ["doc-b", "v1"], ["doc-a", "v2"]]
    assert [value["text"] for value in documents[0]["evidence"]] == [first["text"], last["text"]]
    assert documents[0]["evidence"][1]["source_id"] == "s1"


def test_document_background_keeps_its_non_citable_role_and_completeness() -> None:
    background = {
        **spans()[0],
        "origin": "document_background",
        "document_background_only": True,
        "background_ref": {"truncated": True},
    }
    value = application_documents([background])[0]["evidence"][0]
    assert value["text"] == TABLE
    assert value["origin"] == "document_background"
    assert value["document_background_only"] is True
    assert value["background_ref"] == {"truncated": True}
    assert "先頭ページ背景" in value["role"]
    assert "引用対象外" in value["role"]
    assert "欠落のある旧記録" in value["role"]
