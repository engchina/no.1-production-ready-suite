"""抽出の修訂の版（#2644）の Oracle の表と SQL。

- ``rag_document_extraction_revisions``: レシピごとの不変の版。保存・復元のたびに 1 行足し、行を
  書き換えない（版の数の上限を超えた古い行を消すだけ）。本文は版の行の写しを持つ。
  ``rag_document_extractions`` の行は解析のやり直しの上書き・切替の掃除で消えることがあるので、
  履歴の正本にしない。レシピの削除で消える（``ON DELETE CASCADE``）。
- 版の ``extraction_recipe_id`` は保存ごとに新しいので、chunk_set（``extraction_recipe_id``）が
  どの版から作られたかを辿れる。

解析の結果は次の修正の保存まで版にしない（修正の無い文書に本文の写しを持たない）。レシピの今の抽出が
最新の版の抽出と違うときは解析がやり直されているので、保存の前に解析の版として記録する。修正は解析の
版の後に必ず続くので、再解析の後の抽出は常に新しい ID になる（ID の比較だけで判定できる）。

呼び出す側はレシピの行を ``FOR UPDATE`` で押さえている前提で、同じレシピの版の番号は直列に増える。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pr_backend_core.version_retention import versions_to_prune

from app.clients.oracle import (
    OracleConnectionProtocol,
    ReviewedExtractionWrite,
    _execute,
    _fetch_all,
    _fetch_one,
    _int_value,
    _json_bind,
    _json_input_sizes,
    _json_loads,
    _merge_document_extraction_artifact,
    _optional_int,
    _optional_str,
)
from app.config import get_settings
from app.rag.extraction_revisions import extraction_element_changes
from app.services.document_inspection import with_inspection_index

REVISIONS_TABLE = "rag_document_extraction_revisions"

ExtractionRevisionKind = Literal["PARSED", "EDITED", "RESTORED"]

_SUMMARY_COLUMNS = """revision_no, parent_revision_no, base_revision_no,
       restored_from_revision_no, revision_kind, extraction_recipe_id,
       parse_extraction_recipe_id, changed_element_count, created_at"""

LIST_REVISIONS_SQL = f"""
SELECT {_SUMMARY_COLUMNS}
FROM {REVISIONS_TABLE}
WHERE recipe_id = :recipe_id
ORDER BY revision_no DESC
"""

GET_REVISION_SQL = f"""
SELECT {_SUMMARY_COLUMNS}, extraction_json
FROM {REVISIONS_TABLE}
WHERE recipe_id = :recipe_id AND revision_no = :revision_no
"""

SERVED_EXTRACTIONS_OF_RECIPE_SQL = """
SELECT extraction_recipe_id FROM rag_chunk_sets
WHERE recipe_id = :recipe_id AND is_active = 1 AND status = 'INDEXED'
  AND extraction_recipe_id IS NOT NULL
"""


def oracle_extraction_revision_schema_sql() -> str:
    return f"""
