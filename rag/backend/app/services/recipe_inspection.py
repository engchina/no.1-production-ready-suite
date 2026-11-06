"""分割確認の版選択。配信中の版の切替や検索対象は変更しない。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.clients.oracle import OracleClient


def _candidate_phase(row: Mapping[str, object]) -> bool:
    return row.get("status") in {"CHUNKED", "INDEXING"} or (
        row.get("status") == "ERROR" and row.get("failed_phase") == "INDEX"
    )


def inspection_chunk_set_id(row: Mapping[str, object]) -> str | None:
    """分割完了・索引処理中・索引失敗では保存済み候補を確認する。"""
    value = (
        row.get("pending_chunk_set_id") if _candidate_phase(row) else row.get("active_chunk_set_id")
    )
    return str(value) if value else None


@dataclass(frozen=True)
class ChunkInspectionVersion:
    chunk_set_id: str | None
    extraction_id: str | None = None
    pending: bool = False
    payload: Mapping[str, Any] | None = None


async def chunk_inspection_version(
    oracle: OracleClient,
    document_id: str,
    recipe_id: str,
    row: Mapping[str, object],
    *,
    expected_revision: str | None = None,
) -> ChunkInspectionVersion:
    """選んだ分割版とその抽出を確かめる。消失・不明な版を別版で補わない。"""
    chunk_set_id = inspection_chunk_set_id(row)
    if expected_revision is not None and expected_revision != chunk_set_id:
        raise HTTPException(
            status_code=409, detail="確認する分割結果の版が変わりました。再読み込みしてください。"
        )
    if chunk_set_id is None:
        if _candidate_phase(row):
            raise HTTPException(
                status_code=409, detail="確認する分割結果の版が記録されていません。"
            )
        return ChunkInspectionVersion(None)
    chunk_set = await oracle.get_chunk_set(chunk_set_id)
    if (
        chunk_set is None
        or chunk_set.get("document_id") != document_id
        or chunk_set.get("recipe_id") != recipe_id
        or not chunk_set.get("extraction_recipe_id")
    ):
        raise HTTPException(status_code=409, detail="確認する分割結果の版を読み取れません。")
    extraction_id = str(chunk_set["extraction_recipe_id"])
    pending = chunk_set_id != row.get("active_chunk_set_id")
    payload: Mapping[str, Any] | None = None
    if pending:
        artifact = await oracle.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=extraction_id
        )
        saved_payload = artifact.get("extraction_json") if artifact else None
        if not isinstance(saved_payload, Mapping):
            raise HTTPException(
                status_code=409, detail="分割結果に対応する抽出の版を読み取れません。"
            )
        payload = saved_payload
    return ChunkInspectionVersion(chunk_set_id, extraction_id, pending, payload)
