"""KB別の文書構造索引設定。解析・分割や検索の設定は変更しない。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PREFERENCE_KEY = "structure_index_enabled"


def structure_index_enabled(override: object, default: bool) -> bool:
    """bool以外の旧値をONへ変換しない。未指定はレシピ/globalを継承する。"""
    return override if isinstance(override, bool) else default


async def structure_index_targets(
    oracle: Any, document_id: str, *, default: bool
) -> list[str] | None:
    loader = getattr(oracle, "document_structure_index_preferences", None)
    if loader is None:
        # 決定論の旧I/O adapterはKB別設定を持たない。
        return None if default else []
    preferences = await loader(document_id)
    if preferences is None:
        return None if default else []
    return [
        kb_id for kb_id, value in preferences.items() if structure_index_enabled(value, default)
    ]


def source_fingerprint(chunk_sets: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(chunk_sets)).encode()).hexdigest()


async def structure_index_plan(oracle: Any, knowledge_base_id: str) -> list[dict[str, Any]]:
    """本文を取らず、現在見える所属文書とactive成果・補建記録を比較する。"""
    from app.clients.oracle import _oracle_access_predicate_sql, _with_tenant_bind
    from app.clients.structure_index_store import StructureIndexStore

    kb = await oracle.get_knowledge_base(knowledge_base_id)
    if kb is None:
        raise KeyError("ナレッジベースが見つかりません。")
    rows = await oracle._fetch_all(
        f"""SELECT d.document_id,d.file_name,cs.chunk_set_id,
        JSON_VALUE(r.processing_config,'$.graph_profile') AS recipe_graph_profile,
        COUNT(c.chunk_id) AS chunks,
        SUM(CASE WHEN JSON_VALUE(c.metadata_json,'$.section_title') IS NOT NULL
            OR JSON_EXISTS(c.metadata_json,'$.section_path[0]')
            OR JSON_VALUE(c.metadata_json,'$.section_path') IS NOT NULL
            THEN 1 ELSE 0 END) AS headings
        FROM rag_document_knowledge_bases m JOIN rag_documents d ON d.document_id=m.document_id
        LEFT JOIN rag_chunk_sets cs ON cs.document_id=d.document_id AND cs.is_active=1
        LEFT JOIN rag_chunks c ON c.chunk_set_id=cs.chunk_set_id
        LEFT JOIN rag_document_recipes r ON r.recipe_id=cs.recipe_id
        WHERE m.knowledge_base_id=:kb AND {_oracle_access_predicate_sql(alias="d")}
        GROUP BY d.document_id,d.file_name,cs.chunk_set_id,
            JSON_VALUE(r.processing_config,'$.graph_profile')
        ORDER BY d.document_id,cs.chunk_set_id""",
        _with_tenant_bind({"kb": knowledge_base_id}),
    )
    from app.config import get_settings

    default = get_settings().rag_graph_profile == "entities"
    override = kb.retrieval_config.get(PREFERENCE_KEY)
    builds = await StructureIndexStore(oracle).builds(knowledge_base_id)
    documents: dict[str, dict[str, Any]] = {}
    for row in rows:
        did = str(row["document_id"])
        item = documents.setdefault(
            did,
            {
                "document_id": did,
                "name": str(row["file_name"]),
                "chunk_sets": [],
                "build_chunk_sets": [],
            },
        )
        if row.get("chunk_set_id"):
            item["chunk_sets"].append(str(row["chunk_set_id"]))
            recipe_profile = row.get("recipe_graph_profile")
            recipe_default = (
                recipe_profile == "entities" if recipe_profile in {"off", "entities"} else default
            )
            if structure_index_enabled(override, recipe_default):
                item["build_chunk_sets"].append(str(row["chunk_set_id"]))
            if not row.get("chunks"):
                item["unavailable"] = "no_chunks"
            elif not row.get("headings"):
                item["unavailable"] = "no_structure"
    for item in documents.values():
        if item["chunk_sets"] and not item["build_chunk_sets"]:
            item["unavailable"] = "disabled"
        item["fingerprint"] = source_fingerprint(item["chunk_sets"])
        built = builds.get(item["document_id"])
        item["status"] = (
            "UNAVAILABLE"
            if not item["chunk_sets"] or item.get("unavailable")
            else "CURRENT"
            if built and built["source_fingerprint"] == item["fingerprint"]
            else "STALE"
            if built
            else "MISSING"
        )
        item["quality"] = built["quality"] if built else None
        item["reason"] = "no_chunks" if not item["chunk_sets"] else item.pop("unavailable", None)
    return list(documents.values())


async def rebuild_document_structure_index(
    oracle: Any,
    knowledge_base_id: str,
    document_id: str,
    *,
    expected_source: list[str] | None = None,
    expected_job_id: str | None = None,
    build_chunk_sets: list[str] | None = None,
) -> dict[str, Any]:
    """保存済みactive成果からKBの構造索引だけを補建する。本文/embeddingは読み書きしない。"""
    import asyncio

    from app.clients.oracle import _oracle_retrieval_where
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.chunking import Chunk
    from app.rag.graph_index import (
        GRAPH_ENTITY_MAX_PER_KB,
        _entity_names_for_chunk,
        build_graph_index,
    )
    from app.schemas.extraction import StructuredExtraction

    kb = await oracle.get_knowledge_base(knowledge_base_id)
    document = await oracle.get_document_summary(document_id)
    if kb is None or document is None:
        raise KeyError("対象が見つかりません。")
    preferences = await oracle.document_structure_index_preferences(document_id) or {}
    if knowledge_base_id not in preferences:
        raise ValueError("membership_changed")
    from app.config import get_settings

    fixed_target = expected_job_id is not None and build_chunk_sets is not None
    if not fixed_target and preferences[knowledge_base_id] is False:
        raise ValueError("disabled")
    where, binds = _oracle_retrieval_where(
        {"knowledge_base_id": knowledge_base_id, "document_id": document_id}
    )
    rows = await oracle._fetch_all(
        f"""SELECT c.chunk_id,c.chunk_index,c.chunk_set_id,
        JSON_VALUE(c.metadata_json,'$.section_title' RETURNING VARCHAR2(2000)) AS section_title,
        JSON_QUERY(c.metadata_json,'$.section_path' RETURNING CLOB) AS section_path_json,
        JSON_VALUE(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS section_path_text,
        JSON_VALUE(c.metadata_json,'$.content_kind') AS content_kind,
        JSON_VALUE(c.metadata_json,'$.page_number') AS page_number,
        JSON_VALUE(r.processing_config,'$.graph_profile') AS recipe_graph_profile
        FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
        LEFT JOIN rag_chunk_sets cs ON cs.chunk_set_id=c.chunk_set_id
        LEFT JOIN rag_document_recipes r ON r.recipe_id=cs.recipe_id
        WHERE {where} ORDER BY c.chunk_set_id,c.chunk_index""",
        binds,
    )
    groups: dict[str, list[Any]] = {}
    for row in rows:
        if row.get("chunk_set_id"):
            groups.setdefault(str(row["chunk_set_id"]), []).append(row)
    source = sorted(groups)
    if not source:
        raise ValueError("no_chunks")
    if expected_source is not None and source != sorted(expected_source):
        raise ValueError("source_changed")
    graphs = {}
    counts: dict[str, Any] = {
        "chunk_sets": len(source),
        "entities": 0,
        "relationships": 0,
        "links": 0,
        "quality": "COMPLETE",
    }
    for chunk_set_id, group in groups.items():
        recipe_profile = group[0].get("recipe_graph_profile")
        recipe_default = (
            (recipe_profile == "entities")
            if recipe_profile in {"off", "entities"}
            else get_settings().rag_graph_profile == "entities"
        )
        enabled = (
            chunk_set_id in (build_chunk_sets or [])
            if fixed_target
            else structure_index_enabled(preferences[knowledge_base_id], recipe_default)
        )
        if not enabled:
            counts["quality"] = "PARTIAL"
            continue
        chunks = []
        missing = 0
        for row in group:
            index = int(row["chunk_index"])
            if str(row["chunk_id"]) != f"{document_id}:{chunk_set_id}:{index}":
                raise ValueError("invalid_chunk_id")
            path = row.get("section_path_text")
            if not path and row.get("section_path_json"):
                path = json.loads(str(row["section_path_json"]))
            if isinstance(path, list):
                path = " / ".join(str(part) for part in path)
            metadata = {
                key: row[key]
                for key in ("section_title", "content_kind", "page_number")
                if row.get(key) is not None
            }
            if path:
                metadata["section_path"] = path
            if not path and not row.get("section_title"):
                missing += 1
            chunks.append(
                Chunk(text="", index=index, start_offset=0, end_offset=0, metadata=metadata)
            )
        if missing == len(group):
            raise ValueError("no_structure")
        graph = await asyncio.to_thread(
            build_graph_index,
            document_id=document_id,
            knowledge_base_ids=[knowledge_base_id],
            extraction=StructuredExtraction(),
            chunks=chunks,
            chunk_set_id=chunk_set_id,
            document_name=document.file_name,
        )
        unique = {name for chunk in chunks for name in _entity_names_for_chunk(chunk)}
        if missing or len(unique) + 1 > GRAPH_ENTITY_MAX_PER_KB:
            counts["quality"] = "PARTIAL"
        graphs[chunk_set_id] = graph
        counts["entities"] += len(graph.entities)
        counts["relationships"] += len(graph.relationships)
        counts["links"] += len(graph.entity_chunk_links)
    if not graphs:
        raise ValueError("disabled")
    counts["chunk_sets"] = len(graphs)
    await StructureIndexStore(oracle).publish(
        knowledge_base_id,
        document_id,
        source_fingerprint(source),
        source,
        graphs,
        counts["quality"],
        counts,
        **({"expected_job_id": expected_job_id} if expected_job_id is not None else {}),
    )
    return counts
