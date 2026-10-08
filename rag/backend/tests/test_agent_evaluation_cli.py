"""業務支援の評価を Agent の Run として流す CLI（#1289）。Agent の API は MockTransport の台本。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.agent_evaluation_cli import (
    AgentApi,
    agent_turn,
    evaluate,
    known_conditions_text,
    main,
    summarize_result,
    summary_markdown,
)
from app.schemas.evaluation import EvaluationRunRequest


def _evidence(document_id: str, chunk_id: str, excerpt: str) -> dict[str, Any]:
    return {
        "evidence_id": chunk_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "file_name": "manual.pdf",
        "excerpt": excerpt,
        "score": 0.9,
        "locator": {"page_start": 1, "page_end": 1, "section_path": ["3. アクセス権限の付与"]},
    }


def _step(
    tool: str, output: dict[str, Any], *, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": "completed",
        "tool_call": {"name": f"rag__{tool}", "arguments": arguments or {}},
        "tool_result": {"name": tool, "success": True, "output": output},
    }


def _run(
    run_id: str,
    *,
    answer: str,
    steps: list[dict[str, Any]],
    status: str = "completed",
    thread_id: str = "thread_" + "a" * 32,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "thread_id": thread_id,
        "steps": steps,
        "artifacts": [
            {"kind": "answer", "content": {"text": answer}},
            {"kind": "answer_validation", "content": {"status": "completed"}},
        ],
        "usage": {"requests": 3},
    }


class FakeAgent:
    """`POST /api/runs` で台本の Run を順に返し、`GET /api/runs/{id}` は台本の状態の列を返す。"""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        # 1 Run ごとに、GET で返す状態の列（最後の状態を返し続ける）。
        self.scripts = scripts
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.polls: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "/agents":
            return httpx.Response(200, json={"data": {"id": "agent-eval"}})
        if path.endswith("/publish"):
            return httpx.Response(200, json={"data": {"id": "agent-eval"}})
        if path == "/runs" and request.method == "POST":
            self.created.append(dict(body or {}))
            states = self.scripts[len(self.created) - 1]
            run_id = states[-1]["id"]
            self.polls[run_id] = list(states)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": run_id,
                        "status": "queued",
                        "thread_id": states[-1].get("thread_id"),
                    }
                },
            )
        if path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[2])
            return httpx.Response(200, json={"data": {}})
        run_id = path.split("/")[2]
        queue = self.polls[run_id]
        state = queue[0] if len(queue) == 1 else queue.pop(0)
        return httpx.Response(200, json={"data": state})


def _api(fake: FakeAgent, clock: Callable[[], float] | None = None) -> AgentApi:
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="http://agent")
    return AgentApi(
        client,
        "http://agent",
        poll_interval_seconds=0,
        sleep=lambda _: None,
        **({"clock": clock} if clock else {}),
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
        steps=[
            _step(
                "rag_search",
                {
                    "outcome": "needs_clarification",
                    "clarifications": [{"condition_id": "target", "question": "どちら？"}],
                    "evidence": [],
                },
            )
        ],
    )
    answered = _run(
        "run-2",
        answer="個別の利用者に付与する場合は、利用者の詳細画面の「権限」タブで付与します。",
        steps=[
            _step(
                "rag_search",
                {
                    "outcome": "answered",
                    "clarifications": [],
                    "evidence": [
                        _evidence(
                            "doc-manual", "c1", "個別の利用者に付与する場合: 利用者の詳細画面"
                        ),
                        _evidence("doc-manual", "c1", "重複"),
                    ],
                },
                arguments={"conditions": {"target": "個別"}},
            )
        ],
    )
    fake = FakeAgent([[{**clarify, "status": "running"}, clarify], [answered]])
    metrics, records = evaluate(
        _api(fake),
        _request([GRANT_CASE]),
        agent_id="agent-1",
        labels={},
        run_timeout_seconds=60,
        log=lambda _: None,
    )

    # 返答は同じ会話の次の Run として送る。
    assert fake.created[0] == {"agent_id": "agent-1", "goal": GRANT_CASE["query"]}
    assert fake.created[1] == {
        "agent_id": "agent-1",
        "goal": "個別の利用者に付けます。",
        "thread_id": "thread_" + "a" * 32,
    }
    result = metrics.case_results[0]
    assert result.status == "success"
    assert result.handling_correct is True
    assert result.observed_outcome == "answered"
    assert result.outcome_source == "agent_rag_search"
    assert [turn.observed_outcome for turn in result.turn_results] == [
        "needs_clarification",
        "answered",
    ]
    assert all(turn.outcome_source == "agent_rag_search" for turn in result.turn_results)
    # 根拠は重複なしで、必要な根拠は抜粋で照合する。
    assert result.retrieved_document_ids == ["doc-manual"]
    assert result.evidence_recall == 1.0
    assert result.reasked_conditions == []
    assert metrics.handling_accuracy == 1.0
    assert metrics.split_breakdown["dev"].case_count == 1
    assert [
        (record.run_id, record.validation_status, record.model_requests) for record in records
    ] == [
        ("run-1", "completed", 3),
        ("run-2", "completed", 3),
    ]
    assert records[1].tool_calls == {"rag_search": 1}


def test_known_conditions_are_added_to_the_first_question_with_guide_labels() -> None:
    case = {
        "id": "known",
        "query": "アクセス権限を付与したいです。どの画面で操作しますか？",
        "conditions": {"target": "グループ"},
        "expected_outcomes": ["answered", "conditional"],
        "required_conditions": ["承認"],
    }
    run = _run(
        "run-1",
        answer="部門長の承認を得てから、グループの詳細画面で付与します。",
        steps=[
            _step(
                "rag_retrieve_evidence",
                {
                    "evidence": [
                        _evidence("doc-manual", "c2", "付与の前に部門長の承認を得てください")
                    ]
                },
            )
        ],
    )
    fake = FakeAgent([[run]])
    metrics, _ = evaluate(
        _api(fake),
        _request([case]),
        agent_id="agent-1",
        labels={"target": "付与先"},
        run_timeout_seconds=60,
        log=lambda _: None,
    )
    assert fake.created[0]["goal"].endswith("（既知の条件: 付与先は「グループ」）")
    assert known_conditions_text({"approved": "はい"}, {}) == "（既知の条件: approvedは「はい」）"
    result = metrics.case_results[0]
    # rag_search を呼ばない Run は、RAG の評価と同じ推定（根拠があり拒答の文でなければ answered）。
    assert result.observed_outcome == "answered"
    assert result.outcome_source == "inferred"
    assert result.condition_coverage == 1.0
    # 根拠の無い回答は、RAG の評価と同じく拒答とみなす。
    bare = agent_turn(_run("run-9", answer="グループの詳細画面です。", steps=[]), elapsed_ms=1.0)
    assert bare.response.diagnostics.answer is not None
    assert "outcome" not in bare.response.diagnostics.answer


def test_answer_ending_with_a_question_without_rag_search_is_a_clarification() -> None:
    turn = agent_turn(
        _run("run-1", answer="確認させてください。\n部門長の承認は得ていますか？", steps=[]),
        elapsed_ms=10.0,
    )
    assert turn.response.diagnostics.answer is not None
    assert turn.response.diagnostics.answer["outcome"] == "needs_clarification"
    assert turn.outcome_source == "inferred"
    assert turn.response.trace_id == "run-1"


def test_failed_and_timed_out_runs_are_case_errors() -> None:
    failed = _run("run-1", answer="", steps=[], status="failed")
    running = {**_run("run-2", answer="", steps=[]), "status": "running"}
    fake = FakeAgent([[failed], [running]])
    ticks = iter(range(0, 10_000, 100))
    metrics, records = evaluate(
        _api(fake, clock=lambda: float(next(ticks))),
        _request(
            [
                {"id": "a", "query": "q1", "expected_outcomes": ["answered"]},
                {"id": "b", "query": "q2", "expected_outcomes": ["answered"]},
            ]
        ),
        agent_id="agent-1",
        labels={},
        run_timeout_seconds=150,
        log=lambda _: None,
    )
    assert metrics.error_count == 2
    assert metrics.passed is False
    assert [result.error_type for result in metrics.case_results] == [
        "AgentRunFailed",
        "AgentRunTimeout",
    ]
    assert metrics.failure_reason_counts == {"case_error": 2}
    # 待ちきれなかった Run は取り消す。
    assert fake.cancelled == ["run-2"]
    assert [record.status for record in records] == ["failed", "timeout"]


def test_summary_compares_results_in_a_markdown_table() -> None:
    data = {
        "case_count": 2,
        "error_count": 0,
        "handling_accuracy": 0.5,
        "safe_answer_rate": 0.5,
        "case_results": [
            {"case_id": "a", "status": "success", "elapsed_ms": 1000.0, "forbidden_hits": []},
            {"case_id": "b", "status": "success", "elapsed_ms": 3000.0, "forbidden_hits": ["x"]},
        ],
        "split_breakdown": {
            "holdout": {"case_count": 1, "error_count": 0, "metrics": {"handling_accuracy": 0.0}}
        },
        "category_breakdown": {
            "clarification_required": {"case_count": 2, "handling_correct_rate": 0.5}
        },
    }
    summary = summarize_result(data)
    assert summary["median_elapsed_seconds"] == 2.0
    assert summary["dangerous_case_count"] == 1
    assert summary["dangerous_case_ids"] == ["b"]
    assert summary["splits"]["holdout"]["handling_accuracy"] == 0.0
    table = summary_markdown({"A": summary, "D": summary})
    assert "| 指標 | A | D |" in table
    assert "| 対応の正しさ | 0.50 | 0.50 |" in table
    assert "| holdout: 対応の正しさ | 0.00 | 0.00 |" in table
    assert "| 1 問の時間の中央値（秒） | 2.00 | 2.00 |" in table


def test_main_creates_agent_runs_and_writes_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    fake = FakeAgent([[_run("run-1", answer="答え", steps=[])]])
    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        kwargs.pop("trust_env", None)
        return real_client(transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    output = tmp_path / "out.json"
    code = main(
        [
            "run",
            str(golden),
            "--agent-api-base-url",
            "http://agent",
            "--create-agent",
            "--search-answer-profile-id",
            "sap-1",
            "--split",
            "dev",
            "--poll-interval",
            "0",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["agent"]["agent_id"] == "agent-eval"
    assert payload["data"]["case_count"] == 1
    assert payload["agent"]["runs"][0]["run_id"] == "run-1"
    created_agent = next(body for method, path, body in fake.calls if path == "/agents")
    assert created_agent["skill_ids"] == ["business_rag_research"]
    assert "sap-1" in created_agent["instructions"]
    assert ("POST", "/agents/agent-eval/publish", {"note": "業務支援の評価"}) in fake.calls

    code = main(["summarize", f"D={output}"])
    assert code == 0
    assert "| 指標 | D |" in capsys.readouterr().out


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
