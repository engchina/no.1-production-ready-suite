"""業務支援の評価を Agent の Run として流す CLI（#1289）。Agent は公開の MCP の台本（#3240）。

Agent の MCP（`POST /api/mcp`）は MockTransport の台本で、出力は契約
（`platform/contracts/mcp/agent-tools.json`）の形の合成の値。Run の内部の記録から公開の出力を作る
規則（根拠の重複の除去・読み取りのツールの chunk・本文のつなぎ方）は Agent のテスト
（`agent/backend/tests/test_run_observation.py`）が確かめる。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest

from app.rag.agent_evaluation_cli import (
    AGENT_EVALUATION_METRICS_VERSION,
    DEFAULT_API_KEY_ENV,
    FORBIDDEN_ACTION_REASON,
    MCP_PROTOCOL_VERSION,
    OBSERVATION_PAGE_LIMIT,
    OBSERVATION_SCHEMA_VERSION,
    AgentMcp,
    agent_turn,
    evaluate,
    evidence_citations,
    known_conditions_text,
    main,
    summarize_result,
    summary_markdown,
    superseded_case_summary,
)
from app.schemas.evaluation import EvaluationFailureReason, EvaluationRunRequest

THREAD = "thread_" + "a" * 32
API_KEY = "prak_synthetic"


def _evidence(document_id: str, chunk_id: str, text: str, **fields: Any) -> dict[str, Any]:
    """観測の根拠（`agent_get_run_observation` の `evidence[]`）。"""
    return {
        "rank": 0,
        "tool_call_index": 0,
        "source": "search",
        "document_id": document_id,
        "chunk_id": chunk_id,
        "file_name": "manual.pdf",
        "chunk_set_id": None,
        "evidence_type": "text",
        "origin": "source",
        "superseded": False,
        "retrieval_rank": 1,
        "cited": False,
        "answer_use": None,
        "locator": {
            "section_path": ["3. アクセス権限の付与"],
            "page_start": 1,
            "page_end": 1,
            "page_label_start": None,
            "page_label_end": None,
            "sheet_name": None,
            "row_start": None,
            "row_end": None,
            "cell_range": None,
            "element_locator": None,
        },
        "text": text,
        "text_length": len(text),
        "text_omitted": False,
        "truncated": False,
        **fields,
    }


def _call(
    name: str,
    *,
    status: str = "completed",
    include_superseded: bool | None = None,
) -> dict[str, Any]:
    """観測のツールの呼び出し（`tool_calls[]`）。"""
    return {
        "index": 0,
        "call_id": "step-1",
        "name": f"rag__{name}",
        "status": status,
        "error_code": None,
        "started_at": None,
        "completed_at": None,
        "evidence_count": None,
        "include_superseded": include_superseded,
        "job_status": None,
        "row_count": None,
        "rows_truncated": None,
    }


def _run(
    run_id: str,
    *,
    answer: str,
    status: str = "completed",
    outcome: str | None = None,
    thread_id: str = THREAD,
    skipped: list[str] | None = None,
) -> dict[str, Any]:
    """`agent_chat` / `agent_get_run` の出力（RunOutput）。"""
    return {
        "schema_version": 4,
        "run_id": run_id,
        "agent_id": "agent-1",
        "thread_id": thread_id,
        "status": status,
        "outcome": outcome if status == "completed" else None,
        "in_progress": status in {"queued", "running"},
        "answer": answer,
        "confirmation": None,
        "sources": [],
        "sources_text_omitted": 0,
        "withheld_evidence_count": 0,
        "passage_marks": [],
        "removed_passages": [],
        "answer_warnings": [],
        "tools_used": [],
        "skipped_approval_tools": skipped or [],
        "pending_approvals": 0,
        "message": "合成の案内",
        "notice": None,
    }


def _observation(
    run_id: str,
    *,
    evidence: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """`agent_get_run_observation` の出力（RunObservation）。"""
    items = evidence or []
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "run_id": run_id,
        "agent_id": "agent-1",
        "thread_id": THREAD,
        "agent_version": 3,
        "trace_id": None,
        "status": "completed",
        "outcome": None,
        "validation_status": "completed",
        "created_at": "2026-10-25T01:00:00Z",
        "finished_at": "2026-10-25T01:01:00Z",
        "usage": {"model": None, "requests": 3, "input_tokens": 0, "output_tokens": 0},
        "tool_calls": tool_calls or [],
        "tool_call_total": len(tool_calls or []),
        "evidence": items,
        "evidence_total": len(items),
        "withheld_evidence_count": 0,
        "next_cursor": None,
        **fields,
    }


class FakeAgentMcp:
    """`agent_chat` で台本の Run を順に返し、`agent_get_run` は台本の状態の列を返す。

    台本は 1 Run ごとに（状態の列, 観測）。状態の列の先頭を `agent_chat` が返し、残りを
    `agent_get_run` が順に返す（最後の状態を返し続ける）。
    """

    def __init__(self, scripts: list[tuple[list[dict[str, Any]], dict[str, Any] | None]]) -> None:
        self.scripts = scripts
        self.chats: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self.methods: list[str] = []
        self.polls: dict[str, list[dict[str, Any]]] = {}
        self.observations: dict[str, dict[str, Any] | None] = {}
        self.overrides: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        message = json.loads(request.content)
        self.methods.append(message["method"])
        if message["method"] == "initialize":
            return self._reply(message, {"protocolVersion": MCP_PROTOCOL_VERSION})
        tool = message["params"]["name"]
        arguments = message["params"]["arguments"]
        self.calls.append((tool, arguments))
        if tool in self.overrides:
            return self._reply(message, self.overrides[tool])
        if tool == "agent_chat":
            self.chats.append(arguments)
            states, observation = self.scripts[len(self.chats) - 1]
            run_id = states[0]["run_id"]
            self.polls[run_id] = list(states[1:]) or [states[0]]
            self.observations[run_id] = observation
            return self._result(message, states[0])
        run_id = arguments["run_id"]
        if tool == "agent_cancel_run":
            self.cancelled.append(run_id)
            return self._result(
                message,
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "status": "cancelled",
                    "result": "cancelled",
                    "message": "実行を取り消しました。",
                },
            )
        if tool == "agent_get_run_observation":
            observation = self.observations[run_id]
            assert observation is not None
            return self._result(message, observation)
        queue = self.polls[run_id]
        state = queue[0] if len(queue) == 1 else queue.pop(0)
        return self._result(message, state)

    @staticmethod
    def _reply(message: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _result(self, message: dict[str, Any], content: dict[str, Any]) -> httpx.Response:
        return self._reply(
            message,
            {
                "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
                "structuredContent": content,
                "isError": False,
            },
        )


def _api(fake: FakeAgentMcp, clock: Callable[[], float] | None = None) -> AgentMcp:
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return AgentMcp(
        client, "http://agent/api/mcp", api_key=API_KEY, **({"clock": clock} if clock else {})
    )


def _evaluate(
    fake: FakeAgentMcp,
    cases: list[dict[str, Any]],
    *,
    labels: dict[str, str] | None = None,
    timeout: float = 60,
    api_clock: Callable[[], float] | None = None,
) -> Any:
    clock_kwargs: dict[str, Any] = {"api_clock": api_clock} if api_clock else {}
    return evaluate(
        _api(fake, api_clock),
        _request(cases),
        agent_id="agent-1",
        labels=labels or {},
        run_timeout_seconds=timeout,
        log=lambda _: None,
        **clock_kwargs,
    )


def _request(cases: list[dict[str, Any]]) -> EvaluationRunRequest:
    return EvaluationRunRequest.model_validate({"cases": cases})


GRANT_CASE: dict[str, Any] = {
    "id": "mt-grant",
    "category": "clarification_required",
    "split": "dev",
    "query": "アクセス権限を付けたいのですが、手順を教えてください。",
    "relevant_document_ids": ["doc-manual"],
    "expected_outcomes": ["needs_clarification", "conditional"],
    "required_conditions": ["個別", "グループ"],
    "forbidden_phrases": ["グループに付与してください"],
    "turns": [
        {
            "reply": "個別の利用者に付けます。",
            "conditions": {"target": "個別"},
            "expected_outcomes": ["answered", "conditional"],
            "required_conditions": ["詳細画面"],
        }
    ],
    "required_evidence": [
        {
            "id": "grant-individual",
            "document_id": "doc-manual",
            "text": "個別の利用者に付与する場合",
        }
    ],
}


def test_multi_turn_case_follows_the_thread_and_uses_the_rag_scoring() -> None:
    clarify = _run(
        "run-1",
        answer="付与先を確かめます。\n権限の付与先は、個別の利用者ですか、グループですか？",
        outcome="needs_clarification",
    )
    answered = _run(
        "run-2",
        answer="個別の利用者に付与する場合は、利用者の詳細画面の「権限」タブで付与します。",
        outcome="answered",
    )
    fake = FakeAgentMcp(
        [
            # 1 往復目は待ちの時間内に終わらず、agent_get_run で続きを読む。
            (
                [{**clarify, "status": "running", "in_progress": True, "outcome": None}, clarify],
                _observation("run-1", tool_calls=[_call("rag_search")]),
            ),
            (
                [answered],
                _observation(
                    "run-2",
                    evidence=[
                        _evidence(
                            "doc-manual", "c1", "個別の利用者に付与する場合: 利用者の詳細画面"
                        )
                    ],
                    tool_calls=[_call("rag_search")],
                ),
            ),
        ]
    )
    metrics, records = _evaluate(fake, [GRANT_CASE])

    # 返答は同じ会話の次の agent_chat として送る。
    assert [{k: v for k, v in chat.items() if k != "wait_seconds"} for chat in fake.chats] == [
        {"agent_id": "agent-1", "question": GRANT_CASE["query"]},
        {"agent_id": "agent-1", "question": "個別の利用者に付けます。", "thread_id": THREAD},
    ]
    assert [tool for tool, _ in fake.calls] == [
        "agent_chat",
        "agent_get_run",
        "agent_get_run_observation",
        "agent_chat",
        "agent_get_run_observation",
    ]
    result = metrics.case_results[0]
    assert result.status == "success"
    assert result.response_decision_accuracy == 1
    assert result.observed_outcome == "answered"
    assert result.outcome_source == "agent_answer"
    assert [turn.observed_outcome for turn in result.turn_results] == [
        "needs_clarification",
        "answered",
    ]
    assert all(turn.outcome_source == "agent_answer" for turn in result.turn_results)
    assert result.retrieved_document_ids == ["doc-manual"]
    assert result.required_evidence_recall == 1.0
    assert "unexpected_handling" not in result.failure_reasons
    assert metrics.response_decision_accuracy == 1.0
    assert metrics.split_breakdown["dev"].case_count == 1
    # Agent の経路の採点の版（RAG の六指標の版 + Agent の経路の規則の版。#3240）。
    assert metrics.metrics_version == AGENT_EVALUATION_METRICS_VERSION
    assert [
        (record.run_id, record.validation_status, record.model_requests, record.agent_version)
        for record in records
    ] == [("run-1", "completed", 3, 3), ("run-2", "completed", 3, 3)]
    assert records[1].tool_calls == {"rag_search": 1}


def test_requests_follow_the_mcp_transport_with_the_api_key() -> None:
    """API キーの Bearer・MCP-Protocol-Version で呼び、最初に 1 回だけ initialize する。"""
    fake = FakeAgentMcp(
        [([_run("run-1", answer="答え", outcome="answered")], _observation("run-1"))] * 2
    )
    _evaluate(fake, [{"id": "a", "query": "q1"}, {"id": "b", "query": "q2"}])
    assert fake.methods[0] == "initialize"
    assert fake.methods.count("initialize") == 1
    assert {headers["authorization"] for headers in fake.headers} == {f"Bearer {API_KEY}"}
    assert {headers["mcp-protocol-version"] for headers in fake.headers} == {MCP_PROTOCOL_VERSION}
    # 待ちは MCP の wait_seconds（契約の上限 300 秒）に任せる。
    assert all(1 <= chat["wait_seconds"] <= 300 for chat in fake.chats)


def test_superseded_cases_record_whether_the_agent_searched_old_versions() -> None:
    """旧版も検索して答えるケース（#1366）で、Agent が include_superseded を渡したか（#1392）。"""
    searched = _observation(
        "run-1",
        tool_calls=[
            _call("rag_retrieve_evidence", include_superseded=False),
            _call("rag_retrieve_evidence", include_superseded=True),
            # 失敗した呼び出しと、根拠のツール以外は数えない。
            _call("rag_search", status="failed", include_superseded=True),
            _call("rag_read_source"),
        ],
    )
    missed = _observation("run-2", tool_calls=[_call("rag_search", include_superseded=False)])
    cases: list[dict[str, Any]] = [
        {"id": "versions-a", "query": "期限は旧版から変わった？", "include_superseded": True},
        {"id": "versions-b", "query": "保守枠の改定前との違いは？", "include_superseded": True},
        {"id": "current", "query": "期限は？"},
    ]
    fake = FakeAgentMcp(
        [
            ([_run("run-1", answer="旧版は 5 営業日、今の版は 3 営業日です。")], searched),
            ([_run("run-2", answer="今の版の保守枠は月 1 回です。")], missed),
            ([_run("run-3", answer="今の版の期限は 3 営業日です。")], _observation("run-3")),
        ]
    )
    _, records = _evaluate(fake, cases)

    assert [record.superseded_searches for record in records] == [1, 0, 0]
    assert records[0].as_json()["superseded_searches"] == 1
    assert records[0].tool_calls == {
        "rag_retrieve_evidence": 2,
        "rag_search": 1,
        "rag_read_source": 1,
    }
    assert superseded_case_summary(_request(cases).cases, records) == {
        "case_count": 2,
        "searched_case_ids": ["versions-a"],
        "missed_case_ids": ["versions-b"],
    }


