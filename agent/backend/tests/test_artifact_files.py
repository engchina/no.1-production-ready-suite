"""Run の成果物のファイル（ツールが作った .xlsx / .docx / .pptx / .md）の保存とダウンロード
（#5837・#5867）。

- 形式・大きさ・ファイル名の検査と、合わないファイルを保存しないこと（zip の署名は OOXML の
  3 つだけ、Markdown は UTF-8 の文字）。
- 中身（バイト列・base64）が Run の JSON・一覧・イベント・ツールの結果・MCP の結果に載らないこと。
- MCP の Run の結果に、ファイルの一覧（名前・形式・大きさ）と受け取り方が載ること。
- ダウンロードの API は Run を見られる利用者だけ（ほかの利用者は Run の詳細と同じ 404）。
- Run の行を消すと、ファイルも消えること（Oracle は偽の表 `checkpoint_support`）。

ファイルは合成の中身（zip の署名 + 目印の文字列）。Oracle・OCI には接続しない。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from checkpoint_support import CheckpointTable, load_state
from pr_system_settings.auth.api_keys import api_key_rate_limiter
from pytest import MonkeyPatch
from security_support import client, enable_production_auth, login

from app.features.agent import artifact_file_store
from app.features.agent.artifact_file_store import (
    FILE_ARTIFACT_EXTENSIONS,
    FILE_ARTIFACT_KIND,
    FILE_NAME_MAX_CHARS,
    ArtifactFileError,
    attachment_disposition,
    checked_file,
    safe_file_name,
    save_tool_file,
)
from app.features.agent.mcp_server import FILES_DOWNLOAD_MESSAGE, run_output
from app.features.agent.runtime import (
    AgentProfile,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeRepository,
    AgentRuntimeSnapshot,
    Artifact,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.features.agent.runtime_checkpoint_store import RUN, write_entities
from app.features.agent.tools import (
    ToolCall,
    ToolDefinition,
    ToolInvocationContext,
    tool_registry,
)
from app.security.permissions import AUDIT_VIEW, RUNS_OPERATE, RUNS_VIEW
from app.security.service import set_security_service
from app.settings import get_settings

XLSX, DOCX, PPTX, MARKDOWN = FILE_ARTIFACT_EXTENSIONS
MARKER = b"synthetic-file-marker-5837"
# 合成のファイル（zip の署名で始まる。中身は目印の繰り返し）。
DATA = b"PK\x03\x04" + MARKER * 64


def _leaks(text: str) -> bool:
    """応答・記録の文字列に、ファイルの中身（そのまま・base64）が入っているか。"""
    encoded = base64.b64encode(DATA).decode("ascii")
    return MARKER.decode("ascii") in text or encoded[:64] in text


# ---- 検査（形式・大きさ・ファイル名） -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "mime_type", "expected"),
    [
        ("集計表.xlsx", XLSX, "集計表.xlsx"),
        # 拡張子は MIME から決める（付いていた対象の形式の拡張子は外す）。
        ("report.DOCX", XLSX, "report.xlsx"),
        ("slides", PPTX, "slides.pptx"),
        ("v1.2 draft", DOCX, "v1.2 draft.docx"),
        # パス区切り・OS が使えない文字は残さない。
        ("../../etc/passwd", XLSX, "_.._etc_passwd.xlsx"),
        ("C:\\temp\\a?b*.xlsx", XLSX, "C__temp_a_b_.xlsx"),
        # 制御文字・書字方向の制御・改行は除く。
        ("a\x00b\u202excod.pptx", DOCX, "abxcod.docx"),
        ("line1\r\nline2\t.xlsx", XLSX, "line1 line2.xlsx"),
        ("", XLSX, "file.xlsx"),
        (" . ", PPTX, "file.pptx"),
        ("手順.md", MARKDOWN, "手順.md"),
        ("notes.MD", MARKDOWN, "notes.md"),
        ("report.xlsx", MARKDOWN, "report.md"),
    ],
    ids=[
        "japanese",
        "extension-from-mime",
        "no-extension",
        "inner-dot",
        "path-traversal",
        "windows-path",
        "control-and-bidi",
        "newline",
        "empty",
        "dots-only",
        "markdown",
        "markdown-upper",
        "markdown-from-office",
    ],
)
def test_safe_file_name(name: str, mime_type: str, expected: str) -> None:
    assert safe_file_name(name, mime_type) == expected


def test_safe_file_name_limits_the_length() -> None:
    name = safe_file_name("あ" * 500, XLSX)

    assert len(name) == FILE_NAME_MAX_CHARS
    assert name.endswith(".xlsx")


def test_only_the_office_formats_and_markdown_are_allowed() -> None:
    assert sorted(FILE_ARTIFACT_EXTENSIONS.values()) == [".docx", ".md", ".pptx", ".xlsx"]
    assert MARKDOWN == "text/markdown; charset=utf-8"
    for mime_type in ("application/pdf", "text/html", "application/zip", "text/markdown", ""):
        with pytest.raises(ArtifactFileError) as error:
            checked_file("a", mime_type, DATA, max_bytes=1_000_000)
        assert error.value.code == "artifact.file_type_not_allowed"


def test_too_large_and_invalid_content_are_rejected() -> None:
    with pytest.raises(ArtifactFileError) as too_large:
        checked_file("a", XLSX, DATA, max_bytes=len(DATA) - 1)
    assert too_large.value.code == "artifact.file_too_large"
    assert checked_file("a", XLSX, DATA, max_bytes=len(DATA)).sha256 == (
        hashlib.sha256(DATA).hexdigest()
    )
    for data in (b"", b"<html>synthetic</html>"):
        with pytest.raises(ArtifactFileError) as invalid:
            checked_file("a", XLSX, data, max_bytes=1_000_000)
        assert invalid.value.code == "artifact.file_invalid"


def test_markdown_is_checked_as_utf8_text_not_as_zip() -> None:
    """Markdown は zip の署名を見ず、UTF-8 の文字か（と大きさ）だけを確かめる（#5867）。"""
    text = "# 合成の題\n\n- 項目\n".encode()
    saved = checked_file("合成", MARKDOWN, text, max_bytes=len(text))
    assert (saved.file_name, saved.data) == ("合成.md", text)
    # zip の署名で始まる中身も、UTF-8 の文字なら Markdown として保存できる。
    assert checked_file("a", MARKDOWN, b"PK\x03\x04 text", max_bytes=1_000).file_name == "a.md"
    with pytest.raises(ArtifactFileError) as too_large:
        checked_file("a", MARKDOWN, text, max_bytes=len(text) - 1)
    assert too_large.value.code == "artifact.file_too_large"
    for data in (b"\xff\xfe", "合成".encode("shift_jis"), b"\xed\xa0\x80"):
        with pytest.raises(ArtifactFileError) as invalid:
            checked_file("a", MARKDOWN, data, max_bytes=1_000)
        assert invalid.value.code == "artifact.file_invalid"


