"""業務支援の評価セットを Agent の Run として流し、RAG の評価と同じ採点にかける CLI（#1289）。

handoff §14.3 の D（Agent が RAG の根拠の道具・業務ガイド・最終の検証を編成する経路）を、
A（業務ガイドなし）・C（業務ガイドあり）と同じ評価セット・同じ採点で比べるための道具。

- Agent は公開の HTTP API（`POST /api/runs`・`GET /api/runs/{id}`）だけで呼ぶ。Agent のコードは
  import しない（製品間はコードで依存しない。AGENTS.md）。
- 採点は RAG の評価ランナーと同じ関数（`app.rag.evaluation.score_case_answers` と
  `summarize_case_results`）で行う。この CLI は RAG の backend にあり、同じ製品の採点を直接使う
  （採点の API を足したり、採点を複製したりしない）。Agent の Run から、RAG の検索・回答の応答と
  同じ形（`SearchResponse`）を組み立てて渡す。
- 複数往復のケース（`turns`）は、返答を同じ会話（`thread_id`）の次の Run として送る。
- ケースの既知の条件（`conditions`）は、Agent に構造化して渡す口が無いため、最初の質問の後ろに
  「既知の条件」の文として足す（`--guides` を渡すと条件の名前で書く）。往復の返答は、返答の文に
  条件が書いてあるため、文だけを送る（条件を読み取って `rag_search` に渡すのは Agent の仕事）。

Agent の回答の組み立て:

- 回答: Run の成果物 `answer` の本文（最終の検証 `answer_validation` の後の本文）。
- 引用: その Run の `rag_search` / `rag_retrieve_evidence` が返した根拠（出てきた順・重複なし）。
  本文は MCP の `excerpt`（最大 1000 文字）なので、必要な根拠の照合は抜粋の範囲で行う。
- 対応（outcome）: Run の成果物 `answer` の `outcome.value`（Agent の Control Plane が最終の
  回答に決定的に付けた対応。RAG の AnswerEnvelope と同じ語彙。#1305。
  `outcome_source=agent_answer`）。
  対応を持たない成果物（#1305 より前の Agent）は、その Run の最後の `rag_search` の `outcome`
  （`outcome_source=agent_rag_search`）。`rag_search` も呼ばなかった Run だけ、回答の最後の行
  （最終の検証が足す定型の注記を除く）が問い（？）なら確認の質問、それ以外は RAG の評価と同じ
  推定（拒答の文か）にする（`outcome_source=inferred`。推定は補助で、結果の
  `agent.outcome_sources` に件数を残す）。

    uv run python -m app.rag.agent_evaluation_cli run /tmp/business-support.resolved.json \\
        --agent-api-base-url http://127.0.0.1:8020 --create-agent \\
        --search-answer-profile-id <C の検索・回答プロファイル> \\
        --guides ../evaluation/business-support/support-guides.json \\
        --output /tmp/business-support.agent.json
    uv run python -m app.rag.agent_evaluation_cli summarize \\
        A=/tmp/a.json C=/tmp/c.json D=/tmp/business-support.agent.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.rag.evaluation import case_error_result, score_case_answers, summarize_case_results
from app.rag.evaluation_handling import EVALUATION_OUTCOMES
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRunRequest,
)
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchResponse

DEFAULT_AGENT_API_BASE_URL = "http://localhost:8020"
# 1 Run の終わりを待つ上限（秒）。rag_search は 1 回 50〜110 秒、最終の検証が 20 秒前後かかる。
DEFAULT_RUN_TIMEOUT_SECONDS = 900.0
DEFAULT_POLL_INTERVAL_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 60.0
BUSINESS_SUPPORT_SKILL_ID = "business_rag_research"
SETTLED_STATUSES = frozenset({"completed", "failed", "cancelled", "waiting_approval"})
RAG_SEARCH = "rag_search"
EVIDENCE_TOOLS = frozenset({RAG_SEARCH, "rag_retrieve_evidence"})
# 本文を読むツール（#1330・#1332）。多段の質問では段の事実を読んで確かめるため、読んだ chunk も
# 根拠に数える（Agent の最終の検証と同じ。#1345）。
RAG_READ_SOURCE = "rag_read_source"
RAG_READ_DOCUMENT = "rag_read_document"
READ_EVIDENCE_TOOLS = frozenset({RAG_READ_SOURCE, RAG_READ_DOCUMENT})
MCP_TOOL_SEPARATOR = "__"
# Agent の組み込みの MCP 接続（RAG）の ID。モデルが呼ぶツールの名前は `<接続>__<ツール>`
# （Agent #757）。指示にはこの名前を書く（素の名前だと存在しないツールを呼ぶ。#1303）。
RAG_MCP_CONNECTION = "rag"
OUTCOME_SOURCE_ANSWER = "agent_answer"
OUTCOME_SOURCE_TOOL = "agent_rag_search"
OUTCOME_SOURCE_INFERRED = "inferred"
_QUESTION_ENDINGS = ("？", "?")
# Agent の最終の検証が回答の末尾に足す定型の注記（推定で最後の行を見るときに除く。#1305）。
_AGENT_NOTICES = frozenset(
    {
        "この回答は検証できませんでした。",
        "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。",
    }
)


def agent_tool_name(tool_name: str) -> str:
    """Agent のモデルが呼ぶ RAG のツールの名前（`rag__rag_search` など。#1303）。"""
    return f"{RAG_MCP_CONNECTION}{MCP_TOOL_SEPARATOR}{tool_name}"