def test_known_conditions_are_added_to_the_first_question_with_guide_labels() -> None:
    case = {
        "id": "known",
        "query": "アクセス権限を付与したいです。どの画面で操作しますか？",
        "conditions": {"target": "グループ"},
        "expected_outcomes": ["answered", "conditional"],
        "required_conditions": ["承認"],
    }
    run = _run("run-1", answer="部門長の承認を得てから、グループの詳細画面で付与します。")
    observation = _observation(
        "run-1", evidence=[_evidence("doc-manual", "c2", "付与の前に部門長の承認を得てください")]
    )
    fake = FakeAgentMcp([([run], observation)])
    metrics, _ = _evaluate(fake, [case], labels={"target": "付与先"})
    assert fake.chats[0]["question"].endswith("（既知の条件: 付与先は「グループ」）")
    assert known_conditions_text({"approved": "はい"}, {}) == "（既知の条件: approvedは「はい」）"
    result = metrics.case_results[0]
    # 対応の無い完了の Run は、RAG の評価と同じ推定（根拠があり拒答の文でなければ answered）。
    assert result.observed_outcome == "answered"
    assert result.outcome_source == "inferred"
    assert result.requirement_coverage is None
    # 根拠の無い回答は、RAG の評価と同じく拒答とみなす（対応を渡さない）。
    bare = agent_turn(
        _run("run-9", answer="グループの詳細画面です。"), _observation("run-9"), elapsed_ms=1.0
    )
    assert bare.response.diagnostics.answer is not None
    assert "outcome" not in bare.response.diagnostics.answer


