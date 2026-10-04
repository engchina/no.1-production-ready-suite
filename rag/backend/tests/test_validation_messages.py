"""入力の検証エラー（422）の日本語のメッセージのテスト（#979）。"""

from typing import Any

import pytest

from app.api.validation_messages import validation_error_message, validation_error_messages
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"cases": [{"id": "a"}]}, "cases[0].query: 必須の項目です。入力してください。"),
        (
            {"cases": [{"id": "a", "query": ""}]},
            "cases[0].query: 1 文字以上で入力してください。",
        ),
        (
            {"cases": [{"id": "a", "query": "q", "relevant_document_ids": "doc"}]},
            "cases[0].relevant_document_ids: 配列（[ ]）で指定してください。",
        ),
        ({"cases": [{"id": "a", "query": "q"}], "top_k": "x"}, "top_k: 整数で入力してください。"),
        (
            {"cases": [{"id": "a", "query": "q"}], "top_k": 0},
            "top_k: 1 以上の値を入力してください。",
        ),
        (
            {"cases": [{"id": "a", "query": "q"}], "thresholds": {"foo": 1}},
            "thresholds.foo: この項目は指定できません。",
        ),
        (
            {"cases": [{"id": "a", "query": "q"}], "suite": "loose"},
            "suite: 指定できる値ではありません（指定できる値: 'standard'、'strict'）。",
        ),
        ({"cases": []}, "cases: 1 件以上指定してください。"),
        ({"cases": "x"}, "cases: 配列（[ ]）で指定してください。"),
    ],
    ids=[
        "missing",
        "too-short",
        "list-type",
        "int-parsing",
        "greater-equal",
        "extra",
        "literal",
        "too-short-list",
        "list-type-root",
    ],
)
def test_evaluation_validation_errors_are_japanese(body: dict[str, Any], expected: str) -> None:
    """JSON を直接書く品質評価の入力の誤りを、JSON の位置と日本語の文で返す。"""
    response = client.post("/api/evaluation/jobs/run", json=body)

    assert response.status_code == 422
    assert expected in response.json()["error_messages"]


def test_custom_value_errors_are_returned_as_written() -> None:
    """自前の検証の日本語のメッセージは、接頭辞と位置を付けずにそのまま返す。"""
    response = client.post(
        "/api/evaluation/compare",
        json={
            "cases": [{"id": "case-1", "query": "承認条件"}],
            "experiments": [{"id": "same"}, {"id": "same"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["error_messages"] == ["experiment id が重複しています: same"]


def test_query_parameter_errors_drop_the_source() -> None:
    response = client.get("/api/feedback?limit=1000")

    assert response.status_code == 422
    assert response.json()["error_messages"] == ["limit: 100 以下の値を入力してください。"]


def test_unknown_types_and_broken_context_fall_back() -> None:
    assert (
        validation_error_message({"type": "new_type", "loc": ("body", "x"), "msg": "Whatever"})
        == "x: 入力内容を確認してください。"
    )
    assert (
        validation_error_message({"type": "string_too_short", "loc": ("body",), "msg": "short"})
        == "入力内容を確認してください。"
    )
    assert validation_error_messages(
        [
            {"type": "missing", "loc": ("body", "a"), "msg": "Field required"},
            {"type": "missing", "loc": ("body", "a"), "msg": "Field required"},
        ]
    ) == ["a: 必須の項目です。入力してください。"]
