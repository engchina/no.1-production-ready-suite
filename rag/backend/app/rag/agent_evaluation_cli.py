"""業務支援の評価セットを Agent の Run として流し、RAG の評価と同じ採点にかける CLI（#1289）。

handoff §14.3 の D（Agent が RAG の根拠の道具・業務ガイド・最終の検証を編成する経路）を、
A（業務ガイドなし）・C（業務ガイドあり）と同じ評価セット・同じ採点で比べるための道具。

- Agent は公開の MCP（`POST /api/mcp`。契約は `platform/contracts/mcp/agent-tools.json`）
  だけで、利用者が発行した API キー（`Authorization: Bearer prak_…`。使える製品に `agent`）で
  呼ぶ（#3240）。使うツールは `agent_chat`・`agent_get_run`・`agent_get_run_observation`・
  `agent_cancel_run` だけで、Agent の画面の API・Run の内部の記録・Agent のコードには依存しない
  （製品間はコードで依存しない。AGENTS.md）。業務 Agent は公開済みのものを ID で指定する
  （作成・公開の MCP のツールは持たない）。
- 採点は RAG の評価ランナーと同じ関数（`app.rag.evaluation.score_case_answers` と
  `summarize_case_results`）で行う。この CLI は RAG の backend にあり、同じ製品の採点を直接使う
  （採点の API を足したり、採点を複製したりしない）。Agent の Run の公開の出力から、RAG の
  検索・回答の応答と同じ形（`SearchResponse`）を組み立てて渡す。
- 複数往復のケース（`turns`）は、返答を同じ会話（`thread_id`）の次の `agent_chat` として送る。
- ケースの既知の条件（`conditions`）は、Agent に構造化して渡す口が無いため、最初の質問の後ろに
  「既知の条件」の文として足す（`--guides` を渡すと条件の名前で書く）。往復の返答は、返答の文に
  条件が書いてあるため、文だけを送る（条件を読み取って `rag_search` に渡すのは Agent の仕事）。

Agent の回答の組み立て（公開の出力の項目）:

- 回答: `agent_get_run` の `answer`（最終の検証の後の本文）。
- 引用: `agent_get_run_observation` の `evidence[]`（Run の RAG の根拠のツールが返した根拠と、
  読み取りのツールで読んだ chunk の全件。初めて現れた順 `rank`・重複なし。同じ chunk を後で
  読んだ本文はつないである）。`next_cursor` をたどって全件を読む。場所は `locator`（RAG の
  `EvidenceLocator` と同じ名前）。
- 対応（outcome）: `agent_get_run` の `outcome`（Agent が最終の回答に決定的に付けた対応。
  RAG の AnswerEnvelope と同じ語彙。#1305。`outcome_source=agent_answer`）。対応の無い完了の
  Run（#1305 より前の Agent の Run）だけ、回答の最後の行（最終の検証が足す定型の注記を除く）が
  問い（？）なら確認の質問、それ以外は RAG の評価と同じ推定（拒答の文か）にする
  （`outcome_source=inferred`。推定は補助で、結果の `agent.outcome_sources` に件数を残す）。
- 承認の要るツールを呼ばなかった Run（MCP の Run は承認の要るツールを呼ばない。
  `skipped_approval_tools`・`tool_calls[].status=approval_skipped`）は、必要な操作をしていない
  回答なのでケースの失敗にする（#3240 の Q7。承認待ちで止まった Run と同じ扱い）。
- 時間内に終わらない Run は `agent_cancel_run` で取り消し、結果（`result`）を Run の記録に残す。

    export RAG_EVALUATION_AGENT_API_KEY=prak_...   # 使える製品に agent を含むキー
    uv run python -m app.rag.agent_evaluation_cli run /tmp/business-support.resolved.json \\
        --agent-mcp-url http://127.0.0.1:8020/api/mcp --agent-id <公開済みの業務 Agent> \\
        --guides ../evaluation/business-support/support-guides.json \\
        --output /tmp/business-support.agent.json
    uv run python -m app.rag.agent_evaluation_cli summarize \\
        A=/tmp/a.json C=/tmp/c.json D=/tmp/business-support.agent.json
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import math
import os
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
    EVALUATION_METRICS_VERSION,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRunRequest,
)
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchResponse

DEFAULT_AGENT_MCP_URL = "http://localhost:8020/api/mcp"
# API キーを読む環境変数（キーを引数に書かない。シェルの履歴・プロセスの一覧に残さない）。
DEFAULT_API_KEY_ENV = "RAG_EVALUATION_AGENT_API_KEY"
MCP_PROTOCOL_VERSION = "2025-06-18"
# 1 Run の終わりを待つ上限（秒）。rag_search は 1 回 50〜110 秒、最終の検証が 20 秒前後かかる。
DEFAULT_RUN_TIMEOUT_SECONDS = 900.0
REQUEST_TIMEOUT_SECONDS = 60.0
# `agent_chat` / `agent_get_run` の `wait_seconds` の上限（契約の maximum）。長く待たせて呼び出しの
# 回数を減らす（API キーの 1 分あたりの呼び出しの上限に収める）。
MAX_WAIT_SECONDS = 300
# 観測の根拠を 1 回に読む件数（契約の maximum）と、たどるページの上限（壊れた next_cursor で
# 回り続けない）。
OBSERVATION_PAGE_LIMIT = 100
MAX_OBSERVATION_PAGES = 1000
# 読める出力の形の版（契約 agent-tools.json の schema_version。知らない版は採点しない）。
RUN_OUTPUT_SCHEMA_VERSION = 4
OBSERVATION_SCHEMA_VERSION = 1
CANCEL_SCHEMA_VERSION = 1
# Agent の経路の採点の版。RAG の六指標の版に、Agent の経路の規則の版を足す（agent_v1 は段 1 の
# 画面の API と Run の内部の記録で採点した版。agent_v2 は公開の MCP の出力だけで採点し、承認の要る
# ツールを呼ばなかった Run をケースの失敗にする。#3240）。
AGENT_EVALUATION_METRICS_VERSION = f"{EVALUATION_METRICS_VERSION}+agent_v2"
RAG_SEARCH = "rag_search"
EVIDENCE_TOOLS = frozenset({RAG_SEARCH, "rag_retrieve_evidence"})
MCP_TOOL_SEPARATOR = "__"
OUTCOME_SOURCE_ANSWER = "agent_answer"
OUTCOME_SOURCE_INFERRED = "inferred"
APPROVAL_SKIPPED = "approval_skipped"
# 終わった（in_progress=false）が完了でない Run のうち、取り消して閉じる状態（回答前の確認の待ちを
# 残さない）。承認待ちは画面で判断されうるので取り消さない。
CANCEL_WHEN_SETTLED = frozenset({"waiting_confirmation"})
_QUESTION_ENDINGS = ("？", "?")
# Agent の最終の検証が回答の末尾に足す定型の注記（推定で最後の行を見るときに除く。#1305）。
_AGENT_NOTICES = frozenset(
    {
        "この回答は資料との照合を終えられず、本質問の条件への該当は確認できていません。",
        "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。",
    }
)
_ERROR_MESSAGES = {
    "timeout": "Agent の Run が {timeout:g} 秒以内に終わりませんでした。",
    "waiting_approval": "Agent の Run が承認待ちで止まりました。",
    "waiting_confirmation": "Agent の Run が回答前の確認で止まりました。",
    APPROVAL_SKIPPED: "Agent の Run が承認の要るツール（{tools}）を呼ばずに終わりました。",
}


class AgentEvaluationError(RuntimeError):
    """利用者へ返す失敗（exit code 2）。"""


@dataclass(frozen=True)
class AgentTurn:
    """1 回の Run の回答を、RAG の検索・回答の応答の形にしたもの。"""

    response: SearchResponse
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
    # 旧版も含めて検索した（`include_superseded: true` の）根拠のツールの成功した
    # 呼び出しの数（#1392）。
    superseded_searches: int = 0
    # 使った業務 Agent の公開の版（対象構成の記録。#3240）。
    agent_version: int | None = None
    # 承認が要るため呼ばなかったツール（#3240 の Q7）。
    skipped_approval_tools: list[str] = field(default_factory=list)
    # 見る人の今の権限で見られないため観測から除かれた根拠の数（採点に入らない。#3252）。
    withheld_evidence_count: int = 0
    # 1 件でページの予算を超えるため本文を省かれた根拠の数（本文の条件を照合できない）。
    evidence_text_omitted: int = 0
    # 時間内に終わらず取り消したときの `agent_cancel_run` の結果（cancelled / already_* ）。
    cancel_result: str | None = None

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
            "agent_version": self.agent_version,
            "skipped_approval_tools": list(self.skipped_approval_tools),
            "withheld_evidence_count": self.withheld_evidence_count,
            "evidence_text_omitted": self.evidence_text_omitted,
            "cancel_result": self.cancel_result,
        }


# ---- 公開の出力から回答を組み立てる ---------------------------------------------------------


def base_tool_name(name: str) -> str:
    """MCP 接続のツールの名前（`<接続>__<ツール>`）のツールの部分。"""
    return name.rsplit(MCP_TOOL_SEPARATOR, 1)[-1]


def _records(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def tool_call_counts(observation: Mapping[str, Any]) -> dict[str, int]:
    """ツールごとの呼び出しの数（失敗・承認で呼ばなかった呼び出しを含む）。"""
    counts: dict[str, int] = {}
    for call in _records(observation.get("tool_calls")):
        if call.get("name"):
            name = base_tool_name(str(call["name"]))
            counts[name] = counts.get(name, 0) + 1
    return counts


def superseded_search_count(observation: Mapping[str, Any]) -> int:
    """旧版も含めて検索した（`include_superseded: true`）根拠のツールの成功した呼び出しの数。

    #1392。失敗した呼び出しと、根拠のツール以外（本文を読むツールなど）は数えない。
    """
    return sum(
        1
        for call in _records(observation.get("tool_calls"))
        if base_tool_name(str(call.get("name") or "")) in EVIDENCE_TOOLS
        and call.get("status") == "completed"
        and call.get("include_superseded") is True
    )


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


def answer_outcome(run: Mapping[str, Any]) -> str | None:
    """`agent_get_run` の対応（`outcome`。#1305）。無い・語彙に無ければ None。"""
    value = run.get("outcome")
    return value if isinstance(value, str) and value in EVALUATION_OUTCOMES else None