def test_answer_ending_with_a_question_without_an_outcome_is_a_clarification() -> None:
    turn = agent_turn(
        _run("run-1", answer="確認させてください。\n部門長の承認は得ていますか？"),
        _observation("run-1"),
        elapsed_ms=10.0,
    )
    assert turn.response.diagnostics.answer is not None
    assert turn.response.diagnostics.answer["outcome"] == "needs_clarification"
    assert turn.outcome_source == "inferred"
    assert turn.response.trace_id == "run-1"


def test_outcome_of_the_public_run_output_is_used_for_scoring() -> None:
    # ed-current-session-timeout: rag_search を呼ばない Run も、Agent が回答に付けた対応で採点する
    # （推定は「回答」と取り違えた。#1305）。
    case = {
        "id": "ed-current-session-timeout",
        "category": "environment_data_required",
        "query": "今のセッションの有効期限は何分ですか？",
        "expected_outcomes": ["needs_environment_data", "conditional"],
    }
    run = _run(
        "run-1",
        answer="既定値は 30 分です。\n今の設定値は資料からは確認できません。",
        outcome="conditional",
    )
    fake = FakeAgentMcp([([run], _observation("run-1"))])
    metrics, records = _evaluate(fake, [case])
    result = metrics.case_results[0]
    assert (result.observed_outcome, result.outcome_source) == ("conditional", "agent_answer")
    assert result.response_decision_accuracy == 1
    assert (records[0].outcome, records[0].outcome_source) == ("conditional", "agent_answer")


