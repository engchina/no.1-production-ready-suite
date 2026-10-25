"""製品横断の評価（Agent の Run の比較評価）の採点・集計の基線（#3240 の段 1・段 2）。

`tests/fixtures/agent_evaluation_baseline/` の合成の fixture（ケースと往復ごとの Run の ID）と、
共有の fixture `platform/contracts/mcp/fixtures/agent-evaluation-baseline.json` の公開の
Agent MCP の出力（`agent_get_run`・`agent_get_run_observation`・`agent_cancel_run`。Agent の
`tests/test_evaluation_baseline_observation.py` が本物の MCP の入口で作り、契約に合うことを
確かめる）を、今の CLI（`app.rag.agent_evaluation_cli.evaluate`）に Agent の MCP の再生
（MockTransport）で流し、採点（ケースごと）・集計（評価セットごと）・失敗の扱い・旧形式の扱いが
記録した値と一致するかを確かめる。CLI は公開の出力だけを読む（共有の fixture の `source`
〔Agent の内部の記録〕は読まない）。評価を `suite/evaluation/` へ移すときは、同じ fixture で同じ値を
出すこと（意図した差は採点の version と説明に残す）。fixture が保証する範囲と更新の仕方は
fixture の README.md。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.agent_evaluation_cli import (
    AGENT_EVALUATION_METRICS_VERSION,
    CANCEL_SCHEMA_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    RUN_OUTPUT_SCHEMA_VERSION,
    AgentMcp,
    RunRecord,
    evaluate,
    outcome_source_counts,
    summarize_result,
    summary_markdown,
    superseded_case_summary,
)
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EVALUATION_METRICS_VERSION,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRunRequest,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_evaluation_baseline"
CONTRACTS = Path(__file__).resolve().parents[3] / "platform" / "contracts" / "mcp"
PUBLIC_FIXTURE = CONTRACTS / "fixtures" / "agent-evaluation-baseline.json"
AGENT_CONTRACT = CONTRACTS / "agent-tools.json"
# 再生の条件（README.md「再生の条件」）。時間切れの Run はこの秒数で打ち切る。
RUN_TIMEOUT_SECONDS = 2.0
AGENT_ID = "agent-baseline"
# CLI が読む公開の出力（共有の fixture の Run ごとの項目）。
PUBLIC_TOOLS = ("agent_get_run", "agent_get_run_observation", "agent_cancel_run")
# Run の観測の種類（fixture の turns[].observation）。completed 以外はケースの失敗になる
# （completed でも承認の要るツールを呼ばなかった Run はケースの失敗。#3240 の Q7）。
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


def load_public_runs() -> dict[str, dict[str, Any]]:
    """共有の fixture の Run ごとの公開の出力（CLI が読む項目だけ）。"""
    data = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    return {
        entry["run_id"]: {tool: entry[tool] for tool in PUBLIC_TOOLS if tool in entry}
        for entry in data["runs"]
    }


CASE_FIXTURES = load_case_fixtures()
PUBLIC_RUNS = load_public_runs()
SUMMARIES: list[dict[str, Any]] = _load("summaries.json")["suites"]
DEPENDENCIES: dict[str, Any] = _load("agent_internal_dependencies.json")
AGENT_TOOLS: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in json.loads(AGENT_CONTRACT.read_text(encoding="utf-8"))["tools"]
}


# ---- 再生 ------------------------------------------------------------------------------------


def _check_arguments(tool: str, arguments: Mapping[str, Any]) -> None:
    """CLI が送る引数が契約（agent-tools.json の inputSchema）に合う。"""
    schema = AGENT_TOOLS[tool]["inputSchema"]
    properties: dict[str, Any] = schema["properties"]
    assert set(schema.get("required", [])) <= set(arguments), (tool, arguments)
    assert set(arguments) <= set(properties), (tool, arguments)
    for name, value in arguments.items():
        options = properties[name].get("anyOf", [properties[name]])
        matched = False
        for option in options:
            if option.get("type") == "null":
                matched = matched or value is None
            elif option.get("type") == "string" and isinstance(value, str):
                pattern = option.get("pattern")
                matched = matched or pattern is None or re.fullmatch(pattern, value) is not None
            elif option.get("type") == "integer" and isinstance(value, int):
                low, high = option.get("minimum", value), option.get("maximum", value)
                matched = matched or low <= value <= high
        assert matched, (tool, name, value)


def _tool_result(content: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
        "structuredContent": dict(content),
        "isError": False,
    }


class ReplayAgentMcp:
    """Agent の公開の MCP（`POST /api/mcp`）を、共有の fixture の公開の出力で再生する。

    `agent_chat` ごとに fixture の次の往復を出し（`api_error` は HTTP の失敗）、`agent_get_run`・
    `agent_get_run_observation`・`agent_cancel_run` はその往復の Run の公開の出力を返す（時間切れの
    Run の `agent_get_run` は running のまま）。CLI の引数が契約に合うことを確かめる。
    """

    def __init__(
        self,
        fixtures: Sequence[Mapping[str, Any]],
        runs: Mapping[str, Mapping[str, Any]] = PUBLIC_RUNS,
    ) -> None:
        self._turns: Iterator[Mapping[str, Any]] = iter(
            [turn for fixture in fixtures for turn in fixture["turns"]]
        )
        self._runs = runs
        self.chat_requests: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.authorizations: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/mcp"
        self.authorizations.add(request.headers.get("authorization", ""))
        message = json.loads(request.content)
        if message["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"protocolVersion": message["params"]["protocolVersion"]},
                },
            )
        assert message["method"] == "tools/call"
        tool = message["params"]["name"]
        arguments = message["params"]["arguments"]
        _check_arguments(tool, arguments)
        if tool == "agent_chat":
            # 待つ秒数は再生に関わらない（時計で決まる）ので、送った依頼から除いて記録する。
            self.chat_requests.append(
                {key: value for key, value in arguments.items() if key != "wait_seconds"}
            )
            turn = next(self._turns)
            if turn["observation"] == "api_error":
                return httpx.Response(int(turn["http_status"]), text="synthetic failure")
            content = self._runs[turn["run_id"]]["agent_get_run"]
        else:
            run_id = arguments["run_id"]
            if tool == "agent_cancel_run":
                self.cancelled.append(run_id)
            content = self._runs[run_id][tool]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": _tool_result(content)}
        )


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
    runs: Mapping[str, Mapping[str, Any]] = PUBLIC_RUNS,
) -> tuple[EvaluationRunRequest, EvaluationMetrics, list[RunRecord], ReplayAgentMcp]:
    agent = ReplayAgentMcp(fixtures, runs)
    client = httpx.Client(transport=httpx.MockTransport(agent))
    api_clock = _api_clock()
    api = AgentMcp(client, "http://agent/api/mcp", api_key="prak_synthetic", clock=api_clock)
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
        api_clock=api_clock,
    )
    assert agent.authorizations <= {"Bearer prak_synthetic"}
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
    result: EvaluationCaseResult, records: Sequence[RunRecord], agent: ReplayAgentMcp
) -> dict[str, Any]:
    """今の CLI が Agent の Run から作る項目（Run の ID・診断・Run の記録・送った依頼）。"""
    return {
        "trace_id": result.trace_id,
        "turn_trace_ids": [turn.trace_id for turn in result.turn_results],
        "diagnostics_answer": result.model_dump(mode="json")["diagnostics"]["answer"],
        "error_message": result.error_message,
        "runs": [record.as_json() for record in records],
        "chat_requests": agent.chat_requests,
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


def case_result(
    fixture: Mapping[str, Any], runs: Mapping[str, Mapping[str, Any]] = PUBLIC_RUNS
) -> dict[str, Any]:
    _request, metrics, records, agent = replay([fixture], runs=runs)
    result = metrics.case_results[0]
    return {"scores": case_scores(result), "cli": cli_details(result, records, agent)}


def _run_ids(fixture: Mapping[str, Any]) -> list[str]:
    return [turn["run_id"] for turn in fixture["turns"] if "run_id" in turn]


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
        for turn in fixture["turns"]:
            # Run を作れた往復は共有の fixture の公開の出力を指す（Agent の内部の記録は持たない）。
            assert "run" not in turn, fixture_id
            assert (turn["observation"] == "api_error") == ("run_id" not in turn), fixture_id
            if "run_id" in turn:
                assert turn["run_id"] in PUBLIC_RUNS, fixture_id
    # 全部のケースを流す評価セットがある（分母に全部の観測の種類が入る）。
    everything = next(suite for suite in SUMMARIES if suite["id"] == "all")
    assert sorted(everything["case_fixtures"]) == sorted(CASE_FIXTURES)
    assert {final_observation(fixture) for fixture in CASE_FIXTURES.values()} == OBSERVATIONS
    # 共有の fixture の Run は、どれかのケースが使う。
    used = {run_id for fixture in CASE_FIXTURES.values() for run_id in _run_ids(fixture)}
    assert used == set(PUBLIC_RUNS)


def test_cli_reads_the_output_versions_of_the_public_contract() -> None:
    """CLI が読む出力の形の版は、契約の schema_version と同じ（知らない版は採点しない）。"""

    def version(tool: str) -> int:
        return int(AGENT_TOOLS[tool]["outputSchema"]["properties"]["schema_version"]["default"])

    assert version("agent_chat") == version("agent_get_run") == RUN_OUTPUT_SCHEMA_VERSION
    assert version("agent_get_run_observation") == OBSERVATION_SCHEMA_VERSION
    assert version("agent_cancel_run") == CANCEL_SCHEMA_VERSION
    for entry in PUBLIC_RUNS.values():
        assert entry["agent_get_run"]["schema_version"] == RUN_OUTPUT_SCHEMA_VERSION
        assert entry["agent_get_run_observation"]["schema_version"] == OBSERVATION_SCHEMA_VERSION


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


def test_agent_path_scoring_version_marks_the_agent_rules() -> None:
    """Agent の経路の採点の版は、RAG の六指標の版に Agent の経路の規則の版を足したもの。

    RAG 自身の評価の版（`metrics_version`）は変えない。
    """
    expected = f"{EVALUATION_METRICS_VERSION}+agent_v2"
    assert expected == AGENT_EVALUATION_METRICS_VERSION
    for suite in SUMMARIES:
        assert suite["expected"]["metrics"]["metrics_version"] == AGENT_EVALUATION_METRICS_VERSION


@pytest.mark.parametrize("suite", SUMMARIES, ids=lambda suite: str(suite["id"]))
def test_failed_cases_are_outside_the_metric_denominators(suite: dict[str, Any]) -> None:
    """失敗・時間切れ・取消・承認待ち・承認の要るツールを呼ばなかった Run・作成の失敗は
    error_count に入り、指標の分母に入らない。"""
    fixtures = [CASE_FIXTURES[fixture_id] for fixture_id in suite["case_fixtures"]]
    _request, metrics, _records, _agent = replay(fixtures, suite.get("thresholds"))
    failed = [fixture for fixture in fixtures if fixture["expected"]["scores"]["status"] == "error"]
    assert metrics.error_count == len(failed)
    assert {fixture["id"] for fixture in fixtures if final_observation(fixture) != "completed"} <= {
        fixture["id"] for fixture in failed
    }
    for name in EVALUATION_METRIC_NAMES:
        assert metrics.metric_case_counts[name] <= metrics.case_count - metrics.error_count
    if failed:
        assert metrics.passed is False


def test_completed_run_that_skipped_an_approval_tool_is_a_case_failure() -> None:
    """承認の要るツールを呼ばずに答えた公開の MCP の Run は、ケースの失敗（#3240 の Q7）。"""
    fixture = CASE_FIXTURES["approval-skipped-run"]
    run = PUBLIC_RUNS[_run_ids(fixture)[0]]
    # 回答・根拠・対応はそろっている（成功に数えると、必要な操作をしていない回答を採点する）。
    assert run["agent_get_run"]["status"] == "completed"
    assert run["agent_get_run"]["outcome"] == "answered"
    assert run["agent_get_run"]["skipped_approval_tools"]
    actual = case_result(fixture)
    assert actual["scores"]["status"] == "error"
    assert actual["scores"]["error_type"] == "AgentRunApprovalSkipped"
    assert actual["scores"]["error_stage"] == "turn_0"
    assert actual["cli"]["runs"][0]["skipped_approval_tools"] == ["ext__record_write"]
    # 観測の approval_skipped だけでも失敗にする（skipped_approval_tools は MCP の Run の分だけ）。
    observed_only = copy.deepcopy(dict(PUBLIC_RUNS))
    observed_only[_run_ids(fixture)[0]]["agent_get_run"]["skipped_approval_tools"] = []
    assert case_result(fixture, observed_only)["scores"]["error_type"] == "AgentRunApprovalSkipped"