def skipped_approval_tools(run: Mapping[str, Any], observation: Mapping[str, Any]) -> list[str]:
    """承認が要るため呼ばなかったツール（`skipped_approval_tools` と観測の `approval_skipped`）。"""
    names: list[str] = []
    listed = run.get("skipped_approval_tools")
    observed = [
        call.get("name")
        for call in _records(observation.get("tool_calls"))
        if call.get("status") == APPROVAL_SKIPPED
    ]
    for name in [*(listed if isinstance(listed, list) else []), *observed]:
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _locator_metadata(value: object) -> dict[str, Any]:
    """根拠の場所（RAG の `EvidenceLocator` と同じ名前）。値の無い項目は入れない。"""
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if item is not None}


def evidence_citations(observation: Mapping[str, Any]) -> list[RetrievedChunk]:
    """観測の根拠（Run の RAG の根拠のツールと読み取りのツールの根拠。`rank` の順・重複なし）。

    重複の除去と、同じ chunk を後で読んだ本文をつなぐのは Agent の観測が済ませている。文書と
    chunk の ID の無い根拠は数えない（照合も重複の除去もできない）。
    """
    citations: list[RetrievedChunk] = []
    seen: set[tuple[str, str]] = set()
    items = sorted(
        _records(observation.get("evidence")), key=lambda item: _int(item.get("rank")) or 0
    )
    for item in items:
        document_id = str(item.get("document_id") or "")
        chunk_id = str(item.get("chunk_id") or "")
        if not document_id or not chunk_id or (document_id, chunk_id) in seen:
            continue
        seen.add((document_id, chunk_id))
        citations.append(
            RetrievedChunk(
                document_id=document_id,
                chunk_id=chunk_id,
                text=str(item.get("text") or ""),
                # 観測はスコアを出さない（採点は順位で数え、スコアを使わない）。
                score=0.0,
                file_name=str(item["file_name"]) if item.get("file_name") else None,
                metadata=_locator_metadata(item.get("locator")),
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


def _usage_requests(observation: Mapping[str, Any]) -> int | None:
    usage = observation.get("usage")
    return _int(usage.get("requests")) if isinstance(usage, Mapping) else None


def _validation_status(observation: Mapping[str, Any]) -> str | None:
    status = observation.get("validation_status")
    return status if isinstance(status, str) else None


def agent_turn(
    run: Mapping[str, Any], observation: Mapping[str, Any], *, elapsed_ms: float
) -> AgentTurn:
    """完了した Run の公開の出力（`agent_get_run` と観測）を、RAG の評価が採点する応答にする。"""
    answer = run.get("answer")
    answer = answer if isinstance(answer, str) else ""
    details: dict[str, Any] = {}
    outcome_source = OUTCOME_SOURCE_INFERRED
    recorded = answer_outcome(run)
    if recorded is not None:
        details["outcome"] = recorded
        outcome_source = OUTCOME_SOURCE_ANSWER
    elif _asks_question(answer):
        details["outcome"] = "needs_clarification"
    details["agent"] = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "validation_status": _validation_status(observation),
        "outcome_source": outcome_source,
        "tool_calls": tool_call_counts(observation),
        "model_requests": _usage_requests(observation),
    }
    response = SearchResponse(
        answer=answer,
        citations=evidence_citations(observation),
        trace_id=str(run.get("run_id") or ""),
        elapsed_ms=elapsed_ms,
        diagnostics=SearchDiagnostics(answer=details),
    )
    return AgentTurn(response=response, outcome_source=outcome_source, elapsed_ms=elapsed_ms)


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


# ---- Agent の公開の MCP ------------------------------------------------------------------


class AgentMcp:
    """Agent の公開の MCP（Streamable HTTP の `POST`。JSON-RPC の `tools/call`）。

    HTTP の client と時計を差し替えられる（テスト用）。待ちは MCP の `wait_seconds` に任せる
    （サーバーが終わるまで待って返すので、こちらで間隔を置いて読み直さない）。
    """

    def __init__(
        self,
        client: httpx.Client,
        mcp_url: str,
        *,
        api_key: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._url = mcp_url
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        self._clock = clock
        self._ids = itertools.count(1)
        self._initialized = False

    def _post(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self._client.post(
            self._url,
            json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params},
            headers=self._headers,
        )
        if response.status_code >= 400:
            raise AgentEvaluationError(
                f"Agent の MCP（{method}）が失敗しました（HTTP {response.status_code}）: "
                f"{response.text[:300]}"
            )
        try:
            message = response.json()
        except ValueError as error:
            raise AgentEvaluationError(
                f"Agent の MCP（{method}）の応答が JSON ではありません。"
            ) from error
        if not isinstance(message, Mapping):
            raise AgentEvaluationError(f"Agent の MCP（{method}）の応答の形が読めません。")
        failure = message.get("error")
        if isinstance(failure, Mapping):
            raise AgentEvaluationError(
                f"Agent の MCP（{method}）がエラーを返しました: {str(failure.get('message'))[:300]}"
            )
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise AgentEvaluationError(f"Agent の MCP（{method}）の応答に result がありません。")
        return result

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._post(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "rag-agent-evaluation", "version": "1"},
            },
        )
        self._initialized = True

    def call(
        self, tool: str, arguments: Mapping[str, Any], *, schema_version: int
    ) -> Mapping[str, Any]:
        """ツールを呼び、構造化の出力（`structuredContent`）を返す。ツールのエラーは失敗にする。"""
        self._initialize()
        result = self._post("tools/call", {"name": tool, "arguments": dict(arguments)})
        content = result.get("structuredContent")
        if result.get("isError") is True:
            code = content.get("error_code") if isinstance(content, Mapping) else None
            message = content.get("message") if isinstance(content, Mapping) else None
            raise AgentEvaluationError(
                f"Agent の MCP のツール {tool} がエラーを返しました"
                f"（{code or 'error'}）: {str(message or '')[:300]}"
            )
        if not isinstance(content, Mapping):
            raise AgentEvaluationError(
                f"Agent の MCP のツール {tool} の出力に structuredContent がありません。"
            )
        version = content.get("schema_version")
        if version != schema_version:
            # 知らない形の出力を推測で読まない（「評価不能」にする）。
            raise AgentEvaluationError(
                f"Agent の MCP のツール {tool} の出力の版（schema_version={version}）に対応して"
                f"いません（対応は {schema_version}）。"
            )
        return content

    def _wait_seconds(self, deadline: float) -> int:
        return max(1, min(MAX_WAIT_SECONDS, math.ceil(deadline - self._clock())))

    def chat(
        self, *, agent_id: str, question: str, thread_id: str | None, deadline: float
    ) -> Mapping[str, Any]:
        arguments: dict[str, Any] = {
            "agent_id": agent_id,
            "question": question,
            "wait_seconds": self._wait_seconds(deadline),
        }
        if thread_id:
            arguments["thread_id"] = thread_id
        return self.call("agent_chat", arguments, schema_version=RUN_OUTPUT_SCHEMA_VERSION)

    def get_run(self, run_id: str, *, deadline: float) -> Mapping[str, Any]:
        return self.call(
            "agent_get_run",
            {"run_id": run_id, "wait_seconds": self._wait_seconds(deadline)},
            schema_version=RUN_OUTPUT_SCHEMA_VERSION,
        )

    def wait_run(self, run: Mapping[str, Any], *, deadline: float) -> Mapping[str, Any] | None:
        """Run が終わる（`in_progress=false`）まで読む。期限までに終わらなければ None。"""
        while run.get("in_progress") is True:
            if self._clock() >= deadline:
                return None
            run = self.get_run(str(run["run_id"]), deadline=deadline)
        return run

    def observation(self, run_id: str) -> dict[str, Any]:
        """Run の観測。根拠は `next_cursor` をたどって全件を 1 つにまとめる。"""
        cursor: str | None = None
        merged: dict[str, Any] | None = None
        for _page in range(MAX_OBSERVATION_PAGES):
            arguments: dict[str, Any] = {"run_id": run_id, "limit": OBSERVATION_PAGE_LIMIT}
            if cursor is not None:
                arguments["cursor"] = cursor
            page = self.call(
                "agent_get_run_observation", arguments, schema_version=OBSERVATION_SCHEMA_VERSION
            )
            evidence = _records(page.get("evidence"))
            if merged is None:
                merged = {**page, "evidence": list(evidence)}
            else:
                merged["evidence"].extend(evidence)
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                merged["next_cursor"] = None
                return merged
            cursor = next_cursor
        raise AgentEvaluationError(
            f"Agent の観測の根拠が {MAX_OBSERVATION_PAGES} ページで終わりませんでした。"
        )

    def cancel_run(self, run_id: str) -> str | None:
        """Run を取り消す（冪等）。結果（`result`）を返し、取り消せなかった失敗は None。"""
        with contextlib.suppress(AgentEvaluationError, httpx.HTTPError):
            output = self.call(
                "agent_cancel_run", {"run_id": run_id}, schema_version=CANCEL_SCHEMA_VERSION
            )
            result = output.get("result")
            return result if isinstance(result, str) else None
        return None


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


