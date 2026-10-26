"""管理 API の録画相当の応答で再構築・断点再開・衝突を検証する。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.rag.test_resources_cli import ManagementApi, Rebuilder, RebuildError, read_fixture
from tests.fixtures.synthetic_pdf import text_pdf


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    documents = []
    groups = {}
    for key in ("A", "B"):
        name = f"synthetic-{key}.pdf"
        (tmp_path / name).write_bytes(text_pdf([f"Synthetic {key}"], at=(40, 40)))
        documents.append(
            {
                "group": key,
                "file": name,
                "pages": 1,
                "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest(),
            }
        )
        groups[key] = {
            "knowledge_base": {"name": f"KB {key}", "description": "合成資料"},
            "profile": {"name": f"Profile {key}", "description": "合成資料", "config": {}},
            "agent": {
                "name": f"Agent {key}",
                "description": "合成資料",
                "instructions": "資料を調べて回答する。",
                "skill_ids": ["business-rag-research"],
                "enabled": True,
            },
        }
    (tmp_path / "manifest.json").write_text(json.dumps({"synthetic": True, "documents": documents}))
    (tmp_path / "resources.json").write_text(
        json.dumps(
            {
                "version": 1,
                "recipe": {
                    "parser_adapter_backend": "docling",
                    "chunking_strategy": "small_to_big",
                },
                "groups": groups,
            }
        )
    )
    return tmp_path


class Service:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tables: dict[str, dict[str, dict[str, Any]]] = {
            key: {} for key in ("knowledge-bases", "search-answer-profiles", "documents", "agents")
        }
        self.recipes: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_after_create: str | None = None
        self.ready = True
        self.prefix = "new-"

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/", 1)[1]
        self.requests.append((request.method, path))
        parts = path.split("/")
        resource = parts[0]
        if resource == "skills":
            return httpx.Response(200, json={"data": {"id": parts[1]}})
        table = self.tables[resource]
        payload = (
            json.loads(request.content)
            if request.content and "json" in request.headers.get("content-type", "")
            else {}
        )
        result: Any
        if len(parts) == 1 and request.method == "GET":
            rows = list(table.values())
            if resource == "agents":
                result = {"agents": rows}
            else:
                if request.url.params.get("q"):
                    rows = [
                        row for row in rows if request.url.params["q"] in row.get("file_name", "")
                    ]
                if request.url.params.get("knowledge_base_id"):
                    rows = [
                        row
                        for row in rows
                        if {k["id"] for k in row["knowledge_bases"]}
                        == {request.url.params["knowledge_base_id"]}
                    ]
                offset = int(request.url.params.get("offset", 0))
                result = {"items": rows[offset : offset + 1], "total": len(rows)}
        elif len(parts) == 1 and request.method == "POST":
            item_id = self.prefix + resource + str(len(table) + 1)
            result = {**payload, "id": item_id, "status": "ACTIVE"}
            table[item_id] = deepcopy(result)
            if self.fail_after_create == resource:
                self.fail_after_create = None
                raise httpx.ReadTimeout("secret URL must never be logged", request=request)
        elif resource == "documents" and parts[1] == "upload":
            filename = re.search(rb'filename="([^"]+)"', request.content)
            assert filename
            name = filename.group(1).decode()
            source = next(path for path in self.root.glob("*.pdf") if name.endswith(path.name))
            kb = re.search(rb'name="knowledge_base_ids"\r\n\r\n([^\r]+)', request.content)
            assert kb
            item_id = self.prefix + resource + str(len(table) + 1)
            result = {
                "id": item_id,
                "file_name": name,
                "content_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "knowledge_bases": [{"id": kb.group(1).decode()}],
                "status": "UPLOADED",
            }
            table[item_id] = result
            self.recipes[item_id] = {
                "recipe_id": "recipe-" + item_id,
                "effective_processing_config": {},
                "status": "UPLOADED",
                "searchable": False,
                "needs_reprocessing": False,
                "chunk_count": 0,
                "vector_count": 0,
                "active_chunk_set_id": None,
            }
            self.jobs[item_id] = []
        elif resource == "documents" and len(parts) >= 3 and parts[2] == "recipes":
            recipe = self.recipes[parts[1]]
            if request.method == "PUT":
                recipe["effective_processing_config"] = payload
                result = recipe
            elif request.method == "POST":
                result = {"id": "job-" + parts[1], "status": "RUNNING"}
                self.jobs[parts[1]].append(result)
                if self.ready:
                    result["status"] = "SUCCEEDED"
                    recipe.update(
                        status="INDEXED",
                        searchable=True,
                        chunk_count=2,
                        vector_count=2,
                        active_chunk_set_id="chunks-" + parts[1],
                    )
            else:
                result = [recipe]
        elif resource == "documents" and parts[2:] == ["ingestion-jobs"]:
            result = self.jobs[parts[1]]
        elif resource == "agents" and parts[2:] == ["publish"]:
            result = table[parts[1]]
            version = {k: v for k, v in result.items() if k != "enabled"}
            result.update(published_version=1, versions=[{**version, "version": 1}])
        else:
            result = table[parts[1]]
        return httpx.Response(
            200, json={"data": deepcopy(result), "error_messages": [], "warning_messages": []}
        )


def rebuilder(service: Service, root: Path, **kwargs: Any) -> Rebuilder:
    client = httpx.Client(transport=httpx.MockTransport(service.handle))
    return Rebuilder(
        ManagementApi(client, "https://suite.invalid/rag/api"),
        ManagementApi(client, "https://suite.invalid/agent/api"),
        root,
        root / "state.json",
        log=lambda _: None,
        **kwargs,
    )


def test_rebuild_resolves_ids_and_rerun_has_no_mutation(corpus: Path) -> None:
    service = Service(corpus)
    result = rebuilder(service, corpus).run()
    assert result["complete"]
    assert len(result["documents"]) == 2
    assert all(row["vector_count"] == 2 for row in result["documents"].values())
    for key, group in result["groups"].items():
        profile = service.tables["search-answer-profiles"][group["profile_id"]]
        agent = service.tables["agents"][group["agent_id"]]
        assert profile["config"]["knowledge_base_ids"] == [group["kb_id"]]
        assert agent["data_scopes"]["rag"] == {
            "profile_ids": [group["profile_id"]],
            "default_profile_id": group["profile_id"],
        }
        assert agent["published_version"] == 1
        assert next(
            row
            for row in service.tables["documents"].values()
            if row["file_name"] == f"synthetic-{key}.pdf"
        )["knowledge_bases"] == [{"id": group["kb_id"]}]
    service.requests.clear()
    assert rebuilder(service, corpus).run()["complete"]
    assert all(method == "GET" for method, _ in service.requests)


@pytest.mark.parametrize("resource", ["knowledge-bases", "search-answer-profiles", "agents"])
def test_disconnect_after_create_is_discovered_without_duplicate(
    corpus: Path, resource: str
) -> None:
    service = Service(corpus)
    service.fail_after_create = resource
    with pytest.raises(RebuildError, match="通信") as caught:
        rebuilder(service, corpus).run()
    assert "secret" not in str(caught.value)
    assert not json.loads((corpus / "state.json").read_text())["complete"]
    assert rebuilder(service, corpus).run()["complete"]
    assert all(len(table) == 2 for table in service.tables.values())


@pytest.mark.parametrize(
    "resource,field,value",
    [
        ("knowledge-bases", "description", "different"),
        ("search-answer-profiles", "config", {"knowledge_base_ids": ["foreign"]}),
        ("agents", "data_scopes", {}),
    ],
)
def test_existing_difference_is_not_overwritten(
    corpus: Path, resource: str, field: str, value: Any
) -> None:
    service = Service(corpus)
    rebuilder(service, corpus).run()
    next(iter(service.tables[resource].values()))[field] = value
    service.requests.clear()
    with pytest.raises(RebuildError, match="異なり"):
        rebuilder(service, corpus).run()
    assert all(method == "GET" for method, _ in service.requests)


def test_wait_timeout_then_resume_does_not_enqueue_twice(corpus: Path) -> None:
    service = Service(corpus)
    service.ready = False
    ticks = iter([0, 2])
    with pytest.raises(RebuildError, match="上限"):
        rebuilder(service, corpus, timeout=1, clock=lambda: next(ticks)).run()
    assert not json.loads((corpus / "state.json").read_text())["complete"]
    for recipe in service.recipes.values():
        recipe.update(
            status="INDEXED",
            searchable=True,
            chunk_count=1,
            vector_count=1,
            active_chunk_set_id="set",
        )
    assert rebuilder(service, corpus).run()["complete"]
    assert all(len(jobs) == 1 for jobs in service.jobs.values())


def test_review_between_auto_phases_is_not_a_terminal_failure(corpus: Path) -> None:
    service = Service(corpus)
    rebuilder(service, corpus).run()
    for recipe in service.recipes.values():
        recipe.update(status="REVIEW", searchable=False)

    def finish(_: float) -> None:
        for recipe in service.recipes.values():
            recipe.update(status="INDEXED", searchable=True)

    assert rebuilder(service, corpus, sleep=finish).run()["complete"]


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
def test_failed_job_is_not_success(corpus: Path, status: str) -> None:
    service = Service(corpus)
    rebuilder(service, corpus).run()
    recipe = next(iter(service.recipes.values()))
    recipe.update(status="CHUNKED", searchable=False)
    next(iter(service.jobs.values()))[0]["status"] = status
    with pytest.raises(RebuildError, match="失敗または取消"):
        rebuilder(service, corpus).run()


def test_source_mismatch_prevents_all_writes(corpus: Path) -> None:
    service = Service(corpus)
    (corpus / "synthetic-A.pdf").write_bytes(b"different")
    with pytest.raises(RebuildError, match="SHA-256"):
        rebuilder(service, corpus).run()
    assert not service.requests


def test_archived_collision_and_duplicate_names(corpus: Path) -> None:
    service = Service(corpus)
    rebuilder(service, corpus).run()
    kb = next(iter(service.tables["knowledge-bases"].values()))
    kb["status"] = "ARCHIVED"
    with pytest.raises(RebuildError, match="異なり"):
        rebuilder(service, corpus).run()
    service.tables["knowledge-bases"]["duplicate"] = deepcopy(kb)
    with pytest.raises(RebuildError, match="複数"):
        rebuilder(service, corpus).run()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="private"),
        httpx.Response(200, json={"data": {}, "warning_messages": ["degraded"]}),
        httpx.Response(200, text="invalid"),
    ],
)
def test_errors_never_include_response_body(response: httpx.Response) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(RebuildError) as caught:
        ManagementApi(client, "https://suite.invalid/api").call("GET", "/test")
    assert "private" not in str(caught.value)


def test_repository_fixtures_are_valid() -> None:
    root = Path(__file__).resolve().parents[2] / "examples/test-pdfs-ja"
    manifest, config = read_fixture(root)
    assert len(manifest["documents"]) == 10
    assert sum(row["pages"] for row in manifest["documents"]) == 110
    assert set(config["groups"]) == {"A", "B"}


def test_namespace_creates_separate_resources_and_does_not_change_existing(corpus: Path) -> None:
    service = Service(corpus)
    original = rebuilder(service, corpus).run()
    before = deepcopy(service.tables)
    separate = rebuilder(service, corpus, namespace="isolated").run()
    assert separate["complete"]
    assert separate["groups"]["A"]["kb_id"] != original["groups"]["A"]["kb_id"]
    for kind, table in before.items():
        assert all(service.tables[kind][key] == row for key, row in table.items())
        assert len(service.tables[kind]) == 4
    service.requests.clear()
    assert rebuilder(service, corpus, namespace="isolated").run()["complete"]
    assert all(method == "GET" for method, _ in service.requests)