def test_attachment_disposition_encodes_non_ascii_names() -> None:
    assert attachment_disposition("report.xlsx") == (
        "attachment; filename=\"report.xlsx\"; filename*=UTF-8''report.xlsx"
    )
    assert attachment_disposition("集計 表.xlsx") == (
        f"attachment; filename=\"download.xlsx\"; filename*=UTF-8''{quote('集計 表.xlsx', safe='')}"
    )


# ---- 保存と読み出し（memory） ---------------------------------------------------------------


def _running_run(repository: Any, *, agent_id: str = "default", user: str | None = None) -> str:
    run = RunState(
        id=f"run5837-{uuid4().hex[:8]}",
        goal="合成の質問",
        agent_id=agent_id,
        runtime_id="builtin",
        status=RunStatus.RUNNING,
        created_by_user_uuid=user,
        thread_owner_user_uuid=user,
        thread_id=f"thread5837-{uuid4().hex[:8]}",
        thread_seq=1,
    )
    with repository._lock:
        repository._runs[run.id] = run
    return run.id


def test_file_artifact_keeps_only_metadata_in_the_run() -> None:
    repository = AgentRuntimeRepository()
    run_id = _running_run(repository)

    artifact = repository.save_file_artifact(
        run_id, file_name="dir/集計表", mime_type=XLSX, data=DATA
    )

    assert artifact.kind == FILE_ARTIFACT_KIND
    assert artifact.name == "dir_集計表.xlsx"
    assert artifact.content == {
        "file_name": "dir_集計表.xlsx",
        "mime_type": XLSX,
        "size_bytes": len(DATA),
        "sha256": hashlib.sha256(DATA).hexdigest(),
    }
    run = repository.get_run(run_id)
    assert [item.id for item in run.artifacts] == [artifact.id]
    created = [event for event in run.events if event.type == RunEventType.ARTIFACT_CREATED]
    assert [event.payload for event in created] == [
        {"artifact_id": artifact.id, "kind": FILE_ARTIFACT_KIND}
    ]
    # Run の JSON（snapshot・API の応答・イベント）と一覧に、中身は載らない。
    assert not _leaks(run.model_dump_json())
    assert not _leaks(repository.get_artifact(run_id, artifact.id).model_dump_json())
    assert not _leaks("".join(item.model_dump_json() for item in repository.list_artifacts(run_id)))
    with repository._lock:
        assert not _leaks(repository._export_snapshot_locked().model_dump_json())
    # 読み出すと同じ中身。
    file = repository.read_artifact_file(run_id, artifact.id)
    assert (file.data, file.file_name, file.mime_type) == (DATA, "dir_集計表.xlsx", XLSX)