def _record_observation(record: RunRecord, observation: Mapping[str, Any]) -> None:
    record.validation_status = _validation_status(observation)
    record.model_requests = _usage_requests(observation)
    record.tool_calls = tool_call_counts(observation)
    record.superseded_searches = superseded_search_count(observation)
    record.agent_version = _int(observation.get("agent_version"))
    record.withheld_evidence_count = _int(observation.get("withheld_evidence_count")) or 0
    record.evidence_text_omitted = sum(
        1 for item in _records(observation.get("evidence")) if item.get("text_omitted") is True
    )


def _turn_error(
    case: EvaluationCase,
    records: Sequence[RunRecord],
    *,
    run_id: str,
    kind: str,
    index: int,
    run_timeout_seconds: float,
) -> CaseOutcome:
    """往復の失敗をケースの失敗にする（それまでの往復は採点しない。時間は往復の和）。"""
    template = _ERROR_MESSAGES.get(kind, "Agent の Run が {status} で終わりました。")
    message = template.format(
        timeout=run_timeout_seconds,
        status=kind,
        tools="・".join(records[-1].skipped_approval_tools),
    )
    result = case_error_result(
        case,
        trace_id=run_id,
        elapsed_ms=round(sum(item.elapsed_ms for item in records), 3),
        error_type=f"AgentRun{kind.title().replace('_', '')}",
        error_message=message + " run_id で Agent の実行履歴を確認してください。",
        error_stage=f"turn_{index}",
    )
    return CaseOutcome(result=result, runs=list(records))