def test_outcome_outside_the_vocabulary_is_not_used() -> None:
    """語彙に無い対応は使わず、回答の文から推定する（rag_search の対応は公開しない）。"""
    turn = agent_turn(
        _run("run-1", answer="付与先は個別の利用者ですか？", outcome="maybe"),
        _observation("run-1"),
        elapsed_ms=1.0,
    )
    assert turn.outcome_source == "inferred"
    assert turn.response.diagnostics.answer is not None
    assert turn.response.diagnostics.answer["outcome"] == "needs_clarification"
    # 確認の質問で聞いた条件（rag_search の clarifications）は公開しないので渡さない。
    assert "clarifications" not in turn.response.diagnostics.answer


def test_inference_ignores_the_notices_of_the_final_validation() -> None:
    # 対応を持たない Run（#1305 より前）の推定は、最終の検証が足した注記を除いて最後の行を見る。
    turn = agent_turn(
        _run(
            "run-1",
            answer=(
                "付与先は個別の利用者ですか、グループですか？\n\n"
                "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。"
            ),
        ),
        _observation("run-1"),
        elapsed_ms=1.0,
    )
    assert turn.response.diagnostics.answer is not None
    assert turn.response.diagnostics.answer["outcome"] == "needs_clarification"
    assert turn.outcome_source == "inferred"