# ---- 公開の Agent MCP の項目への依存 ---------------------------------------------------------


def _observation(run: dict[str, Any]) -> dict[str, Any]:
    observation: dict[str, Any] = run["agent_get_run_observation"]
    return observation


def _strip_citation_ids(run: dict[str, Any]) -> None:
    for item in [*_observation(run)["evidence"], *run["agent_get_run"]["sources"]]:
        item["document_id"] = None
        item["chunk_id"] = None


def _strip_locators(run: dict[str, Any]) -> None:
    for item in _observation(run)["evidence"]:
        item["locator"] = {}


def _strip_answer_outcome(run: dict[str, Any]) -> None:
    run["agent_get_run"]["outcome"] = None
    _observation(run)["outcome"] = None


def _strip_validation_status(run: dict[str, Any]) -> None:
    _observation(run)["validation_status"] = None


def _strip_usage(run: dict[str, Any]) -> None:
    _observation(run)["usage"] = None


def _strip_include_superseded(run: dict[str, Any]) -> None:
    for call in _observation(run)["tool_calls"]:
        call["include_superseded"] = None


ABLATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "strip_citation_ids": _strip_citation_ids,
    "strip_locators": _strip_locators,
    "strip_answer_outcome": _strip_answer_outcome,
    "strip_validation_status": _strip_validation_status,
    "strip_usage": _strip_usage,
    "strip_include_superseded": _strip_include_superseded,
}