def evaluate_case(
    api: AgentMcp,
    case: EvaluationCase,
    *,
    agent_id: str,
    labels: Mapping[str, str],
    run_timeout_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    api_clock: Callable[[], float] = time.monotonic,
) -> CaseOutcome:
    """1 ケースの最初の質問と返答を同じ会話の Run として流し、RAG の評価と同じ採点にかける。

    `clock` は往復の時間を測る時計、`api_clock` は Run の待ちの期限の時計（テストで差し替える）。
    """
    thread_id: str | None = None
    answers: list[tuple[SearchResponse, Mapping[str, str]]] = []
    sources: list[str] = []
    records: list[RunRecord] = []
    for index, (question, conditions) in enumerate(_case_turns(case, labels)):
        started = clock()
        deadline = api_clock() + run_timeout_seconds
        created = api.chat(
            agent_id=agent_id, question=question, thread_id=thread_id, deadline=deadline
        )
        run_id = str(created["run_id"])
        thread_id = str(created.get("thread_id") or "") or thread_id
        run = api.wait_run(created, deadline=deadline)
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
        if run is None or status in CANCEL_WHEN_SETTLED:
            record.cancel_result = api.cancel_run(run_id)
        if run is None or status != "completed":
            return _turn_error(
                case,
                records,
                run_id=run_id,
                kind=status,
                index=index,
                run_timeout_seconds=run_timeout_seconds,
            )
        observation = api.observation(run_id)
        _record_observation(record, observation)
        record.skipped_approval_tools = skipped_approval_tools(run, observation)
        if record.skipped_approval_tools:
            # 承認の要るツールを飛ばして答えた Run は、必要な操作をしていない（#3240 の Q7）。
            return _turn_error(
                case,
                records,
                run_id=run_id,
                kind=APPROVAL_SKIPPED,
                index=index,
                run_timeout_seconds=run_timeout_seconds,
            )
        turn = agent_turn(run, observation, elapsed_ms=elapsed)
        outcome = (turn.response.diagnostics.answer or {}).get("outcome")
        record.outcome = outcome if isinstance(outcome, str) else None
        record.outcome_source = turn.outcome_source
        answers.append((turn.response, conditions))
        sources.append(turn.outcome_source)
    result = score_case_answers(case, answers)
    return CaseOutcome(result=_with_outcome_sources(result, sources), runs=records)