CREATE TABLE {REVISIONS_TABLE} (
    recipe_id                  VARCHAR2(64) NOT NULL,
    revision_no                NUMBER(10) NOT NULL,
    parent_revision_no         NUMBER(10),
    base_revision_no           NUMBER(10) NOT NULL,
    restored_from_revision_no  NUMBER(10),
    revision_kind              VARCHAR2(16) NOT NULL,
    extraction_recipe_id       VARCHAR2(64) NOT NULL,
    parse_extraction_recipe_id VARCHAR2(64) NOT NULL,
    changed_element_count      NUMBER(10) DEFAULT 0 NOT NULL,
    extraction_json            JSON NOT NULL,
    created_at                 TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_doc_ext_rev_pk PRIMARY KEY (recipe_id, revision_no),
    CONSTRAINT rag_doc_ext_rev_recipe_fk FOREIGN KEY (recipe_id)
        REFERENCES rag_document_recipes (recipe_id) ON DELETE CASCADE,
    CONSTRAINT rag_doc_ext_rev_kind_ck
        CHECK (revision_kind IN ('PARSED', 'EDITED', 'RESTORED'))
);
""".strip()


def max_extraction_revisions() -> int:
    """レシピごとに残す版の数（``RAG_EXTRACTION_MAX_REVISIONS``）。"""
    return max(1, int(get_settings().rag_extraction_max_revisions))


def _summary_row(row: Mapping[str, object]) -> dict[str, object]:
    return {str(key).lower(): value for key, value in row.items()}


def latest_revision(
    connection: OracleConnectionProtocol, recipe_id: str
) -> dict[str, object] | None:
    row = _fetch_one(
        connection,
        f"""
        SELECT revision_no, base_revision_no, extraction_recipe_id
        FROM {REVISIONS_TABLE}
        WHERE recipe_id = :recipe_id
        ORDER BY revision_no DESC
        FETCH FIRST 1 ROWS ONLY
        """,
        {"recipe_id": recipe_id},
    )
    return _summary_row(row) if row is not None else None


def _insert_revision(
    connection: OracleConnectionProtocol,
    *,
    recipe_id: str,
    revision_no: int,
    parent_revision_no: int | None,
    base_revision_no: int,
    restored_from_revision_no: int | None,
    kind: ExtractionRevisionKind,
    extraction_recipe_id: str,
    parse_extraction_recipe_id: str,
    changed_element_count: int,
    extraction: Mapping[str, object],
) -> None:
    _execute(
        connection,
        f"""
        INSERT INTO {REVISIONS_TABLE} (
            recipe_id, revision_no, parent_revision_no, base_revision_no,
            restored_from_revision_no, revision_kind, extraction_recipe_id,
            parse_extraction_recipe_id, changed_element_count, extraction_json
        ) VALUES (
            :recipe_id, :revision_no, :parent_revision_no, :base_revision_no,
            :restored_from_revision_no, :revision_kind, :extraction_recipe_id,
            :parse_extraction_recipe_id, :changed_element_count, :extraction_json
        )
        """,
        {
            "recipe_id": recipe_id,
            "revision_no": revision_no,
            "parent_revision_no": parent_revision_no,
            "base_revision_no": base_revision_no,
            "restored_from_revision_no": restored_from_revision_no,
            "revision_kind": kind,
            "extraction_recipe_id": extraction_recipe_id,
            "parse_extraction_recipe_id": parse_extraction_recipe_id,
            "changed_element_count": changed_element_count,
            "extraction_json": _json_bind(extraction),
        },
        input_sizes=_json_input_sizes("extraction_json"),
    )


def _record_current_extraction(
    connection: OracleConnectionProtocol,
    *,
    recipe_id: str,
    extraction_recipe_id: str,
    parse_extraction_recipe_id: str,
    extraction: Mapping[str, object],
) -> tuple[int, int]:
    """レシピの今の抽出の版を返す（版が無ければ解析の版を記録する）。(版の番号, 基の解析の版)。"""
    latest = latest_revision(connection, recipe_id)
    if latest is not None and latest["extraction_recipe_id"] == extraction_recipe_id:
        return _int_value(latest["revision_no"]), _int_value(latest["base_revision_no"])
    revision_no = _int_value(latest["revision_no"]) + 1 if latest is not None else 1
    _insert_revision(
        connection,
        recipe_id=recipe_id,
        revision_no=revision_no,
        parent_revision_no=_int_value(latest["revision_no"]) if latest is not None else None,
        base_revision_no=revision_no,
        restored_from_revision_no=None,
        kind="PARSED",
        extraction_recipe_id=extraction_recipe_id,
        parse_extraction_recipe_id=parse_extraction_recipe_id,
        changed_element_count=0,
        extraction=extraction,
    )
    return revision_no, revision_no


def commit_extraction_revision(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    recipe_id: str,
    tenant_id_hash: str | None,
    source_extraction_recipe_id: str,
    source_extraction: Mapping[str, object],
    write: ReviewedExtractionWrite,
    kind: Literal["EDITED", "RESTORED"],
    restored_from: tuple[int, int] | None = None,
) -> int:
    """新しい版を書き、レシピの今の抽出を切り替えて、版の番号を返す。

    ``restored_from`` は復元のときの (元の版の番号, その版の基の解析の版)。切替の前の抽出は、どの
    chunk_set・レシピからも参照されなければ消す（内容は版に残っている）。検索に出している版の抽出は
    chunk_set が参照するので残る。
    """
    parent, base = _record_current_extraction(
        connection,
        recipe_id=recipe_id,
        extraction_recipe_id=source_extraction_recipe_id,
        parse_extraction_recipe_id=write.parse_extraction_recipe_id,
        extraction=source_extraction,
    )
    payload = with_inspection_index(write.extraction)
    _merge_document_extraction_artifact(
        connection,
        {
            "document_id": document_id,
            "extraction_recipe_id": write.extraction_recipe_id,
            "source_sha256": write.source_sha256,
            "tenant_id_hash": tenant_id_hash,
            "recipe_subset": _json_bind(write.recipe_subset),
            "extraction_json": _json_bind(payload),
            "status": write.status,
            "reason": None,
            "metrics_json": _json_bind(None),
        },
        update_extraction=True,
    )
    _execute(
        connection,
        """
        UPDATE rag_document_recipes
        SET active_extraction_recipe_id = :extraction_recipe_id,
            failed_phase = NULL,
            error_message = NULL,
            finished_at = NULL,
            updated_at = SYSTIMESTAMP
        WHERE recipe_id = :recipe_id
        """,
        {"recipe_id": recipe_id, "extraction_recipe_id": write.extraction_recipe_id},
    )
    revision_no = parent + 1
    base_revision_no = restored_from[1] if restored_from is not None else base
    _insert_revision(
        connection,
        recipe_id=recipe_id,
        revision_no=revision_no,
        parent_revision_no=parent,
        base_revision_no=base_revision_no,
        restored_from_revision_no=restored_from[0] if restored_from is not None else None,
        kind=kind,
        extraction_recipe_id=write.extraction_recipe_id,
        parse_extraction_recipe_id=write.parse_extraction_recipe_id,
        changed_element_count=len(extraction_element_changes(source_extraction, write.extraction)),
        extraction=payload,
    )
    _execute(
        connection,
        """
        DELETE FROM rag_document_extractions e
        WHERE e.document_id = :document_id
          AND e.extraction_recipe_id = :extraction_recipe_id
          AND NOT EXISTS (
              SELECT 1 FROM rag_chunk_sets cs
              WHERE cs.extraction_recipe_id = e.extraction_recipe_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM rag_document_recipes r
              WHERE r.active_extraction_recipe_id = e.extraction_recipe_id
          )
        """,
        {"document_id": document_id, "extraction_recipe_id": source_extraction_recipe_id},
    )
    _prune_revisions(connection, recipe_id, keep=(revision_no, base_revision_no))
    return revision_no


def _served_extraction_ids(connection: OracleConnectionProtocol, recipe_id: str) -> set[str]:
    rows = _fetch_all(connection, SERVED_EXTRACTIONS_OF_RECIPE_SQL, {"recipe_id": recipe_id})
    return {str(_summary_row(row)["extraction_recipe_id"]) for row in rows}


def _prune_revisions(
    connection: OracleConnectionProtocol, recipe_id: str, *, keep: tuple[int, ...]
) -> None:
    """上限を超えた古い版を消す（今の版・基の解析の版・検索に出している版は残す。番号は振り直さない）。

    親の版の番号は番号だけを持ち、FK は無いので、親の版を消しても子の版は残る。
    """
    rows = [
        _summary_row(row)
        for row in _fetch_all(
            connection,
            f"""
            SELECT revision_no, extraction_recipe_id FROM {REVISIONS_TABLE}
            WHERE recipe_id = :recipe_id
            """,
            {"recipe_id": recipe_id},
        )
    ]
    served = _served_extraction_ids(connection, recipe_id)
    serving = (
        _int_value(row["revision_no"]) for row in rows if row["extraction_recipe_id"] in served
    )
    for revision_no in versions_to_prune(
        (_int_value(row["revision_no"]) for row in rows),
        keep=(*keep, *serving),
        limit=max_extraction_revisions(),
    ):
        _execute(
            connection,
            f"""
            DELETE FROM {REVISIONS_TABLE}
            WHERE recipe_id = :recipe_id AND revision_no = :revision_no
            """,
            {"recipe_id": recipe_id, "revision_no": revision_no},
        )


def select_revision_for_restore(
    connection: OracleConnectionProtocol, recipe_id: str, revision_no: int
) -> dict[str, object] | None:
    """復元する版の本文と基の情報（レシピの行を押さえている transaction の中で読む）。"""
    row = _fetch_one(
        connection, GET_REVISION_SQL, {"recipe_id": recipe_id, "revision_no": revision_no}
    )
    return revision_row(row) if row is not None else None


def revision_row(row: Mapping[str, object]) -> dict[str, object]:
    """版の行の型をそろえる（``extraction_json`` は読んだときだけある）。"""
    normalized = _summary_row(row)
    normalized["revision_no"] = _int_value(normalized["revision_no"])
    normalized["base_revision_no"] = _int_value(normalized["base_revision_no"])
    normalized["parent_revision_no"] = _optional_int(normalized.get("parent_revision_no"))
    normalized["restored_from_revision_no"] = _optional_int(
        normalized.get("restored_from_revision_no")
    )
    normalized["changed_element_count"] = _int_value(normalized.get("changed_element_count"))
    normalized["extraction_recipe_id"] = _optional_str(normalized.get("extraction_recipe_id"))
    normalized["parse_extraction_recipe_id"] = _optional_str(
        normalized.get("parse_extraction_recipe_id")
    )
    if "extraction_json" in normalized:
        normalized["extraction_json"] = _json_loads(normalized["extraction_json"])
    return normalized


__all__ = [
    "GET_REVISION_SQL",
    "LIST_REVISIONS_SQL",
    "REVISIONS_TABLE",
    "SERVED_EXTRACTIONS_OF_RECIPE_SQL",
    "ExtractionRevisionKind",
    "commit_extraction_revision",
    "latest_revision",
    "max_extraction_revisions",
    "oracle_extraction_revision_schema_sql",
    "revision_row",
    "select_revision_for_restore",
]