def test_failed_timed_out_and_waiting_runs_are_case_errors() -> None:
    failed = _run("run-1", answer="", status="failed")
    running = _run("run-2", answer="", status="running")
    waiting = _run("run-3", answer="", status="waiting_confirmation")
    fake = FakeAgentMcp([([failed], None), ([running], None), ([waiting], None)])
    ticks = iter(range(0, 10_000, 100))
    metrics, records = _evaluate(
        fake,
        [
            {"id": "a", "query": "q1", "expected_outcomes": ["answered"]},
            {"id": "b", "query": "q2", "expected_outcomes": ["answered"]},
            {"id": "c", "query": "q3", "expected_outcomes": ["answered"]},
        ],
        timeout=150,
        api_clock=lambda: float(next(ticks)),
    )
    assert metrics.error_count == 3
    assert metrics.passed is False
    assert [result.error_type for result in metrics.case_results] == [
        "AgentRunFailed",
        "AgentRunTimeout",
        "AgentRunWaitingConfirmation",
    ]
    assert metrics.failure_reason_counts == {"case_error": 3}
    # 待ちきれなかった Run と、回答前の確認で止まった Run は取り消す（結果を残す）。
    assert fake.cancelled == ["run-2", "run-3"]
    assert [record.status for record in records] == ["failed", "timeout", "waiting_confirmation"]
    assert [record.cancel_result for record in records] == [None, "cancelled", "cancelled"]
    # 終わらなかった Run の観測は読まない（採点しない）。
    assert "agent_get_run_observation" not in [tool for tool, _ in fake.calls]