def evaluate(
    api: AgentMcp,
    request: EvaluationRunRequest,
    *,
    agent_id: str,
    labels: Mapping[str, str],
    run_timeout_seconds: float,
    log: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    api_clock: Callable[[], float] = time.monotonic,
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
                api_clock=api_clock,
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
            f"handling={outcome.result.response_decision_accuracy}"
        )
    metrics = summarize_case_results(results, thresholds=request.thresholds)
    return metrics.model_copy(update={"metrics_version": AGENT_EVALUATION_METRICS_VERSION}), records


def outcome_source_counts(records: Sequence[RunRecord]) -> dict[str, int]:
    """完了した Run の対応の出所ごとの数（agent_answer / inferred）。"""
    counts: dict[str, int] = {}
    for record in records:
        if record.outcome_source:
            counts[record.outcome_source] = counts.get(record.outcome_source, 0) + 1
    return counts


# ---- 比較の要約 ---------------------------------------------------------------------------

SUMMARY_METRICS: tuple[tuple[str, str], ...] = (
    ("required_evidence_recall", "必要な根拠の取得率"),
    ("mrr", "正解文書の順位（MRR）"),
    ("answer_correctness", "回答の正確性"),
    ("requirement_coverage", "回答の網羅性"),
    ("claim_support_rate", "根拠への忠実性"),
    ("response_decision_accuracy", "回答・確認・保留の判断の正しさ"),
)
REASONING_SUMMARY_METRICS = SUMMARY_METRICS
# 勧めてはいけない操作を回答が勧めた、という失敗の理由（EvaluationFailureReason）。
FORBIDDEN_ACTION_REASON = "forbidden_action"


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
    # 危険な回答のケースは、失敗の理由に勧めてはいけない操作（`forbidden_action`）が付いたケース。
    # 今の採点（六指標）はこの理由を付けないため、該当が無ければ 0 ではなく測れない（None）とする。
    dangerous = [
        case.get("case_id")
        for case in succeeded
        if FORBIDDEN_ACTION_REASON in (case.get("failure_reasons") or [])
    ]
    summary: dict[str, Any] = {
        "case_count": data.get("case_count"),
        "error_count": data.get("error_count"),
        **{metric: data.get(metric) for metric, _ in SUMMARY_METRICS},
        "dangerous_case_count": len(dangerous) if dangerous else None,
        "dangerous_case_ids": dangerous,
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
                    "handling_correct_rate": item.get("metrics", {}).get(
                        "response_decision_accuracy"
                    ),
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
        for metric, name in SUMMARY_METRICS:
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
            f"{category}: 回答・確認・保留の判断の正しさ",
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
                f"{split} / {name}: 必要な根拠の取得率",
                [groups[label].get(name, {}).get("required_evidence_recall") for label in labels],
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
    run.add_argument(
        "--agent-mcp-url", default=DEFAULT_AGENT_MCP_URL, help="Agent の MCP（POST /api/mcp）"
    )
    run.add_argument(
        "--agent-id", required=True, help="使う業務 Agent（公開済み。agent_list の id）"
    )
    run.add_argument(
        "--api-key-env",
        default=DEFAULT_API_KEY_ENV,
        help=f"Agent の MCP を呼ぶ API キーを読む環境変数（既定 {DEFAULT_API_KEY_ENV}）",
    )
    run.add_argument("--guides", type=Path, help="既知の条件の名前に使う業務ガイド")
    run.add_argument("--case", action="append", default=[], help="流すケースの id（複数可）")
    run.add_argument("--split", choices=["dev", "holdout"], help="流す区分")
    run.add_argument("--run-timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS)
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


def _api_key(name: str) -> str:
    key = os.environ.get(name, "").strip()
    if not key:
        raise AgentEvaluationError(
            f"環境変数 {name} に Agent の MCP の API キー（使える製品に agent を含むキー）を"
            "設定してください。"
        )
    return key


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
    api_key = _api_key(args.api_key_env)
    # Run の待ちは MCP の wait_seconds（最大 300 秒）なので、HTTP の timeout はそれより長くする。
    with httpx.Client(
        timeout=MAX_WAIT_SECONDS + REQUEST_TIMEOUT_SECONDS, trust_env=False
    ) as client:
        api = AgentMcp(client, args.agent_mcp_url, api_key=api_key)
        metrics, records = evaluate(
            api,
            request,
            agent_id=args.agent_id,
            labels=labels,
            run_timeout_seconds=args.run_timeout,
            log=lambda line: print(line, file=sys.stderr),
        )
    data = metrics.model_dump(mode="json")
    payload = {
        "data": data,
        "agent": {
            "agent_id": args.agent_id,
            # 対象構成の記録（Run に使った業務 Agent の公開の版。#3240）。
            "agent_versions": sorted(
                {record.agent_version for record in records if record.agent_version is not None}
            ),
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
