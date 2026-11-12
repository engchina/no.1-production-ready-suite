"""根拠の取得が返す検索・回答プロファイルの用語を、回答を作るモデルへ引き継ぐ（#2701）。

RAG は Agent 主体の取得（planning=caller）で回答を作らないので、承認済みの用語の説明は
根拠の取得の結果（`profile_knowledge`）で Agent に届く。組み込み Runtime はそれを根拠
（`evidence`）と分けたままモデルへ渡し、回答の根拠（AI に渡した根拠・引用）の根拠には
入れない。渡したことは Run の step とログに残る。合成の設定・根拠で、モデルは台本（ScriptedModel）。
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, FakeProductMcp, fake_product_mcp
from pytest import LogCaptureFixture, MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.answer_evidence import ANSWER_EVIDENCE_KIND
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import AgentProfile, RunCreateRequest, RunStatus, runtime_repository
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.support_task import profile_knowledge_summary

SKILL_ID = "test2701-skill"
AGENT_ID = "test2701-agent"
USER_UUID = "27012701-2222-3333-4444-555555555555"
GOAL = "機器 A の合成手続きアルファはいつまでに行うか"
# 原文（根拠の本文）に無い、検索・回答プロファイルで承認した用語の説明。
APPROVED_POLICY = "合成手続きアルファの期限は、受付の翌営業日から数える手続き。"
ANSWER = "機器 A の合成手続きアルファは受付から 5 営業日以内に行います。"

KNOWLEDGE = {
    "search_answer_profile_id": "bv-1",
    "revision": "0123456789abcdef",
    "matched_on": "original_question",
    "terms": [
        {
            "term": "合成手続きアルファ",
            "aliases": ["手続き α"],
            "description": APPROVED_POLICY,
            "source": "",
            "status": "approved",
            "updated_at": "",
        }
    ],
    "terms_omitted": 1,
    "load_failed": False,
}


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="根拠の取得の Skill",
            instructions="rag_retrieve_evidence で根拠を集め、根拠から答える。",
            mcp_requirements=[
                SkillMcpRequirement(
                    server_id="rag",
                    tool_names=["rag_retrieve_evidence"],
                    evidence_planning="caller",
                )
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=AGENT_ID, name="根拠の取得の業務 Agent", skill_ids=[SKILL_ID])
    )
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _tool_output(model: ScriptedModel, call_id: str) -> dict[str, Any]:
    raw = next(
        item["output"]
        for item in model.calls[1].input
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item
    )
    output: dict[str, Any] = json.loads(raw if isinstance(raw, str) else str(raw))
    return output


def test_profile_knowledge_reaches_the_model_apart_from_the_evidence(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, caplog: LogCaptureFixture
) -> None:
    retrieved = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"])
    retrieved["profile_knowledge"] = deepcopy(KNOWLEDGE)
    mcp.outputs["rag_retrieve_evidence"] = retrieved
    model = _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "期限"}, call_id="c1")],
        [assistant_message(ANSWER)],
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=AGENT_ID, draft=True),
        created_by_user_uuid=USER_UUID,
    )
    with caplog.at_level(logging.INFO, logger=builtin_runtime.__name__):
        anyio.run(builtin_runtime.execute_run, run.id)

    finished = runtime_repository.get_run(run.id)
    assert finished.status == RunStatus.COMPLETED, finished.events[-1].message
    [call] = mcp.calls_of("rag_retrieve_evidence")
    assert call["arguments"]["planning"] == "caller"
    # 照合に使う利用者の元の質問は実行環境が付ける。RAG に回答を作らせない。
    assert call["arguments"]["original_question"] == GOAL
    assert mcp.calls_of("rag_search") == []

    # モデルへの入力: 用語は根拠と別の項目のまま届き、根拠の一覧には入らない。
    output = _tool_output(model, "c1")
    assert output["profile_knowledge"]["terms"][0]["description"] == APPROVED_POLICY
    assert output["profile_knowledge"]["terms_omitted"] == 1
    assert APPROVED_POLICY not in json.dumps(output["evidence"], ensure_ascii=False)

    # 回答の根拠（AI に渡した根拠・引用）は資料の根拠だけ。
    [artifact] = [item for item in finished.artifacts if item.kind == ANSWER_EVIDENCE_KIND]
    assert APPROVED_POLICY not in json.dumps(artifact.content, ensure_ascii=False)
    assert {item["chunk_id"] for item in artifact.content["items"]} == {
        item["chunk_id"] for item in retrieved["evidence"]
    }

    # 渡した内容は Run の step（全文）とログ（数と版だけ）で追える。
    [step] = [
        step
        for step in finished.steps
        if step.tool_result is not None and step.tool_result.name.endswith("rag_retrieve_evidence")
    ]
    assert step.tool_result is not None and step.tool_result.output is not None
    assert step.tool_result.output["profile_knowledge"] == KNOWLEDGE
    [record] = [
        r for r in caplog.records if r.message == "builtin_runtime_profile_knowledge_passed"
    ]
    assert (record.__dict__["terms"], record.__dict__["revision"]) == (1, "0123456789abcdef")
    assert APPROVED_POLICY not in str(record.__dict__)


def test_profile_knowledge_summary_counts_without_the_text() -> None:
    assert profile_knowledge_summary({"evidence": []}) is None
    assert profile_knowledge_summary({"profile_knowledge": None}) is None
    summary = profile_knowledge_summary({"profile_knowledge": KNOWLEDGE})
    assert summary == {
        "search_answer_profile_id": "bv-1",
        "revision": "0123456789abcdef",
        "matched_on": "original_question",
        "terms": 1,
        "terms_omitted": 1,
        "load_failed": False,
    }