def test_rejected_files_are_not_saved(monkeypatch: MonkeyPatch) -> None:
    repository = AgentRuntimeRepository()
    run_id = _running_run(repository)
    monkeypatch.setattr(get_settings(), "agent_artifact_file_max_bytes", len(DATA) - 1)

    with pytest.raises(ArtifactFileError) as too_large:
        repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)
    with pytest.raises(ArtifactFileError) as not_allowed:
        repository.save_file_artifact(run_id, file_name="a", mime_type="text/html", data=DATA[:8])

    assert too_large.value.code == "artifact.file_too_large"
    assert not_allowed.value.code == "artifact.file_type_not_allowed"
    assert repository.get_run(run_id).artifacts == []
    assert repository._artifact_files == {}


def test_default_limit_is_20_megabytes() -> None:
    assert type(get_settings()).model_fields["agent_artifact_file_max_bytes"].default == (
        20 * 1024 * 1024
    )


def test_finished_run_does_not_accept_files() -> None:
    repository = AgentRuntimeRepository()
    run_id = _running_run(repository)
    repository.cancel_run(run_id)

    with pytest.raises(ArtifactFileError) as error:
        repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)

    assert error.value.code == "artifact.file_run_closed"
    assert repository._artifact_files == {}


def test_only_file_artifacts_with_matching_content_are_read() -> None:
    repository = AgentRuntimeRepository()
    run_id = _running_run(repository)
    artifact = repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)
    with repository._lock:
        run = repository._runs[run_id]
        other = Artifact(name="表", kind="structured_table", content={"rows": []})
        run.artifacts.append(other)
        repository._mark_run_changed(run)

    for artifact_id in (other.id, "artifact_missing"):
        with pytest.raises(KeyError):
            repository.read_artifact_file(run_id, artifact_id)
    with pytest.raises(KeyError):
        repository.read_artifact_file("run_missing", artifact.id)
    # 保存した sha256 と合わない中身は返さない。
    repository._artifact_files[(run_id, artifact.id)] = DATA + b"x"
    with pytest.raises(KeyError):
        repository.read_artifact_file(run_id, artifact.id)


def test_files_of_removed_runs_are_dropped_from_memory() -> None:
    repository = AgentRuntimeRepository()
    kept, removed = _running_run(repository), _running_run(repository)
    for run_id in (kept, removed):
        repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)

    load_state(repository, AgentRuntimeSnapshot(runs=[repository.get_run(kept)]))

    assert {run_id for run_id, _ in repository._artifact_files} == {kept}


