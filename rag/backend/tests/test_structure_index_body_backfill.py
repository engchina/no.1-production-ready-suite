"""「本文」の節点の前に作った構造索引を、補建の対象として見つけて直す合成回帰。

本文の節点（#4507）の前の索引は、見出しの無い chunk を「文書全体」の章節に結び、本文の節点への
結び付きを持たない。保存した版の指紋は変わらないので、版の比較だけでは構築済みに見える。
"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from rag_parser_core.language import current_pack

from app.rag import structure_index_jobs as jobs_module
from app.rag.chunking import Chunk
from app.rag.graph_index import (
    STRUCTURE_INDEX_BUILDER_VERSION,
    GraphEntity,
    GraphEntityChunkLink,
    GraphIndex,
    build_graph_index,
    graph_body_node_id,
    graph_section_node_id,
    is_body_chunk,
)
from app.rag.structure_index import source_fingerprint, structure_index_plan
from app.rag.structure_tree import StructureChunkRow, build_structure_tree
from app.schemas.extraction import StructuredExtraction

KB = "kb-a"
OTHER_KB = "kb-b"
WHOLE = current_pack().text("labels", "whole_document")


def _json_value(metadata: dict[str, Any], key: str) -> str | None:
    """Oracle の JSON_VALUE と同じく、配列・JSON null は NULL、文字列はそのまま。"""
    value = metadata.get(key)
    if value is None or isinstance(value, bool | list | dict):
        return None
    return str(value)


def _json_query(metadata: dict[str, Any], key: str) -> str | None:
    """Oracle の JSON_QUERY（WRAPPER なし）と同じく、配列・object だけを JSON で返す。"""
    value = metadata.get(key)
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list | dict) else None


class MemoryOracle:
    """構造索引の読み書きを、保存した chunk と graph の行だけで再現する。

    chunk・embedding を書き込む口は持たない（補建がそれらを変えないことの確認を兼ねる）。
    """

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, GraphEntity] = {}
        self.links: set[tuple[str, str, str]] = set()
        self.builds: dict[str, str] = {}
        self.visible: set[str] = set()
        self.job_state: dict[str, Any] = {}
        self.sqls: list[str] = []

    # --- 合成データの準備 ---
    def add_document(
        self, document_id: str, metadata: list[dict[str, Any]], *, kb: str = KB
    ) -> str:
        chunk_set = f"cs-{document_id}"
        self.documents[document_id] = {
            "name": f"synthetic-{document_id}.txt",
            "chunk_set": chunk_set,
            "kb": kb,
            "chunks": [
                {"text": f"合成本文{index}", "metadata": dict(meta)}
                for index, meta in enumerate(metadata)
            ],
        }
        self.visible.add(document_id)
        return chunk_set

    def save_graph(self, document_id: str, graph: GraphIndex) -> None:
        chunk_set = self.documents[document_id]["chunk_set"]
        kb = self.documents[document_id]["kb"]
        self.entities = {
            key: entity
            for key, entity in self.entities.items()
            if not (self._entity_chunk_set(entity) == chunk_set and entity.knowledge_base_id == kb)
        }
        live = set(self.entities)
        self.links = {link for link in self.links if link[0] in live}
        for entity in graph.entities:
            self.entities[entity.entity_id] = entity
        for link in graph.entity_chunk_links:
            self.links.add((link.entity_id, link.chunk_id, link.document_id))

    def _entity_chunk_set(self, entity: GraphEntity) -> str:
        return str(self.documents[entity.source_document_ids[0]]["chunk_set"])

    def chunk_ids(self, document_id: str) -> list[str]:
        doc = self.documents[document_id]
        return [f"{document_id}:{doc['chunk_set']}:{i}" for i in range(len(doc["chunks"]))]

    # --- OracleClient の読み取り ---
    async def get_knowledge_base(self, kb_id: str) -> Any:
        if kb_id not in {KB, OTHER_KB}:
            return None
        return SimpleNamespace(retrieval_config={"structure_index_enabled": True})

    async def get_document_summary(self, document_id: str) -> Any:
        doc = self.documents.get(document_id)
        return SimpleNamespace(file_name=doc["name"]) if doc else None

    async def document_structure_index_preferences(self, document_id: str) -> dict[str, Any]:
        return {self.documents[document_id]["kb"]: True}

    def _scoped(self, binds: dict[str, Any], prefix: str) -> set[str] | None:
        values = {str(v) for k, v in binds.items() if k.startswith(prefix)}
        return values or None

    async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.sqls.append(sql)
        assert "embedding" not in sql.lower()
        if "COUNT(c.chunk_id) AS chunks" in sql:
            return self._plan_rows(binds)
        if "AS body_links" in sql:
            return self._body_link_rows(binds)
        if "SELECT c.chunk_set_id,c.chunk_index" in sql:
            return self._structure_rows(binds)
        if "reference_source_text" in sql:
            return self._rebuild_rows(binds)
        if "SELECT DISTINCT c.document_id" in sql:
            return []
        raise AssertionError(f"想定しない SQL: {sql[:80]}")

    def _plan_rows(self, binds: dict[str, Any]) -> list[dict[str, Any]]:
        wanted = self._scoped(binds, "structure_doc")
        return [
            {
                "document_id": did,
                "file_name": doc["name"],
                "chunk_set_id": doc["chunk_set"],
                "recipe_graph_profile": None,
                "chunks": len(doc["chunks"]),
                "headings": 0,
            }
            for did, doc in sorted(self.documents.items())
            if doc["kb"] == binds["kb"]
            and did in self.visible
            and (wanted is None or did in wanted)
        ]

    def _body_link_rows(self, binds: dict[str, Any]) -> list[dict[str, Any]]:
        sets = self._scoped(binds, "body_link_set") or set()
        rows = []
        for did, doc in sorted(self.documents.items()):
            cs = doc["chunk_set"]
            if cs not in sets:
                continue
            if not any(
                self._entity_chunk_set(e) == cs and e.knowledge_base_id == binds["kb"]
                for e in self.entities.values()
            ):
                continue
            body = graph_body_node_id(cs, binds["kb"])
            count = sum(
                1
                for entity_id, chunk_id, link_doc in self.links
                if entity_id == body
                and link_doc == did
                and self.entities[entity_id].entity_type == "body"
                and chunk_id in self.chunk_ids(did)
            )
            rows.append({"chunk_set_id": cs, "body_links": count})
        return rows

    def _structure_rows(self, binds: dict[str, Any]) -> list[dict[str, Any]]:
        sets = self._scoped(binds, "structure_row_set") or set()
        rows = []
        for doc in self.documents.values():
            if doc["chunk_set"] not in sets:
                continue
            for index, chunk in enumerate(doc["chunks"]):
                meta = chunk["metadata"]
                rows.append(
                    {
                        "chunk_set_id": doc["chunk_set"],
                        "chunk_index": index,
                        "path": _json_value(meta, "section_path"),
                        "path_json": _json_query(meta, "section_path"),
                        "title": _json_value(meta, "section_title"),
                        "kind": _json_value(meta, "content_kind"),
                        "long_value": 0,
                    }
                )
        return rows

    def _rebuild_rows(self, binds: dict[str, Any]) -> list[dict[str, Any]]:
        did = next(str(v) for v in binds.values() if str(v) in self.documents)
        doc = self.documents[did]
        rows = []
        for index, chunk in enumerate(doc["chunks"]):
            meta = chunk["metadata"]
            rows.append(
                {
                    "chunk_id": f"{did}:{doc['chunk_set']}:{index}",
                    "chunk_index": index,
                    "chunk_set_id": doc["chunk_set"],
                    "reference_source_text": chunk["text"],
                    "section_title": _json_value(meta, "section_title"),
                    "section_path_json": _json_query(meta, "section_path"),
                    "section_path_text": _json_value(meta, "section_path"),
                    "content_kind": _json_value(meta, "content_kind"),
                    "page_number": _json_value(meta, "page_number"),
                    "stored_references": None,
                    "recipe_graph_profile": None,
                }
            )
        return rows


class MemoryStore:
    """構築の記録と作業の状態を MemoryOracle に置く StructureIndexStore。"""

    def __init__(self, oracle: MemoryOracle) -> None:
        self.oracle = oracle

    async def builds(self, kb_id: str) -> dict[str, dict[str, Any]]:
        return {
            did: {
                "document_id": did,
                "source_fingerprint": fp,
                "quality": "COMPLETE",
                "builder_version": STRUCTURE_INDEX_BUILDER_VERSION,
            }
            for did, fp in self.oracle.builds.items()
            if self.oracle.documents[did]["kb"] == kb_id
        }

    async def publish(
        self,
        kb_id: str,
        document_id: str,
        fingerprint: str,
        source: list[str],
        graphs: dict[str, GraphIndex],
        quality: str,
        result: dict[str, Any],
        *,
        expected_job_id: str | None = None,
    ) -> None:
        assert source == [self.oracle.documents[document_id]["chunk_set"]]
        for graph in graphs.values():
            self.oracle.save_graph(document_id, graph)
        self.oracle.builds[document_id] = fingerprint

    async def get(self, kb_id: str) -> Any:
        return deepcopy(self.oracle.job_state.get(kb_id))

    async def mutate(self, kb_id: str, change: Any, *, start: bool = False) -> Any:
        old = deepcopy(self.oracle.job_state.get(kb_id))
        if start and old and old["status"] in {"RUNNING", "STOPPING"}:
            raise ValueError("構築中")
        self.oracle.job_state[kb_id] = change(old)
        return deepcopy(self.oracle.job_state[kb_id])

    async def visible_state(self, kb_id: str, state: Any) -> Any:
        return state


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> MemoryOracle:
    from app.clients import structure_index_store

    async def actor() -> Any:
        from app.rag.request_context import current_audit_request_context

        return current_audit_request_context()

    monkeypatch.setattr(structure_index_store, "StructureIndexStore", MemoryStore)
    monkeypatch.setattr(jobs_module, "StructureIndexStore", MemoryStore)
    monkeypatch.setattr(jobs_module, "refresh_actor", actor)
    return MemoryOracle()


def _chunks(oracle: MemoryOracle, document_id: str) -> list[Chunk]:
    """補建と同じ形の metadata（配列のパスは「 > 」でつなぐ）の chunk。"""
    chunks = []
    for index, chunk in enumerate(oracle.documents[document_id]["chunks"]):
        meta = dict(chunk["metadata"])
        if isinstance(meta.get("section_path"), list):
            meta["section_path"] = " > ".join(str(p) for p in meta["section_path"])
        chunks.append(
            Chunk(text=chunk["text"], index=index, start_offset=0, end_offset=0, metadata=meta)
        )
    return chunks


def _current_graph(oracle: MemoryOracle, document_id: str) -> GraphIndex:
    doc = oracle.documents[document_id]
    return build_graph_index(
        document_id=document_id,
        knowledge_base_ids=[doc["kb"]],
        extraction=StructuredExtraction(),
        chunks=_chunks(oracle, document_id),
        chunk_set_id=doc["chunk_set"],
        document_name=doc["name"],
    )


def _pre_body_graph(oracle: MemoryOracle, document_id: str, body: list[bool]) -> GraphIndex:
    """#4507 の前の形: 見出しの無い chunk は「文書全体」の章節に結び、本文の節点は無い。"""
    doc = oracle.documents[document_id]
    graph = _current_graph(oracle, document_id)
    body_id = graph_body_node_id(doc["chunk_set"], doc["kb"])
    whole_id = graph_section_node_id(doc["chunk_set"], doc["kb"], WHOLE)
    chunk_ids = oracle.chunk_ids(document_id)
    entities = [e for e in graph.entities if e.entity_id != body_id]
    links = [
        link
        for link in graph.entity_chunk_links
        if link.entity_id != body_id and chunk_ids.index(link.chunk_id) not in _true(body)
    ]
    if any(body):
        entities.append(
            GraphEntity(
                entity_id=whole_id,
                knowledge_base_id=doc["kb"],
                canonical_name=WHOLE,
                entity_type="section",
                description=WHOLE,
                confidence=1.0,
                source_document_ids=[document_id],
            )
        )
        links += [
            GraphEntityChunkLink(whole_id, chunk_ids[index], document_id, 0.8)
            for index in _true(body)
        ]
    return replace(graph, entities=entities, entity_chunk_links=links)


