"""処理の経過の段の詳細（回答の記録の工程・パイプラインの工程。#2341）。合成の値で確かめる。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.step_details import (
    answer_guardrail_details,
    field_filter_details,
    history_rewrite_details,
    step_details_payload,
)
from app.schemas.history import RunStage, RunStep


def test_payload_reads_new_and_legacy_records_and_drops_empty() -> None:
    assert step_details_payload(
        {
            "fields": [{"label": "件数", "value": "3 件"}, {"label": "空", "value": ""}],
            "lines": ["行"],
        }
    ) == {"fields": [{"label": "件数", "value": "3 件"}], "lines": ["行"]}
    # 旧い記録（#2294）の行の list は行として読む。
    assert step_details_payload(["行 1", 2, "行 2"]) == {"lines": ["行 1", "行 2"]}
    assert step_details_payload({"fields": [], "lines": []}) is None
    assert step_details_payload("壊れた値") is None
    assert step_details_payload(None) is None


def test_payload_bounds_and_redacts() -> None:
    payload = step_details_payload(
        {"lines": [f"行 {index}" for index in range(30)] + ["Bearer abc.def"]}
    )
    assert payload is not None
    assert len(payload["lines"]) == 20
    assert payload["truncated"] is True
    redacted = step_details_payload({"fields": [{"label": "接続", "value": "password=hunter2"}]})
    assert redacted == {"fields": [{"label": "接続", "value": "password=***"}]}


def test_history_rewrite_details() -> None:
    history = [
        SimpleNamespace(role="USER", content="a"),
        SimpleNamespace(role="ASSISTANT", content="b"),
    ]
    assert history_rewrite_details("合成の書き換えた質問", history) == {
        "fields": [
            {"label": "参照した会話", "value": "2 件の発言"},
            {"label": "書き換えた質問", "value": "合成の書き換えた質問"},
        ]
    }
    assert history_rewrite_details("", history) == {
        "fields": [
            {"label": "参照した会話", "value": "2 件の発言"},
            {"label": "結果", "value": "書き換えなし（元の質問のまま）"},
        ]
    }


def test_field_filter_details() -> None:
    conditions = [
        SimpleNamespace(name="項目 A", op="eq", value="値 1"),
        SimpleNamespace(name="項目 B", op="gte", value="10"),
    ]
    assert field_filter_details(conditions) == {
        "fields": [{"label": "読み取った条件", "value": "2 件"}],
        "lines": ["項目 A = 値 1", "項目 B ≥ 10"],
    }
    assert field_filter_details([]) == {
        "fields": [{"label": "読み取った条件", "value": "なし（条件で絞らずに検索します）"}]
    }


@pytest.mark.parametrize(
    ("result", "expected_fields", "expected_lines"),
    [
        (
            SimpleNamespace(
                allowed=True, sanitized_text="回答", findings=[], backend_degraded=False
            ),
            [("結果", "問題なし")],
            [],
        ),
        (
            SimpleNamespace(
                allowed=True,
                sanitized_text="伏せた回答",
                findings=[SimpleNamespace(code="c1", severity="warning", message="指摘 1")],
                backend_degraded=True,
            ),
            [
                ("結果", "警告 1 件（回答は出します）"),
                ("回答の書き換え", "機密らしい箇所を伏せました"),
                ("外部の検査", "使えなかったため、規則の検査だけで確かめました"),
            ],
            ["指摘 1"],
        ),
        (
            SimpleNamespace(
                allowed=False,
                sanitized_text="回答",
                findings=[
                    SimpleNamespace(code="c2", severity="error", message="指摘 2"),
                    SimpleNamespace(code="c2", severity="error", message="指摘 2"),
                ],
                backend_degraded=False,
            ),
            [("結果", "回答を止めました")],
            ["指摘 2"],
        ),
    ],
    ids=["clean", "warning-sanitized-degraded", "blocked"],
)
def test_answer_guardrail_details(result, expected_fields, expected_lines) -> None:
    details = answer_guardrail_details(result, "回答")
    assert details is not None
    assert [(item["label"], item["value"]) for item in details["fields"]] == expected_fields
    assert details.get("lines", []) == expected_lines


def test_history_schema_reads_structured_and_legacy_details() -> None:
    step = RunStep.model_validate(
        {
            "name": "Rerank",
            "details": {"fields": [{"label": "件数", "value": "3 件"}], "lines": ["行"]},
        }
    )
    assert step.details is not None
    assert step.details.fields[0].label == "件数"
    legacy = RunStep.model_validate({"name": "文書検索", "details": ["行 1"]})
    assert legacy.details is not None and legacy.details.lines == ["行 1"]
    assert RunStep.model_validate({"name": "古い工程"}).details is None
    stage = RunStage.model_validate(
        {
            "stage": "answer_guardrail",
            "outcome": "success",
            "details": {"fields": [{"label": "結果", "value": "問題なし"}]},
        }
    )
    assert stage.details is not None and stage.details.fields[0].value == "問題なし"