def _ablated_runs(fixture: Mapping[str, Any], ablation: str) -> dict[str, dict[str, Any]]:
    runs = copy.deepcopy(dict(PUBLIC_RUNS))
    for run_id in _run_ids(fixture):
        ABLATIONS[ablation](runs[run_id])
    return runs


def _has_field(tool_output: Mapping[str, Any], path: str) -> bool:
    """公開の出力の schema に項目があるか（`evidence[].locator` の形の路）。"""
    schema = AGENT_TOOLS[tool_output["tool"]]["outputSchema"]
    node: Mapping[str, Any] = schema
    for part in path.split("."):
        name = part.removesuffix("[]")
        properties = node.get("properties", {})
        if name not in properties:
            return False
        node = properties[name]
        if part.endswith("[]"):
            node = node["items"]
        reference = node.get("$ref") or next(
            (option["$ref"] for option in node.get("anyOf", []) if "$ref" in option), None
        )
        if reference:
            node = schema["$defs"][reference.rsplit("/", 1)[-1]]
    return True


def test_every_dependency_names_its_public_fields_and_an_ablation() -> None:
    """依存の一覧の各項目が、公開の Agent MCP の項目（契約にある）と、確かめる消し方を持つ。"""
    for dependency in DEPENDENCIES["dependencies"]:
        assert dependency["public_agent_mcp"] == "public", dependency["id"]
        assert dependency["observations"], dependency["id"]
        assert dependency["used_by"], dependency["id"]
        assert dependency["public_fields"], dependency["id"]
        for field in dependency["public_fields"]:
            tool, _, path = field.partition(".")
            assert _has_field({"tool": tool}, path), (dependency["id"], field)
        assert dependency["ablation"] in ABLATIONS, dependency["id"]
        assert dependency["witness"]["fixture"] in CASE_FIXTURES, dependency["id"]
    for item in DEPENDENCIES["not_ablatable"]:
        assert item["id"] and item["note"], item
        assert item["public_agent_mcp"] in {"public", "partial"}, item["id"]
        for field in item.get("public_fields", []):
            tool, _, path = field.partition(".")
            assert _has_field({"tool": tool}, path), (item["id"], field)
    for item in DEPENDENCIES["not_added"]:
        assert item["id"] and item["note"] and item["observations"], item
        assert item["public_agent_mcp"] == "not_added", item["id"]


@pytest.mark.parametrize(
    "dependency", DEPENDENCIES["dependencies"], ids=lambda dependency: str(dependency["id"])
)
def test_scores_depend_only_on_public_agent_mcp_fields(dependency: dict[str, Any]) -> None:
    """公開の Agent MCP の項目を消すと、一覧に書いた項目（だけ）が変わる。

    段 1 で Run の内部の記録を消して確かめた変化と同じ項目が、公開の出力の項目で変わる（CLI は
    公開の出力だけで採点する）。
    """
    witness = dependency["witness"]
    fixture = CASE_FIXTURES[witness["fixture"]]
    baseline = case_result(fixture)
    ablated = case_result(fixture, _ablated_runs(fixture, dependency["ablation"]))
    changed = {
        field for field in SCORE_FIELDS if baseline["scores"][field] != ablated["scores"][field]
    }
    assert changed == set(witness["score_changes"])
    for field in witness["run_changes"]:
        before = [run[field] for run in baseline["cli"]["runs"]]
        after = [run[field] for run in ablated["cli"]["runs"]]
        assert before != after, field
