"""根拠を読む入口の認可の行列（入口 × 主体 × 範囲。OWASP API1 / BOLA。#3252）。

入口ごとの詳しいテストは ``test_run_figure_url``・``test_run_source_preview``・
``test_admin_operates_others``・``test_agent_data_scope`` にあり、ここでは「どの入口を、どの主体と
範囲で呼ぶと、何が返るか」を 1 か所の表で固定する。

- 保存した根拠の再表示（Run の詳細・会話・イベント・成果物・MCP の ``agent_get_run``）: 画面を見て
  いる利用者として RAG に今見られる文書かを確かめ（``rag_readable_documents``。``sub`` は見る人）、
  見られない根拠は抜粋・文書名を出さず件数だけ（判断 A）。確かめられないときはすべて伏せる。
- 図・原文のプレビューの URL（``/runs/{run_id}/figure-url``・``/source-preview``）: Run を見られる
  利用者だけ、Run の根拠にある根拠だけ、見る人として RAG に作らせる。
- 会話の履歴・続きの質問: thread の ID を知っているだけの他の利用者は 404、system_admin は閲覧だけ
  （続きの質問は 403。#3244）、別の業務 Agent の会話の ID を混ぜた続きは 404、モデルに渡す履歴に別の
  会話は入らない。

RAG の MCP は契約どおりの fake（``mcp_support``）で、組み込み Runtime・OCI は呼ばない。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from mcp_support import FakeProductMcp, McpToolError, fake_product_mcp
from pr_system_settings.auth.api_keys import api_key_rate_limiter
from pr_system_settings.auth.visibility import OTHERS_CONTENT_FORBIDDEN_MESSAGE
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import router
from app.features.agent.runtime import (
    AgentProfile,
    Artifact,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunState,
    RunStatus,
    RunStep,
    StepStatus,
    runtime_repository,
)
from app.features.agent.tools import ToolCall, ToolResult, mcp_function_name
from app.security.permissions import APPROVALS_DECIDE, RUNS_OPERATE, RUNS_VIEW
from app.security.service import set_security_service

USER_PERMISSIONS = [RUNS_VIEW, RUNS_OPERATE, APPROVALS_DECIDE]
READABLE = "doc-a"
HIDDEN = "doc-b"
HIDDEN_TEXT = "doc-b の合成の抜粋"


@dataclass
class People:
    agent_id: str
    other_agent_id: str
    owner: str
    other: str
    admin: str
    logins: dict[str, str]


def _complete(run: RunState) -> None:
    """組み込み Runtime の代わりに、質問を受けて回答で完了にする。"""
    if runtime_repository.begin_builtin_run(run.id) is not None:
        runtime_repository.complete_builtin_run(run.id, f"回答: {run.goal}")


@pytest.fixture
def people(monkeypatch: MonkeyPatch) -> Iterator[People]:
    auth: ProductionAuth = enable_production_auth(monkeypatch)
    suffix = uuid4().hex[:8]
    agent_id = f"agent3252-{suffix}"
    other_agent_id = f"agent3252-other-{suffix}"
    for item_id in (agent_id, other_agent_id):
        runtime_repository.create_agent(
            AgentProfile(id=item_id, name=f"Agent {item_id[-4:]}", instructions="答える。")
        )
    owner = auth.user_with_permissions(
        f"owner-{suffix}", USER_PERMISSIONS, agent_ids=[agent_id, other_agent_id]
    )
    other = auth.user_with_permissions(f"other-{suffix}", USER_PERMISSIONS, agent_ids=[agent_id])
    admin = auth.create_user(f"admin-{suffix}", system_admin=True)
    monkeypatch.setattr(router, "_schedule_builtin_run", _complete)
    api_key_rate_limiter.reset()
    try:
        yield People(
            agent_id=agent_id,
            other_agent_id=other_agent_id,
            owner=owner.user_uuid,
            other=other.user_uuid,
            admin=admin.user_uuid,
            logins={
                "owner": owner.login_user_id,
                "other": other.login_user_id,
                "system_admin": admin.login_user_id,
            },
        )
    finally:
        set_security_service(None)
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id in {agent_id, other_agent_id}
            ]:
                repository._runs.pop(run_id)
        for item_id in (agent_id, other_agent_id):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(item_id)


def _evidence(document_id: str) -> dict[str, Any]:
    return {
        "evidence_id": f"{document_id}:c1",
        "document_id": document_id,
        "chunk_id": f"{document_id}:c1",
        "file_name": f"{document_id}.pdf",
        "evidence_type": "text",
        "image_ref": {"page": 1},
        "locator": {"section_path": [], "page_start": 1, "page_end": 1},
        "excerpt": f"{document_id} の合成の抜粋",
        "used_in_answer": True,
    }


def _step(run_id: str, tool: str, output: dict[str, Any]) -> RunStep:
    name = mcp_function_name("rag", tool)
    return RunStep(
        run_id=run_id,
        status=StepStatus.COMPLETED,
        tool_call=ToolCall(name=name, arguments={}),
        tool_result=ToolResult(name=name, success=True, output=output),
    )


def _seed_evidence_run(people: People, *, agent_id: str | None = None) -> RunState:
    """RAG の根拠（見られる文書 a と、後で見られなくなる文書 b）を保存した、持ち主の Run。"""
    run_id = f"run3252-{uuid4().hex[:8]}"
    search_output = {"evidence": [_evidence(READABLE), _evidence(HIDDEN)]}
    read_output = {"document_id": HIDDEN, "chunk_id": f"{HIDDEN}:c1", "text": HIDDEN_TEXT}
    run = RunState(
        id=run_id,
        goal="合成の質問",
        agent_id=agent_id or people.agent_id,
        runtime_id="builtin",
        status=RunStatus.COMPLETED,
        created_by_user_uuid=people.owner,
        thread_id=f"thread-{run_id}",
        steps=[
            _step(run_id, "rag_search", search_output),
            _step(run_id, "rag_read_source", read_output),
        ],
        events=[
            RunEvent(
                run_id=run_id,
                type=RunEventType.TOOL_COMPLETED,
                message="ツールが完了しました。",
                payload={"tool_name": "rag__rag_read_source", "output": read_output},
            )
        ],
        artifacts=[
            Artifact(name="回答", kind="answer", content={"text": "合成の回答"}),
            Artifact(
                name=f"{mcp_function_name('rag', 'rag_search')}:step-1",
                kind="rag_evidence",
                content=search_output,
            ),
            Artifact(
                name="回答の根拠",
                kind="answer_evidence",
                content={
                    "items": [
                        {**_evidence(READABLE), "cited": True},
                        {**_evidence(HIDDEN), "cited": True},
                    ],
                    "passed": 2,
                    "cited": 2,
                    "markers": [],
                    "passages": [
                        {"text": "合成の回答", "chunk_ids": [f"{READABLE}:c1", f"{HIDDEN}:c1"]}
                    ],
                    "binding": "text",
                    "references": [
                        {"quote": f"{READABLE} の合成の抜粋", "chunk_ids": [f"{READABLE}:c1"]},
                        {"quote": HIDDEN_TEXT, "chunk_ids": [f"{HIDDEN}:c1"]},
                    ],
                    "citation_lines": [],
                },
            ),
        ],
    )
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - テスト用に Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    return run


def _read_source_output(argument: Any) -> dict[str, Any]:
    url = {"url": "http://rag.test/api/figures/f1.a.b", "path": "/api/figures/f1.a.b"}
    preview = {
        "kind": "text",
        "file_name": "doc-a.pdf",
        "pages": [],
        "content_url": "http://rag.test/api/source-previews/p1.a.b/content",
        "content_path": "/api/source-previews/p1.a.b/content",
        "highlight_metadata": {},
        "expires_at": "2026-10-25T00:05:00+00:00",
        "expires_in_seconds": 300,
    }
    return {
        "schema_version": 4,
        "evidence_id": argument.chunk_id,
        "document_id": argument.document_id,
        "chunk_id": argument.chunk_id,
        "image_url": {**url, "expires_at": "2026-10-25T00:05:00+00:00", "expires_in_seconds": 300},
        "preview": preview,
        "locator": {"section_path": []},
        "text": "合",
        "offset": 0,
        "text_length": 1,
        "truncated": True,
    }


@pytest.fixture
def rag(monkeypatch: MonkeyPatch) -> FakeProductMcp:
    return fake_product_mcp(monkeypatch, outputs={"rag_read_source": _read_source_output})


def _headers(people: People, principal: str) -> dict[str, str]:
    return login(people.logins[principal])


# ---------------------------------------------------------------------------
# 保存した根拠の再表示
# ---------------------------------------------------------------------------

# 主体 → (status, RAG が見られないと答える文書, 見える文書, 権限の無い根拠の数)。
SAVED_MATRIX = [
    ("owner", set(), 200, [READABLE, HIDDEN], 0),
    # Run の後に、持ち主の範囲から文書 b を外した（RAG が見られないと答える）。
    ("owner", {HIDDEN}, 200, [READABLE], 1),
    ("other", set(), 404, None, None),
    # system_admin は閲覧できる。見るのは system_admin の今の権限（Run の利用者の権限ではない）。
    ("system_admin", set(), 200, [READABLE, HIDDEN], 0),
    ("system_admin", {HIDDEN}, 200, [READABLE], 1),
]
SAVED_IDS = ["owner", "owner-kb-removed", "other", "system_admin", "system_admin-no-scope"]


def _answer_evidence(run: dict[str, Any]) -> dict[str, Any]:
    [artifact] = [item for item in run["artifacts"] if item["kind"] == "answer_evidence"]
    content: dict[str, Any] = artifact["content"]
    return content


@pytest.mark.parametrize(
    ("principal", "unreadable", "status", "documents", "withheld"), SAVED_MATRIX, ids=SAVED_IDS
)
def test_run_detail_and_thread_recheck_saved_evidence(
    people: People,
    rag: FakeProductMcp,
    principal: str,
    unreadable: set[str],
    status: int,
    documents: list[str] | None,
    withheld: int | None,
) -> None:
    run = _seed_evidence_run(people)
    rag.unreadable_documents = unreadable
    headers = _headers(people, principal)
    detail = client.get(f"/api/runs/{run.id}", headers=headers)
    thread = client.get(f"/api/threads/{run.thread_id}", headers=headers)
    assert (detail.status_code, thread.status_code) == (status, status), detail.text
    if documents is None:
        # 見えない Run の根拠は、RAG に問い合わせもしない。
        assert rag.calls_of("rag_readable_documents") == []
        return
    viewer = people.admin if principal == "system_admin" else people.owner
    calls = rag.calls_of("rag_readable_documents")
    assert calls and {call["claims"]["sub"] for call in calls} == {viewer}
    assert {call["claims"]["purpose"] for call in calls} == {"evidence_access"}
    for data in (detail.json()["data"], thread.json()["data"]["runs"][0]):
        assert data["withheld_evidence_count"] == withheld
        content = _answer_evidence(data)
        assert [item["document_id"] for item in content["items"]] == documents
        if withheld:
            text = json.dumps(data, ensure_ascii=False)
            # 抜粋・文書名・参考情報の原文を、ツールの結果・イベント・成果物のどこにも残さない。
            assert HIDDEN_TEXT not in text and f"{HIDDEN}.pdf" not in text
            assert content["passages"][0]["chunk_ids"] == [f"{READABLE}:c1"]
            assert [item["quote"] for item in content["references"]] == [f"{READABLE} の合成の抜粋"]
            assert data["steps"][1]["tool_result"]["output"] == {"evidence_withheld": True}
        # 回答の本文は保存した文のまま（既知の限界）。
        answer = [item for item in data["artifacts"] if item["kind"] == "answer"]
        assert answer[0]["content"]["text"] == "合成の回答"
    # 保存した Run は変えない（応答の写しだけを変える）。
    stored = runtime_repository.get_run(run.id)
    assert len(stored.artifacts[2].content["items"]) == 2


@pytest.mark.parametrize("unreadable", [set(), {HIDDEN}], ids=["readable", "kb-removed"])
def test_events_and_artifacts_recheck_saved_evidence(
    people: People, rag: FakeProductMcp, unreadable: set[str]
) -> None:
    run = _seed_evidence_run(people)
    rag.unreadable_documents = unreadable
    headers = _headers(people, "owner")
    events = client.get(f"/api/runs/{run.id}/events", headers=headers)
    artifacts = client.get(f"/api/runs/{run.id}/artifacts", headers=headers)
    artifact_id = run.artifacts[2].id
    single = client.get(f"/api/runs/{run.id}/artifacts/{artifact_id}", headers=headers)
    for response in (events, artifacts, single):
        assert response.status_code == 200, response.text
        assert (HIDDEN_TEXT in response.text) is not bool(unreadable)
    if unreadable:
        assert single.json()["data"]["content"]["passed"] == 1
    # ほかの利用者は読めない（404）。
    other = _headers(people, "other")
    for path in ("events", "artifacts", f"artifacts/{artifact_id}"):
        assert client.get(f"/api/runs/{run.id}/{path}", headers=other).status_code == 404


def test_saved_evidence_is_withheld_when_access_cannot_be_checked(
    people: People, rag: FakeProductMcp
) -> None:
    """RAG で確かめられないとき（失敗）は、RAG の根拠をすべて伏せる（fail closed）。"""
    run = _seed_evidence_run(people)
    rag.servers["rag"].tools["rag_readable_documents"] = (
        rag.servers["rag"]
        .tools["rag_readable_documents"]
        .__class__(
            name="rag_readable_documents",
            description="rag_readable_documents",
            input_model=rag.servers["rag"].tools["rag_readable_documents"].input_model,
            handler=_failing_check,
            listed=False,
        )
    )
    data = client.get(f"/api/runs/{run.id}", headers=_headers(people, "owner")).json()["data"]
    assert data["withheld_evidence_count"] == 2
    assert _answer_evidence(data)["items"] == []
    assert f"{READABLE} の合成の抜粋" not in json.dumps(data, ensure_ascii=False)


async def _failing_check(argument: Any) -> dict[str, Any]:
    del argument
    raise McpToolError("unavailable", "確かめられません。", status=503)


def test_mcp_agent_get_run_rechecks_sources(
    people: People, rag: FakeProductMcp, monkeypatch: MonkeyPatch
) -> None:
    """外部の MCP クライアントが後から読む Run の根拠（sources）も、キーの利用者の今の権限で。"""
    from app.features.agent import mcp_server

    monkeypatch.setattr(mcp_server, "wait_poll_seconds", 0.01)
    run = _seed_evidence_run(people)
    rag.unreadable_documents = {HIDDEN}
    session = _headers(people, "owner")
    created = client.post(
        "/api/account/api-keys",
        json={"name": "外部のクライアント", "products": ["agent"]},
        headers=session,
    )
    assert created.status_code == 200, created.text
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "agent_get_run", "arguments": {"run_id": run.id, "wait_seconds": 0}},
        },
        headers={"authorization": f"Bearer {created.json()['data']['token']}"},
    )
    assert response.status_code == 200, response.text
    output = response.json()["result"]["structuredContent"]
    assert [item["title"] for item in output["sources"]] == [f"{READABLE}.pdf"]
    assert HIDDEN_TEXT not in response.text
    [call] = rag.calls_of("rag_readable_documents")
    assert call["claims"]["sub"] == people.owner


# ---------------------------------------------------------------------------
# 図・原文のプレビューの URL
# ---------------------------------------------------------------------------

# 主体・対象 → (status, RAG に作らせたか)。
URL_MATRIX = [
    ("owner", READABLE, None, 200),
    ("other", READABLE, None, 404),
    ("system_admin", READABLE, None, 200),
    # Run の根拠に無い根拠（path・query の書き換え）は RAG を呼ばずに 404。
    ("owner", "doc-not-in-run", None, 404),
    # RAG が今の権限で見られないと答えた（範囲の外・権限を外した）。
    ("owner", READABLE, "source_not_found", 404),
    ("owner", READABLE, "MCP_TOOL_FORBIDDEN", 403),
]
URL_IDS = ["owner", "other", "system_admin", "not-in-run", "rag-not-found", "rag-forbidden"]


@pytest.mark.parametrize("entry", ["figure-url", "source-preview"])
@pytest.mark.parametrize(
    ("principal", "document_id", "rag_error", "status"), URL_MATRIX, ids=URL_IDS
)
def test_figure_and_source_preview_urls_by_principal(
    people: People,
    rag: FakeProductMcp,
    entry: str,
    principal: str,
    document_id: str,
    rag_error: str | None,
    status: int,
) -> None:
    run = _seed_evidence_run(people)
    if rag_error is not None:
        rag.outputs["rag_read_source"] = McpToolError(rag_error, "見られません。", status=404)
    url = f"/api/runs/{run.id}/{entry}?document_id={document_id}&chunk_id={document_id}:c1"
    response = client.get(url, headers=_headers(people, principal))
    assert response.status_code == status, response.text
    calls = rag.calls_of("rag_read_source")
    if status == 200 or rag_error is not None:
        # 画面を見ている利用者として作らせる（Run の利用者の権限は借りない）。
        viewer = people.admin if principal == "system_admin" else people.owner
        assert [call["claims"]["sub"] for call in calls] == [viewer]
    else:
        assert calls == []
    if status == 200:
        assert response.headers["cache-control"] == "private, no-store"


# ---------------------------------------------------------------------------
# 会話の履歴・続きの質問（#3257 の分）
# ---------------------------------------------------------------------------


def _ask(headers: dict[str, str], agent_id: str, goal: str, thread_id: str | None = None) -> Any:
    body: dict[str, Any] = {"goal": goal, "agent_id": agent_id}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post("/api/runs", json=body, headers=headers)


@pytest.mark.parametrize(
    ("principal", "read", "follow_up"),
    [("owner", 200, 200), ("other", 404, 404), ("system_admin", 200, 403)],
    ids=["owner", "other", "system_admin"],
)
def test_thread_history_and_follow_up_by_principal(
    people: People, principal: str, read: int, follow_up: int
) -> None:
    first = _ask(_headers(people, "owner"), people.agent_id, "最初の質問")
    assert first.status_code == 200, first.text
    thread_id = first.json()["data"]["thread_id"]
    headers = _headers(people, principal)
    assert client.get(f"/api/threads/{thread_id}", headers=headers).status_code == read
    continued = _ask(headers, people.agent_id, "続きの質問", thread_id)
    assert continued.status_code == follow_up, continued.text
    if follow_up == 403:
        assert OTHERS_CONTENT_FORBIDDEN_MESSAGE in continued.text
    runs = runtime_repository.get_thread(thread_id, user_uuid=people.owner).runs
    assert len(runs) == (2 if follow_up == 200 else 1)


def test_follow_up_with_a_thread_of_another_agent_is_not_found(people: People) -> None:
    """別の業務 Agent の会話の ID を混ぜた続きの質問は 404（その会話に足さない）。"""
    owner = _headers(people, "owner")
    other_thread = _ask(owner, people.other_agent_id, "別の Agent の質問").json()["data"]
    mixed = _ask(owner, people.agent_id, "続き", other_thread["thread_id"])
    assert mixed.status_code == 404, mixed.text
    runs = runtime_repository.get_thread(other_thread["thread_id"], user_uuid=people.owner).runs
    assert [run.id for run in runs] == [other_thread["id"]]


def test_history_given_to_the_model_stays_in_the_thread(people: People) -> None:
    """モデルに渡す前の往復（質問の単独化の材料）は、同じ会話の Run だけ。"""
    owner = _headers(people, "owner")
    mine = _ask(owner, people.agent_id, "自分の会話の質問").json()["data"]
    _ask(_headers(people, "other"), people.agent_id, "他の利用者の会話の質問")
    _ask(owner, people.agent_id, "自分の別の会話の質問")
    follow_up = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="続き", agent_id=people.agent_id, thread_id=mine["thread_id"]),
        created_by_user_uuid=people.owner,
    )
    history = runtime_repository.thread_history(follow_up.id)
    text = repr(history)
    assert "自分の会話の質問" in text
    assert "他の利用者の会話の質問" not in text and "自分の別の会話の質問" not in text