# ---- ツールの handler の入口 ----------------------------------------------------------------


def _file_tool(mime_type: str) -> tuple[ToolDefinition, Any]:
    def handler(_arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
        return {
            "file": save_tool_file(
                context.run_id, file_name="合成の表", mime_type=mime_type, data=DATA
            )
        }

    return (
        ToolDefinition(
            name="synthetic_file_tool",
            description="合成のファイルを作る。",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
        ),
        handler,
    )


@pytest.fixture
def memory_run() -> Iterator[str]:
    """既定の repository（memory）に置いた実行中の Run。"""
    run_id = _running_run(runtime_repository)
    yield run_id
    repository: Any = runtime_repository
    with repository._lock:
        repository._runs.pop(run_id, None)
        repository._artifact_files = {
            key: data for key, data in repository._artifact_files.items() if key[0] != run_id
        }


def test_tool_handler_saves_a_file_and_returns_only_the_reference(memory_run: str) -> None:
    definition, handler = _file_tool(XLSX)

    result = tool_registry.invoke(
        ToolCall(name=definition.name),
        context=ToolInvocationContext(run_id=memory_run),
        definition=definition,
        handler=handler,
    )

    assert result.success, result.error
    [artifact] = runtime_repository.get_run(memory_run).artifacts
    assert result.output == {
        "file": {
            "artifact_id": artifact.id,
            "file_name": "合成の表.xlsx",
            "mime_type": XLSX,
            "size_bytes": len(DATA),
            "sha256": hashlib.sha256(DATA).hexdigest(),
        }
    }
    assert not _leaks(result.model_dump_json())


def test_tool_handler_fails_instead_of_succeeding_without_a_file(memory_run: str) -> None:
    definition, handler = _file_tool("application/pdf")

    result = tool_registry.invoke(
        ToolCall(name=definition.name),
        context=ToolInvocationContext(run_id=memory_run),
        definition=definition,
        handler=handler,
    )
    outside = tool_registry.invoke(
        ToolCall(name=definition.name),
        context=ToolInvocationContext(),
        definition=_file_tool(XLSX)[0],
        handler=_file_tool(XLSX)[1],
    )

    assert (result.success, result.error_code) == (False, "artifact.file_type_not_allowed")
    assert (outside.success, outside.error_code) == (False, "artifact.file_run_closed")
    assert runtime_repository.get_run(memory_run).artifacts == []


# ---- ダウンロードの API ---------------------------------------------------------------------


@dataclass
class People:
    agent_id: str
    logins: dict[str, str]
    owner: str


@pytest.fixture
def people(monkeypatch: MonkeyPatch) -> Iterator[People]:
    auth = enable_production_auth(monkeypatch)
    suffix = uuid4().hex[:8]
    agent_id = f"agent5837-{suffix}"
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="合成の Agent", instructions="答える。")
    )
    permissions = [RUNS_VIEW, RUNS_OPERATE, AUDIT_VIEW]
    owner = auth.user_with_permissions(f"owner-{suffix}", permissions, agent_ids=[agent_id])
    other = auth.user_with_permissions(f"other-{suffix}", permissions, agent_ids=[agent_id])
    out_of_scope = auth.user_with_permissions(f"scope-{suffix}", permissions)
    admin = auth.create_user(f"admin-{suffix}", system_admin=True)
    api_key_rate_limiter.reset()
    try:
        yield People(
            agent_id=agent_id,
            owner=owner.user_uuid,
            logins={
                "owner": owner.login_user_id,
                "other": other.login_user_id,
                "out_of_scope": out_of_scope.login_user_id,
                "system_admin": admin.login_user_id,
            },
        )
    finally:
        set_security_service(None)
        repository: Any = runtime_repository
        with repository._lock:
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == agent_id
            ]:
                repository._runs.pop(run_id)
            repository._artifact_files = {
                key: data
                for key, data in repository._artifact_files.items()
                if key[0] in repository._runs
            }
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


