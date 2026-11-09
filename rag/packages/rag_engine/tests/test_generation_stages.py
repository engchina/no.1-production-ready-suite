"""複数の小問では計画と生成を分け、原文と質問の条件を保持する。"""

import json
from typing import Any

from rag_engine.config import get_settings
from rag_engine.generation import grounded
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
            "generation.system": "## Step 1: 保存済みの指示",
        }
    ):
        assert grounded.generation_system_prompt() == "## Step 1: 保存済みの指示"


LONG_TEXT = "項目Cは処理の条件を定めます。" * 80


MISSING_PLAN = {
    "request_coverage": [{"request_id": "Q9", "evidence_scope": [{"evidence_id": "unknown", "target": "項目", "relation": "direct"}]}]
}


def test_generation_without_guide_requirements_never_shows_guide_coverage() -> None:
    """#4236: 指示にも schema にも、業務ガイドの要件が無い生成には guide_coverage が出ない。"""
    for question in (QUESTION, TEXTS[0] + "について教えてください。"):
        assert "guide_coverage" not in grounded.generation_system_prompt()
        assert "guide_coverage" not in json.dumps(grounded.generation_output_model(question).model_json_schema())
        assert "guide_coverage" in grounded.generation_output_model(question, guided=True).model_json_schema()["properties"]