def _true(flags: list[bool]) -> list[int]:
    return [index for index, flag in enumerate(flags) if flag]


def _install_old_index(oracle: MemoryOracle, document_id: str, body: list[bool]) -> None:
    oracle.save_graph(document_id, _pre_body_graph(oracle, document_id, body))
    oracle.builds[document_id] = source_fingerprint([oracle.documents[document_id]["chunk_set"]])


def _body_links(oracle: MemoryOracle, document_id: str) -> list[str]:
    doc = oracle.documents[document_id]
    body = graph_body_node_id(doc["chunk_set"], doc["kb"])
    return sorted(chunk for entity, chunk, _doc in oracle.links if entity == body)


HEADED = {"section_path": "合成章 > 合成節", "section_title": "合成節"}


async def test_old_index_without_body_links_is_stale_and_missing_mode_rebuilds_it(
    oracle: MemoryOracle,
) -> None:
    oracle.add_document("old", [{}, HEADED, {"section_title": "  "}])
    oracle.add_document("fresh", [{}, HEADED])
    oracle.add_document("headed", [HEADED])
    _install_old_index(oracle, "old", [True, False, True])
    oracle.save_graph("fresh", _current_graph(oracle, "fresh"))
    oracle.builds["fresh"] = source_fingerprint(["cs-fresh"])
    _install_old_index(oracle, "headed", [False])
    chunks_before = deepcopy({did: doc["chunks"] for did, doc in oracle.documents.items()})

    plan = {item["document_id"]: item for item in await structure_index_plan(oracle, KB)}
    assert plan["old"]["status"] == "STALE"
    assert plan["old"]["body_links_missing"] is True
    assert plan["fresh"]["status"] == "CURRENT"
    # 見出しだけの文書は本文の節点を持たないので、旧索引でも直す対象にしない。
    assert plan["headed"]["status"] == "CURRENT"
    assert plan["old"]["reason"] is None

    service = jobs_module.StructureIndexJobs()
    await service.start(oracle, KB, "missing")  # type: ignore[arg-type]
    await asyncio.gather(*service.tasks)
    state = oracle.job_state[KB]
    assert {i["document_id"]: i["status"] for i in state["items"]} == {
        "old": "SUCCEEDED",
        "fresh": "SKIPPED",
        "headed": "SKIPPED",
    }
    body_entity = oracle.entities[graph_body_node_id("cs-old", KB)]
    assert body_entity.entity_type == "body"
    assert _body_links(oracle, "old") == ["old:cs-old:0", "old:cs-old:2"]
    assert graph_section_node_id("cs-old", KB, WHOLE) not in oracle.entities
    # 本文・metadata（埋め込みの元）は書き換えない。本文の書き込みの口も持たない。
    assert {did: doc["chunks"] for did, doc in oracle.documents.items()} == chunks_before
    assert not any("embedding" in sql.lower() for sql in oracle.sqls)
    after = {item["document_id"]: item for item in await structure_index_plan(oracle, KB)}
    assert after["old"]["status"] == "CURRENT"
    assert after["old"]["body_links_missing"] is False


