"""Run の RAG の根拠の原文のプレビュー（`GET /api/runs/{run_id}/source-preview`。#2389）。

RAG の MCP は契約どおりの fake（`mcp_support`）。Agent は画面を見ている利用者を `sub`、
`purpose=source_preview` をサービストークンに入れて `rag_read_source`（`include_preview`）を呼び、
返ったプレビューの URL（公開の起点があれば付け替え）を返す。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from mcp_support import FakeProductMcp, McpToolError, fake_product_mcp
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
    login_configured_admin,
)

from app.features.agent.runtime import Artifact, RunState, RunStatus, runtime_repository
from app.features.agent.tools import mcp_function_name
from app.security.domain import CONFIGURED_SYSTEM_ADMIN_USER_UUID
from app.security.permissions import MENU_RUNS, RUNS_VIEW
from app.security.service import set_security_service
from app.settings import get_settings

RUN_ID = "run-preview-2389"
CREATOR = "aaaaaaaa-0000-0000-0000-000000002389"
TOKEN_PATH = "/api/source-previews/p1.payload.signature"
RAG_ORIGIN = "http://10.0.0.5"
HIGHLIGHT = {"page_start": 2, "page_width": 600, "page_height": 800, "bbox": "[60, 80, 300, 200]"}


def _evidence(chunk_id: str) -> dict[str, Any]:
    return {
        "evidence_id": chunk_id,
        "document_id": "doc-1",
        "chunk_id": chunk_id,
        "file_name": "文書 A.pdf",
        "evidence_type": "text",
        "image_ref": None,
        "locator": {"section_path": [], "page_start": 2, "page_end": 2},
        "excerpt": "合成の本文",
        "used_in_answer": True,
    }


def _run() -> RunState:
    return RunState(
        id=RUN_ID,
        goal="合成の質問",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.COMPLETED,
        created_by_user_uuid=CREATOR,
        artifacts=[
            Artifact(
                name=f"{mcp_function_name('rag', 'rag_search')}:step-1",
                kind="rag_evidence",
                content={"evidence": [_evidence("c-1")]},
            )
        ],
    )


def _preview(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "pdf",
        "file_name": "文書 A.pdf",
        "pages": [
            {"page_number": 1, "width": 600.0, "height": 800.0},
            {"page_number": 2, "width": 600.0, "height": 800.0},
        ],
        "page_url_template": f"{RAG_ORIGIN}{TOKEN_PATH}/pages/{{page}}",
        "page_path_template": f"{TOKEN_PATH}/pages/{{page}}",
        "content_url": f"{RAG_ORIGIN}{TOKEN_PATH}/content",
        "content_path": f"{TOKEN_PATH}/content",
        "highlight_metadata": HIGHLIGHT,
        "expires_at": "2026-10-18T00:05:00+00:00",
        "expires_in_seconds": 300,
        **overrides,
    }


def _read_source_output(argument: Any) -> dict[str, Any]:
    assert argument.include_preview is True
    return {
        "schema_version": 4,
        "evidence_id": argument.chunk_id,
        "document_id": argument.document_id,
        "chunk_id": argument.chunk_id,
        "preview": _preview(),
        "locator": {"section_path": []},
        "text": "合",
        "offset": 0,
        "text_length": 5,
        "truncated": True,
    }


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


@pytest.fixture
def rag(monkeypatch: MonkeyPatch) -> FakeProductMcp:
    return fake_product_mcp(monkeypatch, outputs={"rag_read_source": _read_source_output})


@pytest.fixture
def seeded_run() -> Iterator[RunState]:
    repository: Any = runtime_repository
    run = _run()
    with repository._lock:  # noqa: SLF001 - テスト用に Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    try:
        yield run
    finally:
        with repository._lock:  # noqa: SLF001
            repository._runs.pop(run.id, None)  # noqa: SLF001


def _owner_viewer(auth: ProductionAuth, run: RunState) -> None:
    user = auth.user_with_permissions(
        "preview-viewer", [MENU_RUNS, RUNS_VIEW], agent_ids=["default"]
    )
    run.created_by_user_uuid = user.user_uuid


def _url(chunk_id: str = "c-1", document_id: str = "doc-1") -> str:
    return f"/api/runs/{RUN_ID}/source-preview?document_id={document_id}&chunk_id={chunk_id}"


def test_viewer_gets_preview_issued_as_viewer(
    auth: ProductionAuth, rag: FakeProductMcp, seeded_run: RunState, monkeypatch: MonkeyPatch
) -> None:
    del seeded_run
    # Run を実行していない利用者は、同じ業務 Agent の範囲でも開けない（404。#1889）。
    auth.user_with_permissions("preview-viewer", [MENU_RUNS, RUNS_VIEW], agent_ids=["default"])
    assert client.get(_url(), headers=login("preview-viewer")).status_code == 404
    headers = login_configured_admin()

    response = client.get(_url(), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kind"] == "pdf"
    assert [page["page_number"] for page in data["pages"]] == [1, 2]
    assert data["page_url_template"] == f"{RAG_ORIGIN}{TOKEN_PATH}/pages/{{page}}"
    assert data["content_url"] == f"{RAG_ORIGIN}{TOKEN_PATH}/content"
    assert data["highlight_metadata"] == HIGHLIGHT
    assert data["expires_in_seconds"] == 300
    assert "page_path_template" not in data
    assert response.headers["cache-control"] == "private, no-store"

    [call] = rag.calls_of("rag_read_source")
    assert call["arguments"] == {
        "document_id": "doc-1",
        "chunk_id": "c-1",
        "include_preview": True,
        "max_chars": 1,
    }
    # 画面を見ている利用者として呼ぶ（Run の利用者の権限は借りない）。画面の操作の印を付ける。
    claims = call["claims"]
    assert (claims["sub"], claims["aud"], claims["purpose"]) == (
        CONFIGURED_SYSTEM_ADMIN_USER_UUID,
        "rag",
        "source_preview",
    )
    assert claims["run_id"] == RUN_ID

    # 公開の起点（同じ origin の /rag）があれば、RAG の path をそこに付ける。
    monkeypatch.setattr(get_settings(), "agent_external_rag_public_url", "/rag")
    same_origin = client.get(_url(), headers=headers).json()["data"]
    assert same_origin["page_url_template"] == f"/rag{TOKEN_PATH}/pages/{{page}}"
    assert same_origin["content_url"] == f"/rag{TOKEN_PATH}/content"


def test_only_evidence_in_the_run_and_own_runs_can_be_previewed(
    auth: ProductionAuth, rag: FakeProductMcp, seeded_run: RunState
) -> None:
    _owner_viewer(auth, seeded_run)
    headers = login("preview-viewer")
    assert client.get(_url(), headers=headers).status_code == 200
    # Run に無い根拠は RAG を呼ばずに 404。
    before = len(rag.calls_of("rag_read_source"))
    assert client.get(_url("other"), headers=headers).status_code == 404
    assert client.get(_url("c-1", "doc-2"), headers=headers).status_code == 404
    assert len(rag.calls_of("rag_read_source")) == before
    # ほかの利用者の Run は 404、実行を見る権限の無い利用者は経路で 403、未ログインは 401。
    auth.user_with_permissions("sales-viewer", [MENU_RUNS, RUNS_VIEW], agent_ids=["sales"])
    assert client.get(_url(), headers=login("sales-viewer")).status_code == 404
    auth.user_with_permissions("no-runs", [], agent_ids=["default"])
    assert client.get(_url(), headers=login("no-runs")).status_code == 403
    assert client.get(_url()).status_code == 401


@pytest.mark.parametrize(
    ("error_code", "status"),
    [
        ("source_stale", 409),
        ("source_not_found", 404),
        ("preview_file_missing", 404),
        ("preview_unavailable", 503),
        ("MCP_TOOL_FORBIDDEN", 403),
        ("internal_error", 502),
    ],
    ids=["stale", "not-found", "file-missing", "unavailable", "forbidden", "other"],
)
def test_rag_errors_are_returned_as_screen_messages(
    auth: ProductionAuth,
    rag: FakeProductMcp,
    seeded_run: RunState,
    error_code: str,
    status: int,
) -> None:
    rag.outputs["rag_read_source"] = McpToolError(error_code, "RAG のエラー")
    _owner_viewer(auth, seeded_run)
    response = client.get(_url(), headers=login("preview-viewer"))
    assert response.status_code == status
    assert response.json()["error_messages"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_url": "javascript:alert(1)", "content_path": "/x"},
        {"content_url": f"{RAG_ORIGIN}/api/figures/f1.a.b", "content_path": "/api/figures/f1.a.b"},
        {"page_url_template": f"{RAG_ORIGIN}/api/other/{{page}}", "page_path_template": "/x"},
    ],
    ids=["script-url", "other-route", "other-page-route"],
)
def test_unexpected_url_from_rag_is_not_returned(
    auth: ProductionAuth, rag: FakeProductMcp, seeded_run: RunState, overrides: dict[str, Any]
) -> None:
    def bad(argument: Any) -> dict[str, Any]:
        output = _read_source_output(argument)
        output["preview"] = _preview(**overrides)
        return output

    rag.outputs["rag_read_source"] = bad
    _owner_viewer(auth, seeded_run)
    assert client.get(_url(), headers=login("preview-viewer")).status_code == 502


def test_text_preview_has_no_page_url(
    auth: ProductionAuth, rag: FakeProductMcp, seeded_run: RunState
) -> None:
    def text(argument: Any) -> dict[str, Any]:
        output = _read_source_output(argument)
        output["preview"] = _preview(
            kind="text", pages=[], page_url_template=None, page_path_template=None
        )
        return output

    rag.outputs["rag_read_source"] = text
    _owner_viewer(auth, seeded_run)
    data = client.get(_url(), headers=login("preview-viewer")).json()["data"]
    assert (data["kind"], data["pages"], data["page_url_template"]) == ("text", [], None)
