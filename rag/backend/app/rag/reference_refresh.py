"""文書の変化の後に、KB の参照を解決し直す（#4310）。

参照の解決は構築の時点の目録で決まるので、別の文書の追加・削除・差し替えで古くなる。worker の
常駐のループが KB の「文書と有効な版」の指紋を見て、変わった後に次の回まで同じなら解決し直す
（連続のアップロードを 1 回にまとめる）。LLM・embedding を呼ばず、原文を読み直さず、保存した
参照の状態と参照先の列だけを更新する。検索・回答は参照の記録を読まないので影響しない。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter
from collections.abc import Mapping
from typing import Any

from app.rag.cross_references import ReferenceSpec
from app.rag.graph_index import GRAPH_NAME_ELISION
from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference
from app.rag.request_context import system_access_context

logger = logging.getLogger(__name__)

REFERENCE_REFRESH_INTERVAL_SECONDS = 60.0


def plan_reference_updates(
    rows: list[Mapping[str, Any]], documents: tuple[ReferenceDocument, ...]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """保存した参照を今の目録で解決し直し、変わった行と、状態ごとの件数を返す。"""
    by_set = {(item.document_id, item.chunk_set_id): item for item in documents}
    updates: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for row in rows:
        path = str(row.get("source_section_path") or "")
        source = by_set.get((str(row["source_document_id"]), str(row["source_chunk_set_id"])))
        # 列に入らず表示名で保存したパスは、相対の参照の起点に使えないので今の値のまま。
        if source is None or GRAPH_NAME_ELISION in path:
            statuses[str(row["resolution_status"])] += 1
            continue
        spec = ReferenceSpec(
            label=str(row["label"]),
            kind=row["kind"],
            key=str(row.get("reference_key") or ""),
            document_title=row.get("document_title"),
        )
        result = resolve_scoped_reference(
            spec, source=source, citing_path=path, documents=documents
        )
        target = result.target
        current = (
            row["resolution_status"],
            row.get("target_document_id"),
            row.get("target_chunk_set_id"),
            row.get("target_section_path"),
        )
        planned = (
            result.status,
            target.document_id if target else None,
            target.chunk_set_id if target else None,
            target.section_path if target else None,
        )
        statuses[result.status] += 1
        if current != planned:
            updates.append(
                {
                    "reference_id": row["reference_id"],
                    "status": result.status,
                    "target_document_id": planned[1],
                    "target_chunk_set_id": planned[2],
                    "target_section_path": planned[3],
                }
            )
    return updates, statuses


async def refresh_knowledge_base_references(oracle: Any, knowledge_base_id: str) -> dict[str, Any]:
    """KB の全部の参照を、今の目録で解決し直す。"""
    from app.clients.graph_reference_store import GraphReferenceStore, _column_section_path
    from app.clients.oracle import _executemany

    documents = await GraphReferenceStore(oracle).documents(knowledge_base_id)
    rows = await oracle._fetch_all(
        """SELECT r.reference_id,r.source_document_id,r.source_chunk_set_id,r.source_section_path,
        r.label,r.kind,r.reference_key,r.document_title,r.resolution_status,
        r.target_document_id,r.target_chunk_set_id,r.target_section_path
        FROM rag_graph_references r
        JOIN rag_chunk_sets ss ON ss.chunk_set_id=r.source_chunk_set_id AND ss.is_active=1
        WHERE r.knowledge_base_id=:kb""",
        {"kb": knowledge_base_id},
    )
    updates, statuses = await asyncio.to_thread(plan_reference_updates, rows, documents)
    for update in updates:
        update["target_section_path"] = _column_section_path(update["target_section_path"])

    def write(connection: Any) -> None:
        for offset in range(0, len(updates), 250):
            _executemany(
                connection,
                """UPDATE rag_graph_references SET resolution_status=:status,
                target_document_id=:target_document_id,target_chunk_set_id=:target_chunk_set_id,
                target_section_path=:target_section_path WHERE reference_id=:reference_id""",
                updates[offset : offset + 250],
            )

    if updates:
        await oracle._run_transaction(write)
    return {"checked": len(rows), "updated": len(updates), "references": dict(statuses)}


class ReferenceRefreshSweep:
    """KB ごとの指紋を覚え、変わって落ち着いた KB を解決し直す。"""

    def __init__(self, oracle: Any) -> None:
        self._oracle = oracle
        self._done: dict[str, str] = {}
        self._seen: dict[str, str] = {}

    async def _fingerprints(self) -> dict[str, str]:
        rows = await self._oracle._fetch_all(
            """SELECT m.knowledge_base_id,m.document_id,cs.chunk_set_id
            FROM rag_document_knowledge_bases m
            JOIN rag_chunk_sets cs ON cs.document_id=m.document_id AND cs.is_active=1
            WHERE EXISTS (SELECT 1 FROM rag_graph_references r
                WHERE r.knowledge_base_id=m.knowledge_base_id)
            ORDER BY m.knowledge_base_id,m.document_id,cs.chunk_set_id""",
            {},
        )
        members: dict[str, list[str]] = {}
        for row in rows:
            members.setdefault(str(row["knowledge_base_id"]), []).append(
                f"{row['document_id']}:{row['chunk_set_id']}"
            )
        return {
            kb_id: hashlib.sha256("\n".join(items).encode()).hexdigest()
            for kb_id, items in members.items()
        }

    async def run_once(self) -> list[str]:
        """1 回の確認。解決し直した KB の id を返す。"""
        fingerprints = await self._fingerprints()
        refreshed: list[str] = []
        for kb_id, fingerprint in fingerprints.items():
            stable = self._seen.get(kb_id) == fingerprint
            self._seen[kb_id] = fingerprint
            if not stable or self._done.get(kb_id) == fingerprint:
                continue
            result = await refresh_knowledge_base_references(self._oracle, kb_id)
            self._done[kb_id] = fingerprint
            refreshed.append(kb_id)
            logger.info(
                "reference_refresh_completed",
                extra={"knowledge_base_id": kb_id, **result},
            )
        return refreshed


async def run_reference_refresh_loop(
    oracle: Any,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = REFERENCE_REFRESH_INTERVAL_SECONDS,
) -> None:
    """worker の常駐のループ。失敗しても取込を止めず、次の回にやり直す。"""
    # 特定の利用者の権限ではなく、KB の全文書の目録で解決する（読むときに権限を確かめる）。
    sweep = ReferenceRefreshSweep(oracle)
    with system_access_context():
        while not stop_event.is_set():
            try:
                await sweep.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 補助の処理。次の回にやり直す。
                logger.warning("reference_refresh_failed", extra={"error_type": type(exc).__name__})
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