def _run_with_file(people: People) -> tuple[str, str]:
    """持ち主の Run と、そのファイルの成果物（回答で完了にする）。"""
    run_id = _running_run(runtime_repository, agent_id=people.agent_id, user=people.owner)
    artifact = runtime_repository.save_file_artifact(
        run_id, file_name="集計 表", mime_type=XLSX, data=DATA
    )
    repository: Any = runtime_repository
    with repository._lock:
        run = repository._runs[run_id]
        run.status = RunStatus.COMPLETED
        run.artifacts.append(Artifact(name="回答", kind="answer", content={"text": "合成の回答"}))
        repository._mark_run_changed(run)
    return run_id, artifact.id


@pytest.mark.parametrize(
    ("principal", "status"),
    [("owner", 200), ("system_admin", 200), ("other", 404), ("out_of_scope", 404)],
)
def test_download_is_limited_to_people_who_can_read_the_run(
    people: People, principal: str, status: int
) -> None:
    run_id, artifact_id = _run_with_file(people)
    headers = login(people.logins[principal])

    response = client.get(f"/api/runs/{run_id}/artifacts/{artifact_id}/download", headers=headers)
    detail = client.get(f"/api/runs/{run_id}/artifacts/{artifact_id}", headers=headers)

    # Run の詳細・成果物と同じ判定（見られない利用者には、存在も中身も返さない）。
    assert (response.status_code, detail.status_code) == (status, status)
    if status != 200:
        assert not _leaks(response.text)
        return
    assert response.content == DATA
    assert response.headers["content-type"] == XLSX
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"download.xlsx\"; filename*=UTF-8''{quote('集計 表.xlsx', safe='')}"
    )
    assert response.headers["cache-control"] == "private, no-store"


def test_download_needs_a_login(people: People) -> None:
    run_id, artifact_id = _run_with_file(people)

    response = client.get(f"/api/runs/{run_id}/artifacts/{artifact_id}/download")

    assert response.status_code == 401
    assert not _leaks(response.text)


def test_download_of_other_artifacts_and_unknown_ids_is_not_found(people: People) -> None:
    run_id, _ = _run_with_file(people)
    headers = login(people.logins["owner"])
    answer = next(
        item for item in runtime_repository.get_run(run_id).artifacts if item.kind == "answer"
    )

    for path in (
        f"/api/runs/{run_id}/artifacts/{answer.id}/download",
        f"/api/runs/{run_id}/artifacts/artifact_missing/download",
        "/api/runs/run_missing/artifacts/artifact_missing/download",
    ):
        assert client.get(path, headers=headers).status_code == 404, path


def test_run_api_responses_do_not_carry_the_file_content(people: People) -> None:
    run_id, artifact_id = _run_with_file(people)
    headers = login(people.logins["owner"])
    thread_id = runtime_repository.get_run(run_id).thread_id

    for path in (
        f"/api/runs/{run_id}",
        f"/api/runs/{run_id}/artifacts",
        f"/api/runs/{run_id}/artifacts/{artifact_id}",
        f"/api/runs/{run_id}/events",
        f"/api/runs/{run_id}/audit",
        f"/api/threads/{thread_id}",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert not _leaks(response.text), path
    listed = client.get(f"/api/runs/{run_id}/artifacts", headers=headers).json()["data"]
    [file] = [item for item in listed["artifacts"] if item["kind"] == FILE_ARTIFACT_KIND]
    assert file["content"]["file_name"] == "集計 表.xlsx"


def test_mcp_run_result_does_not_carry_the_file_content(people: People) -> None:
    run_id, _ = _run_with_file(people)
    created = client.post(
        "/api/account/api-keys",
        json={"name": "外部のクライアント", "products": ["agent"]},
        headers=login(people.logins["owner"]),
    )
    assert created.status_code == 200, created.text

    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "agent_get_run", "arguments": {"run_id": run_id, "wait_seconds": 0}},
        },
        headers={"authorization": f"Bearer {created.json()['data']['token']}"},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result.get("isError") is not True, response.text
    assert not _leaks(response.text)
    # ファイルの一覧（名前・形式・大きさ）と受け取り方は、モデルの回答の文に頼らずコードで載せる。
    output = result["structuredContent"]
    assert output["answer"] == "合成の回答"
    assert output["files"] == [
        {"file_name": "集計 表.xlsx", "mime_type": XLSX, "size_bytes": len(DATA)}
    ]
    assert output["message"].endswith(FILES_DOWNLOAD_MESSAGE)
    assert "Agent の画面" in FILES_DOWNLOAD_MESSAGE and "ダウンロード" in FILES_DOWNLOAD_MESSAGE
    # MCP の応答に、ダウンロードの URL・成果物の ID は出さない。
    assert "/download" not in response.text and "artifact_" not in response.text