_PARITY_CASES = [
    pytest.param({}, True, id="no-heading"),
    pytest.param({"section_title": "   "}, True, id="blank-title"),
    pytest.param({"section_title": "　\t"}, True, id="wide-space-title"),
    pytest.param({"section_path": []}, True, id="empty-array"),
    pytest.param({"section_path": ["", " "]}, True, id="blank-array"),
    pytest.param({"section_path": " > "}, True, id="separator-only"),
    pytest.param({"section_path": None, "section_title": None}, True, id="json-null"),
    pytest.param({"section_path": ["合成章", "合成節"]}, False, id="array-path"),
    pytest.param({"section_path": "合成章 > 合成節"}, False, id="string-path"),
    pytest.param({"section_title": "合成題名"}, False, id="title-only"),
    pytest.param({"section_path": [], "section_title": "合成題名"}, False, id="empty-array-title"),
]


@pytest.mark.parametrize(("metadata", "body"), _PARITY_CASES)
async def test_detector_tree_and_build_use_the_same_body_judgement(
    oracle: MemoryOracle, metadata: dict[str, Any], body: bool
) -> None:
    from app.clients.structure_graph_store import _section_path_text
    from app.rag.structure_index import rebuild_document_structure_index

    chunk_set = oracle.add_document("doc", [metadata])
    path = _section_path_text(
        {
            "path": _json_value(metadata, "section_path"),
            "path_json": _json_query(metadata, "section_path"),
        }
    )
    title = _json_value(metadata, "section_title")
    assert is_body_chunk(path, title) is body
    tree = build_structure_tree(
        [StructureChunkRow(path, title)], chunk_set_id=chunk_set, knowledge_base_id=KB
    )
    assert (graph_body_node_id(chunk_set, KB) in tree.nodes) is body

    _install_old_index(oracle, "doc", [body])
    [item] = await structure_index_plan(oracle, KB)
    assert item["status"] == ("STALE" if body else "CURRENT")
    await rebuild_document_structure_index(oracle, KB, "doc")
    assert _body_links(oracle, "doc") == (["doc:cs-doc:0"] if body else [])
    [after] = await structure_index_plan(oracle, KB)
    # 補建の後は同じ判定で数えるので、二度と古い扱いにならない。
    assert after["status"] == "CURRENT"


