"""複数の小問では計画と生成を分け、原文と質問の条件を保持する。"""

import json
from typing import Any

import pytest
from citation_unit_support import generated_fixture, without_unit_annotations
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import generation_draft_wire_model, guided_generation_wire_model
from rag_engine.models.llm import RequestScopePlan
from rag_engine.resources.runtime import bind_prompt_overrides

QUESTION = "同じ版の資料を使ってください。①項目Aの意味は？②項目Bの意味は？"
TEXTS = ["項目Aは入力を区別する識別子です。", "項目Bは処理の状態を示します。"]


def spans() -> list[dict[str, Any]]:
    result = [
        {
            "evidence_id": f"source-{index}",
            "label": f"E{index}",
            "text": text,
            "source": "synthetic.txt",
            "source_id": "doc-a",
            "page": 1,
            "function": ("synthetic.txt", "項目の定義"),
            "origin": "document_text",
            "tags": [],
        }
        for index, text in enumerate(TEXTS, 1)
    ]
    grounded.format_functions(result)
    return result


def run(question: str, parse: Any) -> grounded.Round:
    evidence = spans()
    return grounded.run_round(
        question,
        evidence,
        get_settings(environ={}, dotenv_path=None),
        prompt="原質問: " + question + "\n" + grounded.evidence_block_text(evidence),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=parse,
        audit_mode="none",
    )


def plan(question: str = QUESTION) -> dict[str, Any]:
    return {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [{"evidence_id": eid, "target": "項目", "relation": "direct"} for eid in ("E1", "E2")],
            }
        ]
    }


def draft(requests: list[str]) -> dict[str, Any]:
    return {
        "summary": "項目の定義を示します。",
        "confidence": "high",
        "items": [
            {
                "kind": "rule",
                "request_id": request_id,
                "text": text,
                "citations": [{"evidence_id": f"E{index}", "line_start": 1, "line_end": 1}],
            }
            for index, (request_id, text) in enumerate(zip(requests, TEXTS, strict=True), 1)
        ],
    }


def test_each_generation_stage_uses_its_saved_prompt() -> None:
    with bind_prompt_overrides(
        {
            "generation.system": "## Step 1: 単純な質問の保存済み指示",
            "generation.plannedSystem": "## Step 1: 根拠計画後の保存済み指示",
        }
    ):
        assert grounded.generation_system_prompt(QUESTION) == "## Step 1: 根拠計画後の保存済み指示"
        assert grounded.generation_system_prompt("項目Aの意味は？") == "## Step 1: 単純な質問の保存済み指示"


LONG_TEXT = "項目Cは処理の条件を定めます。" * 80


def long_spans() -> list[dict[str, Any]]:
    result = [{**row, "text": LONG_TEXT if row["label"] == "E1" else row["text"]} for row in spans()]
    grounded.format_functions(result)
    return result


def run_long(parse: Any) -> grounded.Round:
    evidence = long_spans()
    assert len(evidence[0]["text"]) > 600
    return grounded.run_round(
        QUESTION,
        evidence,
        get_settings(environ={}, dotenv_path=None),
        prompt="原質問: " + QUESTION + "\n" + grounded.evidence_block_text(evidence),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=parse,
        audit_mode="none",
        invalid_output=lambda exc: isinstance(exc, ValidationError),
    )


def _scope_failures(parse_plans: list[Any]) -> tuple[Any, list[dict[str, Any]]]:
    """計画の応答を順に返す。生成まで進めば生成の応答を返す。呼び出しの入力を記録する。"""
    prompts: list[dict[str, Any]] = []
    plans = iter(parse_plans)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        if issubclass(schema, RequestScopePlan):
            prompts.append(json.loads(prompt))
            value = next(plans)
            if isinstance(value, Exception):
                raise value
            return RequestScopePlan.model_validate(value)
        return generated_fixture(schema, prompt, draft(["Q1", "Q1"]))

    return parse, prompts


MISSING_PLAN = {
    "request_coverage": [{"request_id": "Q9", "evidence_scope": [{"evidence_id": "unknown", "target": "項目", "relation": "direct"}]}]
}


def _linked_plan(link: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [{"evidence_id": "E1", "target": "項目", "relation": "linked", "link": link}],
            }
        ]
    }


def _direct_draft(scope: list[dict[str, Any]]) -> dict[str, Any]:
    """小問の無い質問の草稿（計画を同じ出力で返す）。"""
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": scope}],
        "items": [
            {
                "kind": "rule",
                "request_id": "Q1",
                "text": TEXTS[0],
                "citations": [{"evidence_id": "E1", "line_start": 1, "line_end": 1}],
            }
        ],
        "summary": TEXTS[0],
        "confidence": "medium",
    }


def _run_direct(*outputs: dict[str, Any]) -> tuple[Any, list[Any]]:
    """小問の無い質問の生成を、番号の計画の応答で 1 回の round だけ通す。"""
    calls: list[Any] = []
    values = iter(outputs)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        calls.append((schema, prompt))
        return generation_draft_wire_model().model_validate(next(values))  # 旧記録の範囲検証を維持。

    return run(TEXTS[0] + "について教えてください。", parse), calls


def test_generation_without_guide_requirements_never_shows_guide_coverage() -> None:
    """#4236: 指示にも schema にも、業務ガイドの要件が無い生成には guide_coverage が出ない。"""
    for question in (QUESTION, TEXTS[0] + "について教えてください。"):
        assert "guide_coverage" not in grounded.generation_system_prompt(question)
        assert "guide_coverage" not in json.dumps(grounded.generation_output_model(question).model_json_schema())
        assert "guide_coverage" in grounded.generation_output_model(question, guided=True).model_json_schema()["properties"]