def test_mcp_run_result_without_files_has_an_empty_list(people: People) -> None:
    run_id = _running_run(runtime_repository, agent_id=people.agent_id, user=people.owner)

    output = run_output(runtime_repository.get_run(run_id))

    assert output.files == []
    assert FILES_DOWNLOAD_MESSAGE not in output.message


# ---- Oracle（偽の表） -----------------------------------------------------------------------


def _oracle_run(table: CheckpointTable) -> tuple[AgentRuntimeOracleCheckpointRepository, str]:
    repository = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    run = repository.create_builtin_run(RunCreateRequest(goal="合成の質問", agent_id="default"))
    assert repository.begin_builtin_run(run.id) is not None
    return repository, run.id


def test_oracle_stores_the_content_outside_the_run_row() -> None:
    table = CheckpointTable()
    repository, run_id = _oracle_run(table)

    artifact = repository.save_file_artifact(run_id, file_name="a", mime_type=DOCX, data=DATA)

    assert table.files == {("default", run_id, artifact.id): DATA}
    assert repository._artifact_files == {}
    # Run の行（ENTITY_JSON）には参照とメタデータだけ。
    row = table.entities[("default", "run", run_id)]
    assert row.text is not None and artifact.id in row.text
    assert not _leaks(row.text)
    # 別のプロセス（同じ保存先を読み込んだ repository）からも読める。
    other = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    assert other.read_artifact_file(run_id, artifact.id).data == DATA


def test_oracle_deletes_the_files_with_the_run_row() -> None:
    table = CheckpointTable()
    repository, run_id = _oracle_run(table)
    _, kept_run_id = _oracle_run(table)
    repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)
    kept = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    kept_artifact = kept.save_file_artifact(kept_run_id, file_name="b", mime_type=XLSX, data=DATA)

    # Run の行を消す書き込み（削除の印）は、同じ書き込みでその Run のファイルも消す。
    with table.connect() as connection, connection.cursor() as cursor:
        write_entities(cursor, "default", {(RUN, run_id): None}, version=table.version() + 1)

    assert table.files == {("default", kept_run_id, kept_artifact.id): DATA}


def test_oracle_without_the_table_fails_the_save_and_keeps_run_deletes_working() -> None:
    table = CheckpointTable(files_table=False)
    repository, run_id = _oracle_run(table)

    with pytest.raises(ArtifactFileError) as error:
        repository.save_file_artifact(run_id, file_name="a", mime_type=XLSX, data=DATA)

    assert error.value.code == "artifact.file_store_failed"
    assert "システムテーブル" in str(error.value)
    assert repository.get_run(run_id).artifacts == []
    # 表を作る前（migration 018 の前）でも、Run の行の削除は止めない。
    with table.connect() as connection, connection.cursor() as cursor:
        write_entities(cursor, "default", {(RUN, run_id): None}, version=table.version() + 1)
    assert table.entities[("default", "run", run_id)].text is None


def test_store_sql_uses_the_managed_table() -> None:
    from app.system_schema import MANAGED_TABLES

    assert artifact_file_store.ARTIFACT_FILE_TABLE in MANAGED_TABLES
