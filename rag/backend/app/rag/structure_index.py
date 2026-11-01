"""KB別の文書構造索引設定。解析・分割や検索の設定は変更しない。"""

from __future__ import annotations

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
    return [
        kb_id for kb_id, value in preferences.items() if structure_index_enabled(value, default)
    ]


async def rebuild_document_structure_index(
    oracle: Any, knowledge_base_id: str, document_id: str
) -> dict[str, int]:
    """保存済みactive成果からKBの構造索引だけを補建する。本文/embeddingは読み書きしない。"""
    import asyncio
    import json

    from app.clients.oracle import _oracle_retrieval_where
    from app.rag.chunking import Chunk
    from app.rag.graph_index import build_graph_index
    from app.schemas.extraction import StructuredExtraction

    kb = await oracle.get_knowledge_base(knowledge_base_id)
    document = await oracle.get_document(document_id)
    if kb is None or document is None:
        raise KeyError("対象が見つかりません。")
    preferences = await oracle.document_structure_index_preferences(document_id)
    if knowledge_base_id not in preferences:
        raise ValueError("文書はこのKBに所属していません。")
    # この操作は既存成果の明示的な補建。未指定時だけ現在のglobalを参照する。
    from app.config import get_settings

    if not structure_index_enabled(
        preferences[knowledge_base_id], get_settings().rag_graph_profile == "entities"
    ):
        raise ValueError("文書構造索引の構築が無効です。")
    where, binds = _oracle_retrieval_where(
        {"knowledge_base_id": knowledge_base_id, "document_id": document_id}
    )
    rows = await oracle._fetch_all(
        f"""SELECT c.chunk_id, c.chunk_index, c.chunk_set_id,
        JSON_VALUE(c.metadata_json,'$.section_title' RETURNING VARCHAR2(2000)) AS section_title,
        JSON_QUERY(c.metadata_json,'$.section_path' RETURNING CLOB) AS section_path_json,
        JSON_VALUE(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS section_path_text,
        JSON_VALUE(c.metadata_json,'$.content_kind') AS content_kind,
        JSON_VALUE(c.metadata_json,'$.page_number') AS page_number
        FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
        LEFT JOIN rag_chunk_sets cs ON cs.chunk_set_id=c.chunk_set_id
        LEFT JOIN rag_document_recipes r ON r.recipe_id=cs.recipe_id
        WHERE {where} ORDER BY c.chunk_set_id,c.chunk_index""",
        binds,
    )
    groups: dict[str, list[Any]] = {}
    for row in rows:
        if not row.get("chunk_set_id"):
            continue
        groups.setdefault(str(row["chunk_set_id"]), []).append(row)
    if not groups:
        raise ValueError("構築に使えるactive索引がありません。")
    counts = {"chunk_sets": 0, "entities": 0, "relationships": 0, "links": 0}
    for chunk_set_id, group in groups.items():
        chunks = []
        for row in group:
            index = int(row["chunk_index"])
            if str(row["chunk_id"]) != f"{document_id}:{chunk_set_id}:{index}":
                raise ValueError("保存されたチャンクの識別が一致しません。")
            path = row.get("section_path_text")
            if not path and row.get("section_path_json"):
                path = json.loads(str(row["section_path_json"]))
            if isinstance(path, list):
                path = " / ".join(str(part) for part in path)
            metadata = {
                key: row.get(key)
                for key in ("section_title", "content_kind", "page_number")
                if row.get(key) is not None
            }
            if path:
                metadata["section_path"] = path
            chunks.append(
                Chunk(text="", index=index, start_offset=0, end_offset=0, metadata=metadata)
            )
        graph = await asyncio.to_thread(
            build_graph_index,
            document_id=document_id,
            knowledge_base_ids=[knowledge_base_id],
            extraction=StructuredExtraction(),
            chunks=chunks,
            chunk_set_id=chunk_set_id,
            document_name=document.file_name,
        )
        await oracle.replace_document_graph_index(
            document_id, graph, chunk_set_id=chunk_set_id, knowledge_base_ids=[knowledge_base_id]
        )
        current = await oracle.get_chunk_set(chunk_set_id)
        if current is None or int(str(current.get("is_active") or 0)) != 1:
            raise ValueError("構築中にactive版が変更されました。再実行してください。")
        counts["chunk_sets"] += 1
        counts["entities"] += len(graph.entities)
        counts["relationships"] += len(graph.relationships)
        counts["links"] += len(graph.entity_chunk_links)
    return counts