async def test_document_ids_pilot_runs_only_selected_and_hides_foreign_or_invisible(
    oracle: MemoryOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from app.api.routes import knowledge_bases as routes
    from app.schemas.knowledge_base import StructureIndexStartRequest

    for did in ("a", "b"):
        oracle.add_document(did, [{}, HEADED])
        _install_old_index(oracle, did, [True, False])
    oracle.add_document("foreign", [{}], kb=OTHER_KB)
    _install_old_index(oracle, "foreign", [True])
    oracle.add_document("hidden", [{}])
    _install_old_index(oracle, "hidden", [True])
    oracle.visible.discard("hidden")
    monkeypatch.setattr(routes, "OracleClient", lambda: oracle)

    errors = []
    for ids in (["a", "foreign"], ["a", "hidden"], ["synthetic-missing"]):
        with pytest.raises(HTTPException) as error:
            await routes.start_structure_index_job(
                KB, StructureIndexStartRequest(mode="missing", document_ids=ids)
            )
        errors.append((error.value.status_code, error.value.detail))
    # 他の KB の文書・見えない文書・無い文書を区別せず、作業も始めない。
    assert set(errors) == {(404, "指定した文書が見つかりません。")}
    assert KB not in oracle.job_state

    response = await routes.start_structure_index_job(
        KB, StructureIndexStartRequest(document_ids=["a", " a "])
    )
    await asyncio.gather(*jobs_module.structure_index_jobs.tasks)
    assert response.data is not None and response.data["total"] == 1
    assert [item["document_id"] for item in oracle.job_state[KB]["items"]] == ["a"]
    assert oracle.job_state[KB]["items"][0]["status"] == "SUCCEEDED"
    assert _body_links(oracle, "a") == ["a:cs-a:0"]
    assert _body_links(oracle, "b") == []
    plan = {item["document_id"]: item["status"] for item in await structure_index_plan(oracle, KB)}
    assert plan == {"a": "CURRENT", "b": "STALE"}


@pytest.mark.parametrize(
    "payload",
    [
        {"document_ids": []},
        {"document_ids": ["  "]},
        {"document_ids": [f"doc-{i}" for i in range(201)]},
        {"document_ids": ["a"], "unknown": True},
    ],
    ids=["empty", "blank", "too-many", "extra-field"],
)
def test_start_request_rejects_invalid_document_scope(payload: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from app.schemas.knowledge_base import StructureIndexStartRequest

    with pytest.raises(ValidationError):
        StructureIndexStartRequest(**payload)
    assert StructureIndexStartRequest().document_ids is None


async def test_body_link_gap_check_batches_chunk_sets_and_reads_no_text() -> None:
    from app.clients.structure_graph_store import StructureGraphStore

    calls: list[tuple[str, dict[str, Any]]] = []

    class Oracle:
        async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append((sql, binds))
            assert "chunk_text" not in sql and "embedding" not in sql.lower()
            sets = sorted(str(v) for k, v in binds.items() if "_set_" in k)
            if "AS body_links" in sql:
                return [{"chunk_set_id": cs, "body_links": 0} for cs in sets]
            return [
                {"chunk_set_id": cs, "chunk_index": 0, "path": None, "path_json": None}
                for cs in sets
                if cs.endswith("7")
            ]

    sets = [f"cs-{i:04d}" for i in range(1001)]
    gaps = await StructureGraphStore(Oracle()).body_link_gaps(KB, sets)
    assert gaps == {cs for cs in sets if cs.endswith("7")}
    assert all(len(binds) <= 2 * 500 + 1 for _sql, binds in calls)
    assert len([sql for sql, _ in calls if "AS body_links" in sql]) == 3