class AgentEvaluationError(RuntimeError):
    """利用者へ返す失敗（exit code 2）。"""


@dataclass(frozen=True)
class AgentTurn:
    """1 回の Run の回答を、RAG の検索・回答の応答の形にしたもの。"""

    response: SearchResponse
    run: Mapping[str, Any]
    outcome_source: str
    elapsed_ms: float


@dataclass
class RunRecord:
    """結果に残す Run の要約（本文は含めない）。"""

    case_id: str
    turn: int
    run_id: str | None
    thread_id: str | None
    status: str
    elapsed_ms: float
    outcome: str | None = None
    outcome_source: str | None = None
    validation_status: str | None = None
    model_requests: int | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)
    # 旧版も含めて検索した（`include_superseded: true` の）根拠のツールの成功した呼び出しの数（#1392）。
    superseded_searches: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "turn": self.turn,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "outcome": self.outcome,
            "outcome_source": self.outcome_source,
            "validation_status": self.validation_status,
            "model_requests": self.model_requests,
            "tool_calls": dict(self.tool_calls),
            "superseded_searches": self.superseded_searches,
        }


# ---- Run から回答を組み立てる ---------------------------------------------------------------


def base_tool_name(name: str) -> str:
    """MCP 接続のツールの名前（`<接続>__<ツール>`）のツールの部分。"""
    return name.rsplit(MCP_TOOL_SEPARATOR, 1)[-1]