def test_run_that_skipped_an_approval_tool_is_a_case_error() -> None:
    """承認の要るツールを呼ばずに答えた Run はケースの失敗（#3240 の Q7）。"""
    run = _run("run-1", answer="記録しました。", outcome="answered", skipped=["ext__write"])
    fake = FakeAgentMcp([([run], _observation("run-1"))])
    metrics, records = _evaluate(
        fake, [{"id": "a", "query": "q", "expected_outcomes": ["answered"]}]
    )
    result = metrics.case_results[0]
    assert (result.status, result.error_type, result.error_stage) == (
        "error",
        "AgentRunApprovalSkipped",
        "turn_0",
    )
    assert result.error_message is not None and "ext__write" in result.error_message
    assert records[0].skipped_approval_tools == ["ext__write"]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {
                "content": [{"type": "text", "text": "上限です。"}],
                "structuredContent": {"error_code": "API_KEY_RUN_LIMIT", "message": "上限です。"},
                "isError": True,
            },
            "API_KEY_RUN_LIMIT",
        ),
        (
            {
                "content": [],
                "structuredContent": {**_run("run-1", answer="答え"), "schema_version": 99},
                "isError": False,
            },
            "schema_version=99",
        ),
    ],
    ids=["tool-error", "unknown-schema-version"],
)
def test_tool_errors_and_unknown_output_versions_are_case_errors(
    override: dict[str, Any], expected: str
) -> None:
    """ツールのエラーと、知らない形の版の出力は採点せずケースの失敗にする。"""
    fake = FakeAgentMcp([])
    fake.overrides["agent_chat"] = override
    metrics, records = _evaluate(fake, [{"id": "a", "query": "q"}])
    result = metrics.case_results[0]
    assert (result.status, result.error_type) == ("error", "AgentEvaluationError")
    assert result.error_message is not None and expected in result.error_message
    assert records == []


def test_observation_pages_are_followed_until_the_last_cursor() -> None:
    """観測の根拠は next_cursor をたどって全件を読む（1 回に契約の上限の件数）。"""
    first = _observation(
        "run-1",
        evidence=[_evidence("doc-1", "c-1", "本文 1")],
        next_cursor=base64.urlsafe_b64encode(b"page-2").decode(),
    )
    second = _observation("run-1", evidence=[_evidence("doc-2", "c-2", "本文 2")])
    second["evidence"][0]["rank"] = 2
    pages = iter([first, second])
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        requests.append(message["params"])
        content = next(pages)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"structuredContent": content, "isError": False},
            },
        )

    api = AgentMcp(
        httpx.Client(transport=httpx.MockTransport(handler)), "http://a/api/mcp", api_key=API_KEY
    )
    api._initialized = True  # noqa: SLF001 - ページのたどり方だけを確かめる
    observation = api.observation("run-1")
    assert [params["arguments"] for params in requests] == [
        {"run_id": "run-1", "limit": OBSERVATION_PAGE_LIMIT},
        {"run_id": "run-1", "limit": OBSERVATION_PAGE_LIMIT, "cursor": first["next_cursor"]},
    ]
    assert [(item.document_id, item.text) for item in evidence_citations(observation)] == [
        ("doc-1", "本文 1"),
        ("doc-2", "本文 2"),
    ]
    assert observation["next_cursor"] is None


def test_summary_compares_results_in_a_markdown_table() -> None:
    data = {
        "case_count": 2,
        "error_count": 0,
        "response_decision_accuracy": 0.5,
        "claim_support_rate": 0.5,
        "case_results": [
            {"case_id": "a", "status": "success", "elapsed_ms": 1000.0, "failure_reasons": []},
            {
                "case_id": "b",
                "status": "success",
                "elapsed_ms": 3000.0,
                "failure_reasons": ["forbidden_action"],
            },
            # 失敗したケースは危険な回答に数えない。
            {
                "case_id": "c",
                "status": "error",
                "elapsed_ms": 500.0,
                "failure_reasons": ["case_error", "forbidden_action"],
            },
        ],
        "split_breakdown": {
            "holdout": {
                "case_count": 1,
                "error_count": 0,
                "metrics": {"response_decision_accuracy": 0.0},
            }
        },
        # 評価の結果（EvaluationCategorySummary）の形: 分類の指標は metrics の下にある。
        "category_breakdown": {
            "clarification_required": {
                "case_count": 2,
                "error_count": 0,
                "metrics": {"response_decision_accuracy": 0.5},
            }
        },
    }
    summary = summarize_result(data)
    assert summary["median_elapsed_seconds"] == 2.0
    assert summary["dangerous_case_count"] == 1
    assert summary["dangerous_case_ids"] == ["b"]
    assert summary["failed_case_ids"] == ["c"]
    assert summary["splits"]["holdout"]["response_decision_accuracy"] == 0.0
    assert summary["categories"]["clarification_required"]["handling_correct_rate"] == 0.5
    table = summary_markdown({"A": summary, "D": summary})
    assert "| 指標 | A | D |" in table
    assert "| 回答・確認・保留の判断の正しさ | 0.50 | 0.50 |" in table
    assert "| 危険な回答のケース | 1 | 1 |" in table
    assert "| holdout: 回答・確認・保留の判断の正しさ | 0.00 | 0.00 |" in table
    assert "| clarification_required: 回答・確認・保留の判断の正しさ | 0.50 | 0.50 |" in table
    assert "| 1 問の時間の中央値（秒） | 2.00 | 2.00 |" in table


