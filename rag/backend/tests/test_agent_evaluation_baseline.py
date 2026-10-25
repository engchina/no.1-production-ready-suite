"""製品横断の評価（Agent の Run の比較評価）の採点・集計の基線（#3240 の段 1）。

`tests/fixtures/agent_evaluation_baseline/` の合成の fixture（ケースと、保存した Run の観測）を、
今の CLI（`app.rag.agent_evaluation_cli.evaluate`）に Agent の API の再生（MockTransport）で流し、
採点（ケースごと）・集計（評価セットごと）・失敗の扱い・旧形式の扱いが記録した値と一致するかを
確かめる。評価を `suite/evaluation/` へ移すときは、同じ fixture で同じ値を出すこと（意図した差は
採点の version と説明に残す）。fixture が保証する範囲と更新の仕方は fixture の README.md。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.agent_evaluation_cli import (
    RAG_SEARCH,
    READ_CHUNK_LOCATION_KEYS,
    AgentApi,
    RunRecord,
    evaluate,
    outcome_source_counts,
    summarize_result,
    summary_markdown,
    superseded_case_summary,
)
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRunRequest,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_evaluation_baseline"
# 再生の条件（README.md「再生の条件」）。時間切れの Run はこの秒数で打ち切る。
RUN_TIMEOUT_SECONDS = 2.0
AGENT_ID = "agent-baseline"
# Run の観測の種類（fixture の turns[].observation）。completed 以外はケースの失敗になる。
OBSERVATIONS = frozenset(
    {"completed", "failed", "timeout", "cancelled", "waiting_approval", "api_error"}
)
# 移設の後も一致させる採点の項目（ケースの結果のうち、Agent の Run の ID・本文を含まないもの）。
SCORE_FIELDS: tuple[str, ...] = (
    "status",
    "category",
    "split",
    "reasoning_type",
    "hops",
    "retrieved_document_ids",
    "relevant_document_ids",
    "hit_document_ids",
    "required_evidence_recall",
    "mrr",
    "answer_correctness",
    "requirement_coverage",
    "claim_support_rate",
    "response_decision_accuracy",
    "expected_decisions",
    "observed_outcome",
    "outcome_source",
    "missing_evidence",
    "turn_results",
    "answer_evaluation",
    "guardrail_warnings",
    "failure_reasons",
    "elapsed_ms",
    "error_type",
    "error_stage",
)


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_case_fixtures() -> dict[str, dict[str, Any]]:
    fixtures: dict[str, dict[str, Any]] = {}
    for path in sorted((FIXTURES / "cases").glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixtures[fixture["id"]] = fixture
    return fixtures


CASE_FIXTURES = load_case_fixtures()
SUMMARIES: list[dict[str, Any]] = _load("summaries.json")["suites"]
DEPENDENCIES: dict[str, Any] = _load("agent_internal_dependencies.json")


# ---- 再生 ------------------------------------------------------------------------------------


class ReplayAgent:
    """Agent の公開の HTTP API を、fixture の保存した Run で再生する（MockTransport の handler）。

    `POST /api/runs` ごとに fixture の次の往復を出し（`api_error` は HTTP の失敗）、
    `GET /api/runs/{id}` はその往復の保存した Run を返す（時間切れの Run は running のまま）。
    """

    def __init__(self, fixtures: Sequence[Mapping[str, Any]]) -> None:
        self._turns: Iterator[Mapping[str, Any]] = iter(
            [turn for fixture in fixtures for turn in fixture["turns"]]
        )
        self._runs: dict[str, Mapping[str, Any]] = {}
        self.run_requests: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        if path == "/runs" and request.method == "POST":
            self.run_requests.append(json.loads(request.content))
            turn = next(self._turns)
            if turn["observation"] == "api_error":
                return httpx.Response(int(turn["http_status"]), text="synthetic failure")
            run = turn["run"]
            self._runs[run["id"]] = run
            return httpx.Response(
                200,
                json={"data": {"id": run["id"], "status": "queued", "thread_id": run["thread_id"]}},
            )
        run_id = path.split("/")[2]
        if path.endswith("/cancel"):
            self.cancelled.append(run_id)
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(200, json={"data": self._runs[run_id]})


def _turn_clock(fixtures: Sequence[Mapping[str, Any]]) -> Callable[[], float]:
    """`evaluate` の時計。往復ごとに 0 秒と、fixture の elapsed_ms の時刻を返す。

    `evaluate_case` は往復の始まりと Run の終わりで 1 回ずつ時計を読む（Run を作れなかった往復は
    始まりの 1 回だけ）。
    """
    ticks: list[float] = []
    for fixture in fixtures:
        for turn in fixture["turns"]:
            ticks.append(0.0)
            if turn["observation"] != "api_error":
                ticks.append(float(turn["elapsed_ms"]) / 1000)
    queue = iter(ticks)
    return lambda: next(queue)


def _api_clock() -> Callable[[], float]:
    """Run の待ちの時計（読むたびに 1 秒進む）。時間切れの Run は数回の取得で打ち切る。"""
    state = {"now": 0.0}

    def clock() -> float:
        state["now"] += 1.0
        return state["now"]

    return clock


def replay(
    fixtures: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, float] | None = None,
) -> tuple[EvaluationRunRequest, EvaluationMetrics, list[RunRecord], ReplayAgent]:
    agent = ReplayAgent(fixtures)
    client = httpx.Client(transport=httpx.MockTransport(agent), base_url="http://agent")
    api = AgentApi(
        client, "http://agent", poll_interval_seconds=0, sleep=lambda _: None, clock=_api_clock()
    )
    request = EvaluationRunRequest.model_validate(
        {"cases": [fixture["case"] for fixture in fixtures], "thresholds": thresholds}
    )
    metrics, records = evaluate(
        api,
        request,
        agent_id=AGENT_ID,
        labels={},
        run_timeout_seconds=RUN_TIMEOUT_SECONDS,
        log=lambda _: None,
        clock=_turn_clock(fixtures),
    )
    return request, metrics, records, agent


# ---- 比べる形 --------------------------------------------------------------------------------


def case_scores(result: EvaluationCaseResult) -> dict[str, Any]:
    """移設の後も一致させる採点の項目（往復の trace_id は Agent の Run の ID なので除く）。"""
    dumped = result.model_dump(mode="json")
    scores = {field: dumped[field] for field in SCORE_FIELDS}
    scores["turn_results"] = [
        {key: value for key, value in turn.items() if key != "trace_id"}
        for turn in dumped["turn_results"]
    ]
    return scores


def cli_details(
    result: EvaluationCaseResult, records: Sequence[RunRecord], agent: ReplayAgent
) -> dict[str, Any]:
    """今の CLI が Agent の Run から作る項目（Run の ID・診断・Run の記録・送った依頼）。"""
    return {
        "trace_id": result.trace_id,
        "turn_trace_ids": [turn.trace_id for turn in result.turn_results],
        "diagnostics_answer": result.model_dump(mode="json")["diagnostics"]["answer"],
        "error_message": result.error_message,
        "runs": [record.as_json() for record in records],
        "run_requests": agent.run_requests,
        "cancelled_run_ids": agent.cancelled,
    }


def final_observation(fixture: Mapping[str, Any]) -> str:
    return str(fixture["turns"][-1]["observation"])


def denominators(
    fixtures: Sequence[Mapping[str, Any]], metrics: EvaluationMetrics
) -> dict[str, Any]:
    """集計の分母（ケースの数・失敗の数・観測の種類ごとの数・指標ごとの測れた / 測れない数）。"""
    observations: dict[str, int] = {}
    for fixture in fixtures:
        name = final_observation(fixture)
        observations[name] = observations.get(name, 0) + 1
    succeeded = metrics.case_count - metrics.error_count
    return {
        "case_count": metrics.case_count,
        "error_count": metrics.error_count,
        "observations": dict(sorted(observations.items())),
        "measured": {name: metrics.metric_case_counts[name] for name in EVALUATION_METRIC_NAMES},
        "not_evaluable": {
            name: succeeded - metrics.metric_case_counts[name] for name in EVALUATION_METRIC_NAMES
        },
    }


def suite_result(suite: Mapping[str, Any]) -> dict[str, Any]:
    fixtures = [CASE_FIXTURES[fixture_id] for fixture_id in suite["case_fixtures"]]
    request, metrics, records, _agent = replay(fixtures, suite.get("thresholds"))
    data = metrics.model_dump(mode="json")
    summary = summarize_result(data)
    return {
        "metrics": metrics.model_dump(mode="json", exclude={"case_results"}),
        "denominators": denominators(fixtures, metrics),
        "cli": {
            "summary": summary,
            "summary_markdown": summary_markdown({"D": summary}).splitlines(),
            "outcome_sources": outcome_source_counts(records),
            "superseded_cases": superseded_case_summary(request.cases, records),
        },
    }


def case_result(fixture: Mapping[str, Any]) -> dict[str, Any]:
    _request, metrics, records, agent = replay([fixture])
    result = metrics.case_results[0]
    return {"scores": case_scores(result), "cli": cli_details(result, records, agent)}


# ---- fixture の整合 --------------------------------------------------------------------------


def test_fixtures_are_consistent() -> None:
    """fixture の id がケースの id と同じで、失敗した往復の後に往復を書いていない。"""
    assert CASE_FIXTURES
    for fixture_id, fixture in CASE_FIXTURES.items():
        assert fixture["case"]["id"] == fixture_id
        observations = [turn["observation"] for turn in fixture["turns"]]
        assert set(observations) <= OBSERVATIONS, fixture_id
        assert all(item == "completed" for item in observations[:-1]), fixture_id
        assert len(observations) <= 1 + len(fixture["case"].get("turns", [])), fixture_id
    # 全部のケースを流す評価セットがある（分母に全部の観測の種類が入る）。
    everything = next(suite for suite in SUMMARIES if suite["id"] == "all")
    assert sorted(everything["case_fixtures"]) == sorted(CASE_FIXTURES)
    assert {final_observation(fixture) for fixture in CASE_FIXTURES.values()} == OBSERVATIONS


# ---- ケースごとの採点 ------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", sorted(CASE_FIXTURES))
def test_case_scores_match_the_baseline(fixture_id: str) -> None:
    """ケースの採点（指標・対応・失敗の理由・失敗の種類）が基線と一致する。"""
    fixture = CASE_FIXTURES[fixture_id]
    assert case_result(fixture)["scores"] == fixture["expected"]["scores"]


@pytest.mark.parametrize("fixture_id", sorted(CASE_FIXTURES))
def test_case_cli_details_match_the_baseline(fixture_id: str) -> None:
    """今の CLI の Agent に固有の出力（診断・Run の記録・送った依頼・取消）が基線と一致する。

    段 4（旧形式の処理を suite の Agent の adapter へ移す）で形が変わる項目。変えるときは差を
    README.md と採点の version に残す。
    """
    fixture = CASE_FIXTURES[fixture_id]
    assert case_result(fixture)["cli"] == fixture["expected"]["cli"]


# ---- 集計 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("suite", SUMMARIES, ids=lambda suite: str(suite["id"]))
def test_summary_matches_the_baseline(suite: dict[str, Any]) -> None:
    """評価セットの集計（六指標・分母・内訳・合否）と CLI の比較の要約が基線と一致する。"""
    actual = suite_result(suite)
    assert actual["metrics"] == suite["expected"]["metrics"]
    assert actual["denominators"] == suite["expected"]["denominators"]
    assert actual["cli"] == suite["expected"]["cli"]


@pytest.mark.parametrize("suite", SUMMARIES, ids=lambda suite: str(suite["id"]))
def test_failed_cases_are_outside_the_metric_denominators(suite: dict[str, Any]) -> None:
    """失敗・時間切れ・取消・承認待ち・作成の失敗は error_count に入り、指標の分母に入らない。"""
    fixtures = [CASE_FIXTURES[fixture_id] for fixture_id in suite["case_fixtures"]]
    _request, metrics, _records, _agent = replay(fixtures, suite.get("thresholds"))
    failed = [fixture for fixture in fixtures if final_observation(fixture) != "completed"]
    assert metrics.error_count == len(failed)
    for name in EVALUATION_METRIC_NAMES:
        assert metrics.metric_case_counts[name] <= metrics.case_count - metrics.error_count
    if failed:
        assert metrics.passed is False


# ---- 公開の Agent MCP に無い観測への依存 -----------------------------------------------------


def _outputs(run: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for step in run.get("steps", []):
        result = step.get("tool_result") or {}
        output = result.get("output")
        if isinstance(output, dict):
            yield str(result.get("name") or ""), output


def _strip_citation_ids(run: dict[str, Any]) -> None:
    for _tool, output in _outputs(run):
        for item in [*output.get("evidence", []), *output.get("chunks", []), output]:
            for key in ("document_id", "chunk_id", "evidence_id"):
                item.pop(key, None)


def _strip_locators(run: dict[str, Any]) -> None:
    for _tool, output in _outputs(run):
        output.pop("locator", None)
        for item in output.get("evidence", []):
            item.pop("locator", None)
        for chunk in output.get("chunks", []):
            for key in READ_CHUNK_LOCATION_KEYS:
                chunk.pop(key, None)


def _strip_answer_outcome(run: dict[str, Any]) -> None:
    for artifact in run.get("artifacts", []):
        if artifact.get("kind") == "answer":
            artifact["content"].pop("outcome", None)


def _strip_rag_search_signals(run: dict[str, Any]) -> None:
    for tool, output in _outputs(run):
        if tool == RAG_SEARCH:
            for key in ("outcome", "clarifications", "insufficient_reason"):
                output.pop(key, None)


def _strip_validation_artifact(run: dict[str, Any]) -> None:
    run["artifacts"] = [
        artifact
        for artifact in run.get("artifacts", [])
        if artifact.get("kind") != "answer_validation"
    ]


def _strip_usage(run: dict[str, Any]) -> None:
    run.pop("usage", None)


def _strip_tool_arguments(run: dict[str, Any]) -> None:
    for step in run.get("steps", []):
        if isinstance(step.get("tool_call"), dict):
            step["tool_call"]["arguments"] = {}


ABLATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "strip_citation_ids": _strip_citation_ids,
    "strip_locators": _strip_locators,
    "strip_answer_outcome": _strip_answer_outcome,
    "strip_rag_search_signals": _strip_rag_search_signals,
    "strip_validation_artifact": _strip_validation_artifact,
    "strip_usage": _strip_usage,
    "strip_tool_arguments": _strip_tool_arguments,
}


def _ablated(fixture: Mapping[str, Any], ablation: str) -> dict[str, Any]:
    copied = copy.deepcopy(dict(fixture))
    for turn in copied["turns"]:
        if "run" in turn:
            ABLATIONS[ablation](turn["run"])
    return copied


def test_every_dependency_names_a_public_gap_and_an_ablation() -> None:
    """依存の一覧の各項目が、公開の Agent MCP での扱いと、確かめる消し方（または理由）を持つ。"""
    for dependency in DEPENDENCIES["dependencies"]:
        assert dependency["public_agent_mcp"] in {"absent", "partial"}, dependency["id"]
        assert dependency["observations"], dependency["id"]
        assert dependency["used_by"], dependency["id"]
        assert dependency["ablation"] in ABLATIONS, dependency["id"]
        assert dependency["witness"]["fixture"] in CASE_FIXTURES, dependency["id"]
    for item in DEPENDENCIES["not_ablatable"]:
        assert item["id"] and item["public_agent_mcp"] in {"absent", "partial"} and item["note"]


@pytest.mark.parametrize(
    "dependency", DEPENDENCIES["dependencies"], ids=lambda dependency: str(dependency["id"])
)
def test_scores_depend_on_agent_internal_observations(dependency: dict[str, Any]) -> None:
    """公開の Agent MCP に無い観測を消すと、一覧に書いた項目（だけ）が変わる。

    段 2 の設計（Agent の MCP の出力の拡張）で、どの観測を契約にするかの根拠にする。
    """
    witness = dependency["witness"]
    fixture = CASE_FIXTURES[witness["fixture"]]
    baseline = case_result(fixture)
    ablated = case_result(_ablated(fixture, dependency["ablation"]))
    changed = {
        field for field in SCORE_FIELDS if baseline["scores"][field] != ablated["scores"][field]
    }
    assert changed == set(witness["score_changes"])
    for field in witness["run_changes"]:
        before = [run[field] for run in baseline["cli"]["runs"]]
        after = [run[field] for run in ablated["cli"]["runs"]]
        assert before != after, field