def _records(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _tool_results(
    run: Mapping[str, Any], tools: frozenset[str]
) -> list[tuple[str, Mapping[str, Any]]]:
    """成功したツールの呼び出しの（素のツールの名前, 出力）（呼んだ順）。"""
    results: list[tuple[str, Mapping[str, Any]]] = []
    for step in _records(run.get("steps")):
        call = step.get("tool_call")
        result = step.get("tool_result")
        if not isinstance(call, Mapping) or not isinstance(result, Mapping):
            continue
        tool = base_tool_name(str(call.get("name") or ""))
        if tool not in tools:
            continue
        output = result.get("output")
        if result.get("success") and isinstance(output, Mapping):
            results.append((tool, output))
    return results


def _tool_outputs(run: Mapping[str, Any], tools: frozenset[str]) -> list[Mapping[str, Any]]:
    """成功したツールの呼び出しの出力（呼んだ順）。"""
    return [output for _tool, output in _tool_results(run, tools)]


def tool_call_counts(run: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in _records(run.get("steps")):
        call = step.get("tool_call")
        if isinstance(call, Mapping) and call.get("name"):
            name = base_tool_name(str(call["name"]))
            counts[name] = counts.get(name, 0) + 1
    return counts


def superseded_search_count(run: Mapping[str, Any]) -> int:
    """旧版も含めて検索した（`include_superseded: true`）根拠のツールの成功した呼び出しの数（#1392）。"""
    count = 0
    for step in _records(run.get("steps")):
        call = step.get("tool_call")
        result = step.get("tool_result")
        if not isinstance(call, Mapping) or not isinstance(result, Mapping):
            continue
        arguments = call.get("arguments")
        if (
            base_tool_name(str(call.get("name") or "")) in EVIDENCE_TOOLS
            and result.get("success")
            and isinstance(arguments, Mapping)
            and arguments.get("include_superseded") is True
        ):
            count += 1
    return count


def superseded_case_summary(
    cases: Sequence[EvaluationCase], records: Sequence[RunRecord]
) -> dict[str, Any]:
    """旧版も検索して答えるケース（`include_superseded: true`。#1366）で Agent が旧版を検索したか。

    A（RAG の評価）はそのケースだけ旧版を含めて検索するが、D は Agent が質問から判断して
    `include_superseded` を渡す（#1392）。渡さなかったケースは旧版の根拠が欠ける。
    """
    searched = {record.case_id for record in records if record.superseded_searches}
    marked = [case.id for case in cases if case.include_superseded]
    return {
        "case_count": len(marked),
        "searched_case_ids": [case_id for case_id in marked if case_id in searched],
        "missed_case_ids": [case_id for case_id in marked if case_id not in searched],
    }


def _artifact(run: Mapping[str, Any], kind: str) -> Mapping[str, Any] | None:
    for artifact in reversed(_records(run.get("artifacts"))):
        content = artifact.get("content")
        if artifact.get("kind") == kind and isinstance(content, Mapping):
            return content
    return None


def answer_text(run: Mapping[str, Any]) -> str:
    content = _artifact(run, "answer")
    text = content.get("text") if content else None
    return text if isinstance(text, str) else ""


def answer_outcome(run: Mapping[str, Any]) -> str | None:
    """成果物 `answer` の対応（`outcome.value`。#1305）。無い・語彙に無ければ None。"""
    content = _artifact(run, "answer")
    outcome = content.get("outcome") if content else None
    value = outcome.get("value") if isinstance(outcome, Mapping) else None
    return value if isinstance(value, str) and value in EVALUATION_OUTCOMES else None


def validation_status(run: Mapping[str, Any]) -> str | None:
    content = _artifact(run, "answer_validation")
    status = content.get("status") if content else None
    return status if isinstance(status, str) else None


def _read_items(tool: str, output: Mapping[str, Any]) -> list[dict[str, Any]]:
    """本文を読むツールの出力を、根拠の項目（document_id・chunk_id・excerpt・locator）にする。"""
    document_id = output.get("document_id")
    file_name = output.get("file_name")
    if tool == RAG_READ_SOURCE:
        return [
            {
                "document_id": document_id,
                "chunk_id": output.get("chunk_id"),
                "excerpt": output.get("text"),
                "file_name": file_name,
                "locator": output.get("locator"),
            }
        ]
    text = output.get("text")
    text = text if isinstance(text, str) else ""
    items: list[dict[str, Any]] = []
    for chunk in _records(output.get("chunks")):
        start, end = chunk.get("start"), chunk.get("end")
        excerpt = text[start:end] if isinstance(start, int) and isinstance(end, int) else ""
        items.append(
            {
                "document_id": document_id,
                "chunk_id": chunk.get("chunk_id"),
                "excerpt": excerpt,
                "file_name": file_name,
                "locator": {
                    key: chunk[key]
                    for key in ("element_locator", "section_path", "page_start", "page_end")
                    if chunk.get(key) is not None
                },
            }
        )
    return items


def evidence_citations(run: Mapping[str, Any]) -> list[RetrievedChunk]:
    """その Run の RAG の根拠のツールが返した根拠と、読み取りのツールで読んだ chunk（出てきた順・
    重複なし。#1345）。"""
    citations: list[RetrievedChunk] = []
    seen: set[tuple[str, str]] = set()
    for tool, output in _tool_results(run, EVIDENCE_TOOLS | READ_EVIDENCE_TOOLS):
        items = (
            _records(output.get("evidence"))
            if tool in EVIDENCE_TOOLS
            else _read_items(tool, output)
        )
        for item in items:
            document_id = str(item.get("document_id") or "")
            chunk_id = str(item.get("chunk_id") or item.get("evidence_id") or "")
            if not document_id or not chunk_id or (document_id, chunk_id) in seen:
                continue
            seen.add((document_id, chunk_id))
            locator = item.get("locator")
            metadata: dict[str, Any] = dict(locator) if isinstance(locator, Mapping) else {}
            score = item.get("score")
            citations.append(
                RetrievedChunk(
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=str(item.get("excerpt") or ""),
                    score=float(score) if isinstance(score, int | float) else 0.0,
                    file_name=str(item["file_name"]) if item.get("file_name") else None,
                    metadata=metadata,
                )
            )
    return citations


def _asks_question(answer: str) -> bool:
    lines = [
        line.strip()
        for line in answer.splitlines()
        if line.strip() and line.strip() not in _AGENT_NOTICES
    ]
    return bool(lines) and lines[-1].endswith(_QUESTION_ENDINGS)


def agent_turn(run: Mapping[str, Any], *, elapsed_ms: float) -> AgentTurn:
    """完了した Run を、RAG の評価が採点する応答（`SearchResponse`）にする。"""
    answer = answer_text(run)
    searches = _tool_outputs(run, frozenset({RAG_SEARCH}))
    last = searches[-1] if searches else None
    details: dict[str, Any] = {}
    outcome_source = OUTCOME_SOURCE_INFERRED
    recorded = answer_outcome(run)
    if recorded is not None:
        details["outcome"] = recorded
        outcome_source = OUTCOME_SOURCE_ANSWER
    elif last is not None and isinstance(last.get("outcome"), str) and last.get("outcome"):
        details["outcome"] = last["outcome"]
        outcome_source = OUTCOME_SOURCE_TOOL
    elif _asks_question(answer):
        details["outcome"] = "needs_clarification"
    if last is not None and outcome_source != OUTCOME_SOURCE_INFERRED:
        # 確認の質問で聞いた条件（聞き直しの採点）は、確認の質問を返したときだけ
        # rag_search の問いを使う（Agent が問いを返していない回答で聞き直しを数えない）。
        if details["outcome"] == "needs_clarification":
            details["clarifications"] = list(_records(last.get("clarifications")))
        if last.get("insufficient_reason"):
            details["insufficient_reason"] = last["insufficient_reason"]
    usage = run.get("usage")
    details["agent"] = {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "validation_status": validation_status(run),
        "outcome_source": outcome_source,
        "tool_calls": tool_call_counts(run),
        "model_requests": usage.get("requests") if isinstance(usage, Mapping) else None,
    }
    response = SearchResponse(
        answer=answer,
        citations=evidence_citations(run),
        trace_id=str(run.get("id") or ""),
        elapsed_ms=elapsed_ms,
        diagnostics=SearchDiagnostics(answer=details),
    )
    return AgentTurn(
        response=response, run=run, outcome_source=outcome_source, elapsed_ms=elapsed_ms
    )


def _with_outcome_sources(
    result: EvaluationCaseResult, sources: Sequence[str]
) -> EvaluationCaseResult:
    """採点の `outcome_source`（回答の記録の explicit / inferred）を、Agent の出所に直す。"""
    turn_results = [
        turn.model_copy(update={"outcome_source": sources[turn.turn]})
        if turn.turn < len(sources)
        else turn
        for turn in result.turn_results
    ]
    return result.model_copy(update={"outcome_source": sources[-1], "turn_results": turn_results})


def known_conditions_text(conditions: Mapping[str, str], labels: Mapping[str, str]) -> str:
    """ケースの既知の条件を、質問に足す文にする（Agent には構造化して渡す口が無い）。"""
    parts = [f"{labels.get(key, key)}は「{value}」" for key, value in conditions.items()]
    return f"（既知の条件: {'、'.join(parts)}）" if parts else ""


def condition_labels(guides: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for guide in _records(guides.get("guides")):
        for condition in _records(guide.get("conditions")):
            if condition.get("id") and condition.get("label"):
                labels[str(condition["id"])] = str(condition["label"])
    return labels


# ---- Agent の API ------------------------------------------------------------------------


class AgentApi:
    """Agent の公開の HTTP API。HTTP の client と待ち方を差し替えられる（テスト用）。"""

    def __init__(
        self,
        client: httpx.Client,
        api_base_url: str,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._api = api_base_url.rstrip("/") + "/api"
        self._interval = poll_interval_seconds
        self._sleep = sleep
        self._clock = clock

    def _data(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise AgentEvaluationError(
                f"{response.request.method} {response.request.url.path} が失敗しました"
                f"（HTTP {response.status_code}）: {response.text[:300]}"
            )
        return response.json().get("data")

    def create_agent(self, *, name: str, search_answer_profile_id: str | None) -> str:
        """業務支援のスキルを持つ業務 Agent を作って公開し、ID を返す。"""
        instructions = (
            "あなたは業務の手順・規則の問い合わせに答える業務支援の担当です。"
            "資料と業務ガイドの根拠だけで答え、根拠の無いことは推測で補わない。"
        )
        if search_answer_profile_id:
            profile_tools = "・".join(
                agent_tool_name(name)
                for name in (RAG_SEARCH, "rag_retrieve_evidence", "rag_lookup_guides")
            )
            instructions += (
                f"RAG のツール（{profile_tools}）には、"
                f"search_answer_profile_id に「{search_answer_profile_id}」を必ず渡す。"
            )
        created = self._data(
            self._client.post(
                f"{self._api}/agents",
                json={
                    "name": name,
                    "description": "業務支援の評価（D: Agent の経路）のための業務 Agent（評価用）",
                    "instructions": instructions,
                    "skill_ids": [BUSINESS_SUPPORT_SKILL_ID],
                },
            )
        )
        agent_id = str(created["id"])
        self._data(
            self._client.post(
                f"{self._api}/agents/{agent_id}/publish", json={"note": "業務支援の評価"}
            )
        )
        return agent_id

    def create_run(self, *, agent_id: str, goal: str, thread_id: str | None) -> Mapping[str, Any]:
        payload: dict[str, Any] = {"agent_id": agent_id, "goal": goal}
        if thread_id:
            payload["thread_id"] = thread_id
        data: Mapping[str, Any] = self._data(self._client.post(f"{self._api}/runs", json=payload))
        return data

    def get_run(self, run_id: str) -> Mapping[str, Any]:
        data: Mapping[str, Any] = self._data(self._client.get(f"{self._api}/runs/{run_id}"))
        return data

    def cancel_run(self, run_id: str) -> None:
        with contextlib.suppress(httpx.HTTPError):
            self._client.post(f"{self._api}/runs/{run_id}/cancel", json={})

    def wait_run(self, run_id: str, timeout_seconds: float) -> Mapping[str, Any] | None:
        """Run が終わる（完了・失敗・取消・承認待ち）まで待つ。時間内に終わらなければ None。"""
        deadline = self._clock() + timeout_seconds
        while True:
            run = self.get_run(run_id)
            if str(run.get("status")) in SETTLED_STATUSES:
                return run
            if self._clock() >= deadline:
                return None
            self._sleep(self._interval)


# ---- 評価 ------------------------------------------------------------------------------


@dataclass
class CaseOutcome:
    result: EvaluationCaseResult
    runs: list[RunRecord]


def _case_turns(
    case: EvaluationCase, labels: Mapping[str, str]
) -> list[tuple[str, dict[str, str]]]:
    """送る発話と、その発話までに分かった条件（採点の聞き直しの判定に使う）。"""
    first = case.query
    if case.conditions:
        first = f"{first}\n{known_conditions_text(case.conditions, labels)}"
    turns = [(first, dict(case.conditions))]
    conditions = dict(case.conditions)
    for turn in case.turns:
        conditions = {**conditions, **turn.conditions}
        turns.append((turn.reply, dict(conditions)))
    return turns


def evaluate_case(
    api: AgentApi,
    case: EvaluationCase,
    *,
    agent_id: str,
    labels: Mapping[str, str],
    run_timeout_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> CaseOutcome:
    """1 ケースの最初の質問と返答を同じ会話の Run として流し、RAG の評価と同じ採点にかける。"""
    thread_id: str | None = None
    answers: list[tuple[SearchResponse, Mapping[str, str]]] = []
    sources: list[str] = []
    records: list[RunRecord] = []
    for index, (goal, conditions) in enumerate(_case_turns(case, labels)):
        started = clock()
        created = api.create_run(agent_id=agent_id, goal=goal, thread_id=thread_id)
        run_id = str(created["id"])
        thread_id = str(created.get("thread_id") or "") or thread_id
        run = api.wait_run(run_id, run_timeout_seconds)
        elapsed = (clock() - started) * 1000
        status = "timeout" if run is None else str(run.get("status"))
        record = RunRecord(
            case_id=case.id,
            turn=index,
            run_id=run_id,
            thread_id=thread_id,
            status=status,
            elapsed_ms=elapsed,
        )
        records.append(record)
        if run is None or status != "completed":
            if run is None:
                api.cancel_run(run_id)
            message = {
                "timeout": f"Agent の Run が {run_timeout_seconds:g} 秒以内に終わりませんでした。",
                "waiting_approval": "Agent の Run が承認待ちで止まりました。",
            }.get(status, f"Agent の Run が {status} で終わりました。")
            result = case_error_result(
                case,
                trace_id=run_id,
                elapsed_ms=round(sum(item.elapsed_ms for item in records), 3),
                error_type=f"AgentRun{status.title().replace('_', '')}",
                error_message=message + " run_id で Agent の実行履歴を確認してください。",
                error_stage=f"turn_{index}",
            )
            return CaseOutcome(result=result, runs=records)
        turn = agent_turn(run, elapsed_ms=elapsed)
        outcome = (turn.response.diagnostics.answer or {}).get("outcome")
        record.outcome = outcome if isinstance(outcome, str) else None
        record.outcome_source = turn.outcome_source
        record.validation_status = validation_status(run)
        usage = run.get("usage")
        requests = usage.get("requests") if isinstance(usage, Mapping) else None
        record.model_requests = requests if isinstance(requests, int) else None
        record.tool_calls = tool_call_counts(run)
        record.superseded_searches = superseded_search_count(run)
        answers.append((turn.response, conditions))
        sources.append(turn.outcome_source)
    result = score_case_answers(case, answers)
    return CaseOutcome(result=_with_outcome_sources(result, sources), runs=records)


def evaluate(
    api: AgentApi,
    request: EvaluationRunRequest,
    *,
    agent_id: str,
    labels: Mapping[str, str],
    run_timeout_seconds: float,
    log: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[EvaluationMetrics, list[RunRecord]]:
    results: list[EvaluationCaseResult] = []
    records: list[RunRecord] = []
    for index, case in enumerate(request.cases, start=1):
        try:
            outcome = evaluate_case(
                api,
                case,
                agent_id=agent_id,
                labels=labels,
                run_timeout_seconds=run_timeout_seconds,
                clock=clock,
            )
        except (AgentEvaluationError, httpx.HTTPError) as error:
            # Run を作れない・読めないケースは失敗として記録して続ける。
            outcome = CaseOutcome(
                result=case_error_result(
                    case,
                    trace_id="",
                    elapsed_ms=0.0,
                    error_type=type(error).__name__,
                    error_message=str(error)[:300],
                ),
                runs=[],
            )
        results.append(outcome.result)
        records.extend(outcome.runs)
        log(
            f"[{index}/{len(request.cases)}] {case.id}: {outcome.result.status} "
            f"outcome={outcome.result.observed_outcome} "
            f"source={outcome.result.outcome_source} "
            f"handling={outcome.result.handling_correct}"
        )
    return summarize_case_results(results, thresholds=request.thresholds), records


def outcome_source_counts(records: Sequence[RunRecord]) -> dict[str, int]:
    """完了した Run の対応の出所ごとの数（agent_answer / agent_rag_search / inferred）。"""
    counts: dict[str, int] = {}
    for record in records:
        if record.outcome_source:
            counts[record.outcome_source] = counts.get(record.outcome_source, 0) + 1
    return counts


# ---- 比較の要約 ---------------------------------------------------------------------------

SUMMARY_METRICS: tuple[tuple[str, str], ...] = (
    ("handling_accuracy", "対応の正しさ"),
    ("step_order_score", "手順の網羅と順序"),
    ("safe_answer_rate", "危険な回答の無さ"),
    ("condition_coverage", "条件への言及"),
    ("required_evidence_recall", "必要な根拠の再現率"),
    ("evidence_chain_complete_rate", "根拠の連鎖の完全率"),
    ("refusal_accuracy", "拒答の正しさ"),
    ("context_recall", "正解文書の再現率"),
)
# 多段の質問の種類別・段の数別の内訳に出す指標（#1335。RAG の評価の内訳と同じ）。
REASONING_SUMMARY_METRICS: tuple[tuple[str, str], ...] = (
    ("evidence_chain_complete_rate", "根拠の連鎖の完全率"),
    ("required_evidence_recall", "必要な根拠の再現率"),
    ("answer_keyword_hit_rate", "期待する語の一致率"),
)


def _reasoning_summary(breakdown: object) -> dict[str, dict[str, Any]]:
    """種類別・段の数別の内訳（`reasoning_type_breakdown` / `hops_breakdown`）の要約。"""
    summary: dict[str, dict[str, Any]] = {}
    if not isinstance(breakdown, Mapping):
        return summary
    for name, item in breakdown.items():
        if not isinstance(item, Mapping):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
        summary[str(name)] = {
            "case_count": item.get("case_count"),
            "error_count": item.get("error_count"),
            **{metric: (metrics or {}).get(metric) for metric, _ in REASONING_SUMMARY_METRICS},
        }
    return summary


def _median_seconds(values: Sequence[float]) -> float | None:
    return round(statistics.median(values) / 1000, 1) if values else None


def summarize_result(data: Mapping[str, Any]) -> dict[str, Any]:
    """評価の結果（RAG の評価 job の結果、またはこの CLI の結果の `data`）の比較用の要約。"""
    cases = _records(data.get("case_results"))
    succeeded = [case for case in cases if case.get("status", "success") == "success"]
    summary: dict[str, Any] = {
        "case_count": data.get("case_count"),
        "error_count": data.get("error_count"),
        **{metric: data.get(metric) for metric, _ in SUMMARY_METRICS},
        "dangerous_case_count": sum(1 for case in succeeded if case.get("forbidden_hits")),
        "dangerous_case_ids": [
            case.get("case_id") for case in succeeded if case.get("forbidden_hits")
        ],
        "failed_case_ids": [
            case.get("case_id") for case in cases if case.get("status", "success") != "success"
        ],
        "median_elapsed_seconds": _median_seconds(
            [float(case["elapsed_ms"]) for case in succeeded if case.get("elapsed_ms") is not None]
        ),
        "splits": {},
        "categories": {},
        # 多段の質問の種類別・段の数別（#1335）。
        "reasoning_types": _reasoning_summary(data.get("reasoning_type_breakdown")),
        "hops": _reasoning_summary(data.get("hops_breakdown")),
    }
    splits = data.get("split_breakdown")
    if isinstance(splits, Mapping):
        for split, item in splits.items():
            if not isinstance(item, Mapping):
                continue
            metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
            summary["splits"][split] = {
                "case_count": item.get("case_count"),
                "error_count": item.get("error_count"),
                **{metric: (metrics or {}).get(metric) for metric, _ in SUMMARY_METRICS},
                "reasoning_types": _reasoning_summary(item.get("reasoning_type_breakdown")),
            }
    categories = data.get("category_breakdown")
    if isinstance(categories, Mapping):
        for category, item in categories.items():
            if isinstance(item, Mapping):
                summary["categories"][category] = {
                    "case_count": item.get("case_count"),
                    "handling_correct_rate": item.get("handling_correct_rate"),
                }
    return summary


def _format(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def summary_markdown(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    """要約を Markdown の表にする（docs の記録用）。"""
    labels = list(summaries)
    header = "| 指標 | " + " | ".join(labels) + " |"
    rule = "|---|" + "---|" * len(labels)
    rows = [header, rule]

    def row(name: str, values: Sequence[object]) -> None:
        rows.append(f"| {name} | " + " | ".join(_format(value) for value in values) + " |")

    for metric, name in SUMMARY_METRICS:
        row(name, [summaries[label].get(metric) for label in labels])
    row("危険な回答のケース", [summaries[label].get("dangerous_case_count") for label in labels])
    row("失敗したケース", [summaries[label].get("error_count") for label in labels])
    row(
        "1 問の時間の中央値（秒）",
        [summaries[label].get("median_elapsed_seconds") for label in labels],
    )
    splits = sorted(
        {split for summary in summaries.values() for split in summary.get("splits", {})}
    )
    for split in splits:
        for metric in ("handling_accuracy", "step_order_score", "safe_answer_rate"):
            name = dict(SUMMARY_METRICS)[metric]
            row(
                f"{split}: {name}",
                [summaries[label].get("splits", {}).get(split, {}).get(metric) for label in labels],
            )
    _reasoning_rows(summaries, labels, row)
    categories = sorted(
        {category for summary in summaries.values() for category in summary.get("categories", {})}
    )
    for category in categories:
        row(
            f"{category}: 対応の正しさ",
            [
                summaries[label]
                .get("categories", {})
                .get(category, {})
                .get("handling_correct_rate")
                for label in labels
            ],
        )
    return "\n".join(rows) + "\n"


def _reasoning_rows(
    summaries: Mapping[str, Mapping[str, Any]],
    labels: Sequence[str],
    row: Callable[[str, Sequence[object]], None],
) -> None:
    """多段の質問の種類別・段の数別の行（#1335）と、区分ごとの種類別の根拠の連鎖の完全率の行。"""
    for key, suffix in (("reasoning_types", ""), ("hops", " 段")):
        groups = {label: summaries[label].get(key, {}) for label in labels}
        for name in _ordered_names(groups.values()):
            for metric, metric_name in REASONING_SUMMARY_METRICS:
                row(
                    f"{name}{suffix}: {metric_name}",
                    [groups[label].get(name, {}).get(metric) for label in labels],
                )
    splits = sorted(
        {split for summary in summaries.values() for split in summary.get("splits", {})}
    )
    for split in splits:
        groups = {
            label: summaries[label].get("splits", {}).get(split, {}).get("reasoning_types", {})
            for label in labels
        }
        for name in _ordered_names(groups.values()):
            row(
                f"{split} / {name}: 根拠の連鎖の完全率",
                [
                    groups[label].get(name, {}).get("evidence_chain_complete_rate")
                    for label in labels
                ],
            )


def _ordered_names(groups: Iterable[Mapping[str, Any]]) -> list[str]:
    """内訳の名前（出てきた順・重複なし）。"""
    names: list[str] = []
    for group in groups:
        for name in group:
            if name not in names:
                names.append(name)
    return names


def _load_result(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data", payload) if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise AgentEvaluationError(f"評価の結果の形が読めません: {path}")
    return data


# ---- CLI ------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="評価セットを Agent の Run として流して採点する")
    run.add_argument("golden_set", type=Path, help="文書の参照を置き換えた評価セット")
    run.add_argument("--agent-api-base-url", default=DEFAULT_AGENT_API_BASE_URL)
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--agent-id", help="使う業務 Agent（公開済み・業務支援のスキル）")
    target.add_argument(
        "--create-agent",
        action="store_true",
        help="業務支援のスキル（business_rag_research）を持つ業務 Agent を作って公開する",
    )
    run.add_argument(
        "--search-answer-profile-id",
        help="--create-agent で、RAG のツールに渡す検索・回答プロファイル（C と同じもの）",
    )
    run.add_argument("--guides", type=Path, help="既知の条件の名前に使う業務ガイド")
    run.add_argument("--case", action="append", default=[], help="流すケースの id（複数可）")
    run.add_argument("--split", choices=["dev", "holdout"], help="流す区分")
    run.add_argument("--run-timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS)
    run.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    run.add_argument("--output", type=Path, required=True, help="結果（JSON）の出力先")
    summarize = commands.add_parser("summarize", help="評価の結果を比べる表を出す")
    summarize.add_argument("results", nargs="+", help="<名前>=<結果の JSON>（例: A=a.json）")
    summarize.add_argument("--json", action="store_true", help="表ではなく JSON で出す")
    return parser


def _select_cases(
    request: EvaluationRunRequest, ids: Sequence[str], split: str | None
) -> EvaluationRunRequest:
    cases = [
        case
        for case in request.cases
        if (not ids or case.id in ids) and (split is None or case.split == split)
    ]
    if not cases:
        raise AgentEvaluationError(
            "流すケースがありません（--case / --split を確かめてください）。"
        )
    return request.model_copy(update={"cases": cases})


def _run_command(args: argparse.Namespace) -> int:
    try:
        request = EvaluationRunRequest.model_validate(
            json.loads(args.golden_set.read_text(encoding="utf-8"))
        )
    except ValidationError as error:
        raise AgentEvaluationError(f"評価セットの形が不正です: {error.error_count()} 件") from error
    unresolved = [
        case.id
        for case in request.cases
        if any(value.startswith("file:") for value in case.relevant_document_ids)
    ]
    if unresolved:
        raise AgentEvaluationError(
            "文書の参照（file:）を置き換えていません。evaluation_corpus_cli で取り込んでください: "
            + ", ".join(unresolved[:5])
        )
    request = _select_cases(request, args.case, args.split)
    labels = (
        condition_labels(json.loads(args.guides.read_text(encoding="utf-8"))) if args.guides else {}
    )
    with httpx.Client(
        headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False
    ) as client:
        api = AgentApi(client, args.agent_api_base_url, poll_interval_seconds=args.poll_interval)
        agent_id = args.agent_id or api.create_agent(
            name=f"業務支援の評価 {time.strftime('%Y%m%d-%H%M%S')}",
            search_answer_profile_id=args.search_answer_profile_id,
        )
        print(f"agent {agent_id}", file=sys.stderr)
        metrics, records = evaluate(
            api,
            request,
            agent_id=agent_id,
            labels=labels,
            run_timeout_seconds=args.run_timeout,
            log=lambda line: print(line, file=sys.stderr),
        )
    data = metrics.model_dump(mode="json")
    payload = {
        "data": data,
        "agent": {
            "agent_id": agent_id,
            "search_answer_profile_id": args.search_answer_profile_id,
            "runs": [record.as_json() for record in records],
            # 対応の出所ごとの Run の数（推定 inferred が残っていないかを確かめる。#1305）。
            "outcome_sources": outcome_source_counts(records),
            # 旧版も検索して答えるケースで、Agent が旧版を検索したか（#1392）。
            "superseded_cases": superseded_case_summary(request.cases, records),
            "summary": summarize_result(data),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


def _summarize_command(args: argparse.Namespace) -> int:
    summaries: dict[str, Mapping[str, Any]] = {}
    for item in args.results:
        label, separator, path = item.partition("=")
        if not separator or not label or not path:
            raise AgentEvaluationError(f"<名前>=<結果の JSON> の形で渡してください: {item}")
        summaries[label] = summarize_result(_load_result(Path(path)))
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        print(summary_markdown(summaries), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run_command(args)
        return _summarize_command(args)
    except (AgentEvaluationError, OSError, json.JSONDecodeError, httpx.HTTPError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