def test_summary_marks_dangerous_cases_unmeasured_without_forbidden_action() -> None:
    """今の採点は勧めてはいけない操作を測らないため、該当が無ければ 0 ではなく測れない（-）。"""
    data = {
        "case_count": 1,
        "error_count": 0,
        "case_results": [
            {"case_id": "a", "status": "success", "elapsed_ms": 1000.0, "failure_reasons": []}
        ],
    }
    summary = summarize_result(data)
    assert summary["dangerous_case_count"] is None
    assert summary["dangerous_case_ids"] == []
    assert "| 危険な回答のケース | - |" in summary_markdown({"D": summary})


def test_forbidden_action_reason_is_in_the_failure_reason_vocabulary() -> None:
    assert FORBIDDEN_ACTION_REASON in get_args(EvaluationFailureReason)


def _golden(tmp_path: Path) -> Path:
    golden = tmp_path / "set.json"
    golden.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "a", "split": "dev", "query": "q", "expected_outcomes": ["answered"]},
                    {
                        "id": "b",
                        "split": "holdout",
                        "query": "q",
                        "expected_outcomes": ["answered"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return golden


def test_main_runs_a_published_agent_through_the_mcp_and_writes_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeAgentMcp(
        [([_run("run-1", answer="答え", outcome="answered")], _observation("run-1"))]
    )
    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        kwargs.pop("trust_env", None)
        return real_client(transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setenv(DEFAULT_API_KEY_ENV, API_KEY)
    output = tmp_path / "out.json"
    code = main(
        [
            "run",
            str(_golden(tmp_path)),
            "--agent-mcp-url",
            "http://agent/api/mcp",
            "--agent-id",
            "agent-eval",
            "--split",
            "dev",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["agent"]["agent_id"] == "agent-eval"
    assert payload["agent"]["agent_versions"] == [3]
    assert payload["data"]["case_count"] == 1
    assert payload["data"]["metrics_version"] == AGENT_EVALUATION_METRICS_VERSION
    assert payload["agent"]["runs"][0]["run_id"] == "run-1"
    assert payload["agent"]["runs"][0]["outcome_source"] == "agent_answer"
    assert payload["agent"]["outcome_sources"] == {"agent_answer": 1}
    assert payload["agent"]["superseded_cases"] == {
        "case_count": 0,
        "searched_case_ids": [],
        "missed_case_ids": [],
    }
    assert fake.chats[0]["agent_id"] == "agent-eval"
    # 業務 Agent の作成・公開はしない（MCP のツールは持たない。#3240 の Q4）。
    assert {tool for tool, _ in fake.calls} <= {
        "agent_chat",
        "agent_get_run",
        "agent_get_run_observation",
        "agent_cancel_run",
    }

    code = main(["summarize", f"D={output}"])
    assert code == 0
    assert "| 指標 | D |" in capsys.readouterr().out


def test_main_requires_the_api_key_and_an_existing_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
    golden = _golden(tmp_path)
    output = tmp_path / "o.json"
    assert main(["run", str(golden), "--agent-id", "x", "--output", str(output)]) == 2
    assert DEFAULT_API_KEY_ENV in capsys.readouterr().err
    assert not output.exists()
    # 業務 Agent を作る口（段 1 の --create-agent）は無い。
    with pytest.raises(SystemExit):
        main(["run", str(golden), "--create-agent", "--output", str(output)])


def test_main_rejects_unresolved_file_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "set.json"
    golden.write_text(
        json.dumps({"cases": [{"id": "a", "query": "q", "relevant_document_ids": ["file:m.pdf"]}]}),
        encoding="utf-8",
    )
    code = main(["run", str(golden), "--agent-id", "x", "--output", str(tmp_path / "o.json")])
    assert code == 2
    assert "file:" in capsys.readouterr().err


def test_observed_evidence_becomes_citations_with_locations() -> None:
    """観測の根拠（根拠のツールと読み取りのツールの chunk）を rank の順の引用にする。

    場所は値のある項目だけを metadata に写す（表計算のシート・行・セル範囲を含む。#1471）。
    ID の無い根拠は数えない。
    """
    from app.rag.file_processing_evaluation import (
        _has_traceable_citation_metadata,
        citation_traceability_coverage,
    )

    ledger = _evidence(
        "doc-ledger",
        "c-ledger-6",
        "行6: システムID: SYS-103 / 重要度: A",
        source="read",
        file_name="system-ledger.xlsx",
        locator={
            "section_path": ["システム台帳"],
            "page_start": None,
            "page_end": None,
            "page_label_start": None,
            "page_label_end": None,
            "sheet_name": "システム台帳",
            "row_start": 6,
            "row_end": 6,
            "cell_range": "A6:F6",
            "element_locator": "doc:doc-ledger/ext:er_1/el:sheet-000-r6",
        },
    )
    # 位置の無い chunk（場所の項目が無い・要素を指さない定位子だけ）。
    unplaced = _evidence(
        "doc-ledger",
        "c-ledger-x",
        "",
        source="read",
        locator={"section_path": [], "element_locator": "doc:doc-ledger/ext:er_1"},
    )
    pdf = _evidence("doc-pdf", "c-pdf-2", "部署の承認者", file_name="組織規程.pdf")
    pdf["locator"]["element_locator"] = "doc:doc-pdf/ext:er_2/page:2/el:7"
    no_ids = _evidence("", "", "ID の無い根拠")
    observation = _observation("run-1", evidence=[pdf, ledger, unplaced, no_ids])
    # 観測の rank の順に並べる（応答の並びに依らない）。
    observation["evidence"].reverse()

    citations = evidence_citations(observation)

    assert [item.chunk_id for item in citations] == ["c-pdf-2", "c-ledger-6", "c-ledger-x"]
    by_id = {item.chunk_id: item for item in citations}
    assert by_id["c-ledger-6"].metadata == {
        "element_locator": "doc:doc-ledger/ext:er_1/el:sheet-000-r6",
        "section_path": ["システム台帳"],
        "sheet_name": "システム台帳",
        "row_start": 6,
        "row_end": 6,
        "cell_range": "A6:F6",
    }
    assert by_id["c-ledger-6"].file_name == "system-ledger.xlsx"
    assert by_id["c-ledger-6"].text == "行6: システムID: SYS-103 / 重要度: A"
    assert _has_traceable_citation_metadata(by_id["c-ledger-6"])
    assert _has_traceable_citation_metadata(by_id["c-pdf-2"])
    assert not _has_traceable_citation_metadata(by_id["c-ledger-x"])
    assert citation_traceability_coverage(citations) == pytest.approx(2 / 3)


def test_evidence_without_text_is_recorded() -> None:
    """1 件でページの予算を超えて本文を省かれた根拠と、見られない根拠の数を Run の記録に残す。"""
    omitted = _evidence("doc-1", "c-1", "", text_omitted=True, text_length=90_000)
    observation = _observation(
        "run-1",
        evidence=[omitted, _evidence("doc-2", "c-2", "本文")],
        withheld_evidence_count=2,
    )
    fake = FakeAgentMcp([([_run("run-1", answer="答え", outcome="answered")], observation)])
    _, records = _evaluate(fake, [{"id": "a", "query": "q"}])
    assert (records[0].evidence_text_omitted, records[0].withheld_evidence_count) == (1, 2)
