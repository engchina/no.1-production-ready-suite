"""クォータナリ接続の失敗の案内（#5587。実の API は呼ばない）。"""

from __future__ import annotations

import pytest

from pr_anthropic_messages import (
    AnthropicMessagesError,
    anthropic_failure_actions,
    anthropic_failure_hint,
    find_anthropic_error,
)

# OpenAI 互換の接続（OCI Enterprise AI）の案内にだけある語。クォータナリ接続の案内には出さない。
OCI_ONLY_WORDS = ("IAM", "Project OCID", "リージョン", "OCI", "generativeaiapikey", "policy")

# 完了条件の各 kind（401 / 403 / 404 / 429 / 5xx / 時間切れ / 接続の失敗）と、ほかの種類。
CASES = [
    ("authentication", 401, "API key が接続先に受け付けられませんでした"),
    ("authorization", 403, "呼び出す権限がありません"),
    ("model_not_found", 404, "モデル ID が見つからないか"),
    ("request", 404, "Messages API の path が見つかりません"),
    ("request", 400, "推論の量"),
    ("rate_limit", 429, "rate limit"),
    ("unavailable", 529, "一時的に応答できません"),
    ("unavailable", 500, "一時的に応答できません"),
    ("timeout", None, "タイムアウト秒数"),
    ("connection", None, "へ接続できませんでした"),
    ("response_format", None, "Messages API を指しているか"),
    ("refusal", None, "応答を拒否しました"),
    ("incomplete", None, "出力の上限を広げる"),
]
CASE_IDS = [f"{kind}-{status}" for kind, status, _ in CASES]


@pytest.mark.parametrize(("kind", "status_code", "expected"), CASES, ids=CASE_IDS)
def test_actions_by_kind_do_not_mention_oci(
    kind: str, status_code: int | None, expected: str
) -> None:
    actions = anthropic_failure_actions(kind, status_code=status_code)
    assert len(actions) == 1
    assert expected in actions[0]
    for word in OCI_ONLY_WORDS:
        assert word not in actions[0]


def test_endpoint_is_shown_only_when_given() -> None:
    with_endpoint = anthropic_failure_actions("connection", endpoint="https://example.invalid")
    assert "接続先（https://example.invalid）へ接続できませんでした" in with_endpoint[0]
    without = anthropic_failure_actions("connection")
    assert "接続先へ接続できませんでした" in without[0]
    path = anthropic_failure_actions("request", status_code=404, endpoint="https://example.invalid")
    assert "Endpoint URL（https://example.invalid）は base URL まで" in path[0]


def test_unknown_kind_falls_back_to_the_generic_tip() -> None:
    assert "クォータナリ接続の Endpoint URL と API key" in anthropic_failure_actions(None)[0]
    assert anthropic_failure_actions("other") == anthropic_failure_actions(None)


def test_find_and_hint_follow_the_cause_chain() -> None:
    error = AnthropicMessagesError("失敗", kind="authorization", status_code=403)
    try:
        try:
            raise error
        except AnthropicMessagesError as inner:
            raise RuntimeError("製品の層の失敗") from inner
    except RuntimeError as outer:
        wrapped = outer
    assert find_anthropic_error(wrapped) is error
    assert (
        anthropic_failure_hint(wrapped)
        == anthropic_failure_actions("authorization", status_code=403)[0]
    )
    assert find_anthropic_error(ValueError("他の失敗")) is None
    assert anthropic_failure_hint(ValueError("他の失敗")) is None
