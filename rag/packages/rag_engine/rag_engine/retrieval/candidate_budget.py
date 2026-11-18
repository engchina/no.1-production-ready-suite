"""rerank の入力の選択（同一成果物の同じ原文の重複除去と、rerank に入れない経路の除外）。関連度は再判定しない。"""

from collections.abc import Sequence
from typing import Any

# rerank に入れる経路。検索拡張（参照・実体・構造・目録）の chunk は rerank の候補に入らない（#5162）。
# 件数は検索経路ごとの取得件数（backend の RAG_RETRIEVAL_QUERY_LIMIT）で決まり、ここでは切らない（#5443）。
RERANK_STAGES = frozenset({"fusion", "attachment"})


def record_id(record: Any) -> str:
    return str(record.chunk_uid or record.id)


def artifact_key(record: Any) -> tuple[str, ...]:
    metadata = record.metadata or {}
    return (
        str(metadata.get("document_id") or record.source),
        str(record.source_run_id or ""),
        str(metadata.get("recipe_id") or ""),
        str(metadata.get("chunk_set_id") or ""),
        str(metadata.get("artifact_id") or ""),
    )


def select_candidates(records: Sequence[Any]) -> tuple[list[Any], dict[str, Any]]:
    """既存の順位を保ち、同じ成果物の同文だけを統合し、rerank に入れない経路の候補を除く。

    別文書・版・レシピの同文は統合しない。範囲の異なる部分一致も、同じ事実との推測では消さない。
    """
    seen_ids: set[tuple[Any, ...]] = set()
    originals: dict[tuple[Any, ...], str] = {}
    selected: list[Any] = []
    excluded: list[dict[str, str]] = []
    admitted: dict[str, int] = {}
    for record in records:
        uid = record_id(record)
        text = str(record.text or "").strip()
        refs = tuple(sorted({str(ref.get("record_id") or "") for ref in record.source_record_refs if ref.get("record_id")}))
        artifact = artifact_key(record)
        # 同文でも別ページの繰り返しは同じ根拠ではない。成果物と抽出元が分からない場合は ID だけで比較する。
        source = refs if refs and (record.source_run_id or record.metadata.get("chunk_set_id")) else (uid,)
        key = (*artifact, source, text)
        identity = (*artifact, uid)
        if identity in seen_ids or (text and key in originals):
            excluded.append({"id": uid, "reason": "duplicate_original", "retained_id": originals.get(key, uid)})
            continue
        seen_ids.add(identity)
        if text:
            originals[key] = uid
        stage = str(record.metadata.get("retrieval_admission_stage") or "fusion")
        if stage not in RERANK_STAGES:
            excluded.append({"id": uid, "reason": "not_rerank_stage", "stage": stage})
            continue
        selected.append(record)
        admitted[stage] = admitted.get(stage, 0) + 1
    return selected, {
        "input_count": len(records),
        "admitted": admitted,
        "selected_ids": [record_id(record) for record in selected],
        "excluded": excluded,
    }
