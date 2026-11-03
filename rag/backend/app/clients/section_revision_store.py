"""人が修正した章節の版（#4171）の Oracle の表と SQL。

- ``rag_document_section_revisions``: 文書ごとの不変の版。保存・復元・抽出結果への戻しのたびに
  行を足し、行を書き換えない（版の数の上限を超えた古い行を消すだけ）。文書の削除で消える
  （``ON DELETE CASCADE``）。
- ``rag_document_sections`` は今の修正の 1 行のまま（読み手を変えない）。``revision`` は今の版の
  番号で、同時保存の衝突の検出に使う。

人の修正が無い状態（抽出の章節）から保存するときは、その抽出の章節も版にする（最初の修正の差分を
見られるようにする。修正しない文書には版を持たない）。抽出結果へ戻す操作は、戻した先の抽出の章節を
内容にした版にする。履歴の表より前に保存された修正は、次の変更のときに基の記録なしの版にする。

各操作は文書の行を ``FOR UPDATE`` で押さえるので、同じ文書の版の番号は直列に増える（修正の行がまだ
無い最初の保存どうしも衝突として検出する）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from pr_backend_core.version_retention import versions_to_prune

from app.clients.oracle import (
    DocumentSectionsConflictError,
    OracleConnectionProtocol,
    _execute,
    _fetch_all,
    _fetch_one,
    _int_value,
    _json_bind,
    _json_input_sizes,
    _json_loads,
    _optional_int,
    _optional_str,
)
from app.config import get_settings
from app.rag.document_sections import stored_sections_payload
from app.rag.section_revisions import SectionExtractionBase, section_changes

REVISIONS_TABLE = "rag_document_section_revisions"

_SUMMARY_COLUMNS = """revision_no, parent_revision_no, restored_from_revision_no, revision_kind,
       base_extraction_recipe_id, base_outline_sha256, changed_section_count, created_at"""

LIST_SECTION_REVISIONS_SQL = f"""
SELECT {_SUMMARY_COLUMNS}
FROM {REVISIONS_TABLE}
WHERE document_id = :document_id
ORDER BY revision_no DESC
"""

GET_SECTION_REVISION_SQL = f"""
SELECT {_SUMMARY_COLUMNS}, sections_json
FROM {REVISIONS_TABLE}
WHERE document_id = :document_id AND revision_no = :revision_no
"""


def oracle_section_revision_schema_sql() -> str:
    return f"""
CREATE TABLE {REVISIONS_TABLE} (
    document_id                VARCHAR2(64) NOT NULL,
    revision_no                NUMBER(19) NOT NULL,
    parent_revision_no         NUMBER(19),
    restored_from_revision_no  NUMBER(19),
    revision_kind              VARCHAR2(16) NOT NULL,
    base_extraction_recipe_id  VARCHAR2(64),
    base_outline_sha256        VARCHAR2(64),
    changed_section_count      NUMBER(10) DEFAULT 0 NOT NULL,
    sections_json              JSON NOT NULL,
    created_at                 TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_doc_sec_rev_pk PRIMARY KEY (document_id, revision_no),
    CONSTRAINT rag_doc_sec_rev_document_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_doc_sec_rev_kind_ck
        CHECK (revision_kind IN ('EXTRACTED', 'EDITED', 'RESTORED', 'RESET'))
);
""".strip()


def max_section_revisions() -> int:
    """文書ごとに残す版の数（``RAG_SECTION_MAX_REVISIONS``）。"""
    return max(1, int(get_settings().rag_section_max_revisions))


def section_revision_row(row: Mapping[str, object]) -> dict[str, object]:
    """版の行の型をそろえる（``sections_json`` は読んだときだけある）。"""
    normalized = dict(row)
    normalized["revision_no"] = _int_value(normalized["revision_no"])
    normalized["parent_revision_no"] = _optional_int(normalized.get("parent_revision_no"))
    normalized["restored_from_revision_no"] = _optional_int(
        normalized.get("restored_from_revision_no")
    )
    normalized["changed_section_count"] = _int_value(normalized.get("changed_section_count"))
    normalized["base_extraction_recipe_id"] = _optional_str(
        normalized.get("base_extraction_recipe_id")
    )
    normalized["base_outline_sha256"] = _optional_str(normalized.get("base_outline_sha256"))
    if "sections_json" in normalized:
        normalized["sections_json"] = _json_loads(normalized["sections_json"])
    return normalized


def stored_payload(raw: object) -> dict[str, object]:
    """保存値を版の内容の形にする（古い形の一覧も ``{sections, removed_source_ids}`` にする）。"""
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    return {"sections": raw if isinstance(raw, list) else [], "removed_source_ids": []}


def _lock_document(connection: OracleConnectionProtocol, document_id: str) -> None:
    row = _fetch_one(
        connection,
        "SELECT document_id FROM rag_documents WHERE document_id = :document_id FOR UPDATE",
        {"document_id": document_id},
    )
    if row is None:
        raise DocumentSectionsConflictError(document_id)


def _current_row(
    connection: OracleConnectionProtocol, document_id: str
) -> dict[str, object] | None:
    row = _fetch_one(
        connection,
        """
        SELECT sections_json, revision FROM rag_document_sections
        WHERE document_id = :document_id
        """,
        {"document_id": document_id},
    )
    if row is None:
        return None
    raw = row["sections_json"]
    # 古い形（章節の一覧）も読む（#721）。
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return {
        "sections_json": decoded if isinstance(decoded, list) else _json_loads(decoded),
        "revision": _int_value(row["revision"]),
    }


def _latest_revision(
    connection: OracleConnectionProtocol, document_id: str
) -> dict[str, object] | None:
    row = _fetch_one(
        connection,
        f"""
        SELECT {_SUMMARY_COLUMNS}
        FROM {REVISIONS_TABLE}
        WHERE document_id = :document_id
        ORDER BY revision_no DESC
        FETCH FIRST 1 ROWS ONLY
        """,
        {"document_id": document_id},
    )
    return section_revision_row(row) if row is not None else None


def _insert_revision(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    revision_no: int,
    parent_revision_no: int | None,
    restored_from_revision_no: int | None,
    kind: str,
    base_extraction_recipe_id: str | None,
    base_outline_sha256: str | None,
    changed_section_count: int,
    payload: Mapping[str, object],
) -> None:
    _execute(
        connection,
        f"""
        INSERT INTO {REVISIONS_TABLE} (
            document_id, revision_no, parent_revision_no, restored_from_revision_no, revision_kind,
            base_extraction_recipe_id, base_outline_sha256, changed_section_count, sections_json
        ) VALUES (
            :document_id, :revision_no, :parent_revision_no, :restored_from_revision_no,
            :revision_kind, :base_extraction_recipe_id, :base_outline_sha256,
            :changed_section_count, :sections_json
        )
        """,
        {
            "document_id": document_id,
            "revision_no": revision_no,
            "parent_revision_no": parent_revision_no,
            "restored_from_revision_no": restored_from_revision_no,
            "revision_kind": kind,
            "base_extraction_recipe_id": base_extraction_recipe_id,
            "base_outline_sha256": base_outline_sha256,
            "changed_section_count": changed_section_count,
            "sections_json": _json_bind(dict(payload)),
        },
        input_sizes=_json_input_sizes("sections_json"),
    )


def _changed_count(before: object, after: object) -> int:
    return len(
        section_changes(stored_sections_payload(before)[0], stored_sections_payload(after)[0])
    )


def _same_content(before: object, after: object) -> bool:
    before_sections, before_removed = stored_sections_payload(before)
    after_sections, after_removed = stored_sections_payload(after)
    return before_removed == after_removed and before_sections == after_sections


def _record_current_state(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    current: Mapping[str, object] | None,
    latest: Mapping[str, object] | None,
    extraction_base: SectionExtractionBase,
) -> int:
    """変更の前の状態の版の番号を返す（版が無ければ記録する）。"""
    latest_no = _int_value(latest["revision_no"]) if latest is not None else 0
    if current is not None:
        revision = _int_value(current["revision"])
        if latest_no == revision:
            return revision
        # 履歴の表より前の修正。基の抽出は分からない。
        captured = max(revision, latest_no + 1)
        _insert_revision(
            connection,
            document_id=document_id,
            revision_no=captured,
            parent_revision_no=latest_no or None,
            restored_from_revision_no=None,
            kind="EDITED",
            base_extraction_recipe_id=None,
            base_outline_sha256=None,
            changed_section_count=0,
            payload=stored_payload(current["sections_json"]),
        )
        return captured
    if (
        latest is not None
        and latest["revision_kind"] in {"EXTRACTED", "RESET"}
        and latest["base_outline_sha256"] == extraction_base.outline_sha256
    ):
        return latest_no
    captured = latest_no + 1
    _insert_revision(
        connection,
        document_id=document_id,
        revision_no=captured,
        parent_revision_no=latest_no or None,
        restored_from_revision_no=None,
        kind="EXTRACTED",
        base_extraction_recipe_id=extraction_base.extraction_recipe_id,
        base_outline_sha256=extraction_base.outline_sha256,
        changed_section_count=0,
        payload=extraction_base.outline,
    )
    return captured


def _write_current_row(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    revision_no: int,
    payload: Mapping[str, object],
    exists: bool,
) -> None:
    _execute(
        connection,
        """
        UPDATE rag_document_sections
        SET sections_json = :sections_json,
            revision = :revision_no,
            updated_at = SYSTIMESTAMP
        WHERE document_id = :document_id
        """
        if exists
        else """
        INSERT INTO rag_document_sections (document_id, sections_json, revision)
        VALUES (:document_id, :sections_json, :revision_no)
        """,
        {
            "document_id": document_id,
            "revision_no": revision_no,
            "sections_json": _json_bind(dict(payload)),
        },
        input_sizes=_json_input_sizes("sections_json"),
    )


def _prune_revisions(connection: OracleConnectionProtocol, document_id: str, *, keep: int) -> None:
    """上限を超えた古い版を消す（今の版は残す。番号は振り直さない）。

    親の版は番号だけを持ち FK は無いので、親の版を消しても子の版は残る。
    """
    rows = _fetch_all(
        connection,
        f"SELECT revision_no FROM {REVISIONS_TABLE} WHERE document_id = :document_id",
        {"document_id": document_id},
    )
    for revision_no in versions_to_prune(
        (_int_value(row["revision_no"]) for row in rows),
        keep=(keep,),
        limit=max_section_revisions(),
    ):
        _execute(
            connection,
            f"""
            DELETE FROM {REVISIONS_TABLE}
            WHERE document_id = :document_id AND revision_no = :revision_no
            """,
            {"document_id": document_id, "revision_no": revision_no},
        )


def commit_section_save(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    sections: Mapping[str, object],
    base_revision: int | None,
    extraction_base: SectionExtractionBase,
) -> int:
    """保存を新しい版にし、今の版の番号を返す。

    ``base_revision`` は読み込んだときの版（修正が無ければ None）。今の版と違えば
    ``DocumentSectionsConflictError``。内容も基の抽出も変わらない保存は版を作らない（内容が同じでも
    基の抽出が変わっていれば版を作る。修正を今の抽出で確かめたという記録になる）。
    """
    _lock_document(connection, document_id)
    current = _current_row(connection, document_id)
    current_no = _int_value(current["revision"]) if current is not None else None
    if current_no != base_revision:
        raise DocumentSectionsConflictError(document_id)
    latest = _latest_revision(connection, document_id)
    if (
        current is not None
        and latest is not None
        and latest["revision_no"] == current_no
        and latest["base_extraction_recipe_id"] == extraction_base.extraction_recipe_id
        and latest["base_outline_sha256"] == extraction_base.outline_sha256
        and _same_content(current["sections_json"], sections)
    ):
        return _int_value(current_no)
    parent = _record_current_state(
        connection,
        document_id=document_id,
        current=current,
        latest=latest,
        extraction_base=extraction_base,
    )
    revision_no = parent + 1
    _insert_revision(
        connection,
        document_id=document_id,
        revision_no=revision_no,
        parent_revision_no=parent,
        restored_from_revision_no=None,
        kind="EDITED",
        base_extraction_recipe_id=extraction_base.extraction_recipe_id,
        base_outline_sha256=extraction_base.outline_sha256,
        changed_section_count=_changed_count(
            current["sections_json"] if current is not None else extraction_base.outline, sections
        ),
        payload=sections,
    )
    _write_current_row(
        connection,
        document_id=document_id,
        revision_no=revision_no,
        payload=sections,
        exists=current is not None,
    )
    _prune_revisions(connection, document_id, keep=revision_no)
    return revision_no


def commit_section_restore(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    revision_no: int,
    expected_revision: int | None,
    extraction_base: SectionExtractionBase,
) -> int:
    """指定の版の内容を新しい版として今の修正にし、新しい版の番号を返す。

    基の抽出は元の版のものを引き継ぐ（内容を作った前提は変わらないので、今の抽出と違えば画面に出る）。
    過去の版の行は変えない。今の版が ``expected_revision`` と違う・指定の版がすでに今の版なら
    ``DocumentSectionsConflictError``、指定の版が無ければ ``KeyError``。
    """
    _lock_document(connection, document_id)
    current = _current_row(connection, document_id)
    current_no = _int_value(current["revision"]) if current is not None else None
    if current_no != expected_revision:
        raise DocumentSectionsConflictError(document_id)
    target = _fetch_one(
        connection,
        GET_SECTION_REVISION_SQL,
        {"document_id": document_id, "revision_no": revision_no},
    )
    if target is None:
        raise KeyError(revision_no)
    restored = section_revision_row(target)
    if restored["revision_no"] == current_no:
        raise DocumentSectionsConflictError(document_id)
    payload = stored_payload(restored["sections_json"])
    parent = _record_current_state(
        connection,
        document_id=document_id,
        current=current,
        latest=_latest_revision(connection, document_id),
        extraction_base=extraction_base,
    )
    new_revision_no = parent + 1
    _insert_revision(
        connection,
        document_id=document_id,
        revision_no=new_revision_no,
        parent_revision_no=parent,
        restored_from_revision_no=revision_no,
        kind="RESTORED",
        base_extraction_recipe_id=_optional_str(restored["base_extraction_recipe_id"]),
        base_outline_sha256=_optional_str(restored["base_outline_sha256"]),
        changed_section_count=_changed_count(
            current["sections_json"] if current is not None else extraction_base.outline, payload
        ),
        payload=payload,
    )
    _write_current_row(
        connection,
        document_id=document_id,
        revision_no=new_revision_no,
        payload=payload,
        exists=current is not None,
    )
    _prune_revisions(connection, document_id, keep=new_revision_no)
    return new_revision_no


def commit_section_reset(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    extraction_base: SectionExtractionBase,
) -> None:
    """人の修正を今の抽出の章節へ戻し、戻したことを版にする（修正が無ければ何もしない）。"""
    _lock_document(connection, document_id)
    current = _current_row(connection, document_id)
    if current is None:
        return
    parent = _record_current_state(
        connection,
        document_id=document_id,
        current=current,
        latest=_latest_revision(connection, document_id),
        extraction_base=extraction_base,
    )
    revision_no = parent + 1
    _insert_revision(
        connection,
        document_id=document_id,
        revision_no=revision_no,
        parent_revision_no=parent,
        restored_from_revision_no=None,
        kind="RESET",
        base_extraction_recipe_id=extraction_base.extraction_recipe_id,
        base_outline_sha256=extraction_base.outline_sha256,
        changed_section_count=_changed_count(current["sections_json"], extraction_base.outline),
        payload=extraction_base.outline,
    )
    _execute(
        connection,
        "DELETE FROM rag_document_sections WHERE document_id = :document_id",
        {"document_id": document_id},
    )
    _prune_revisions(connection, document_id, keep=revision_no)


__all__ = [
    "GET_SECTION_REVISION_SQL",
    "LIST_SECTION_REVISIONS_SQL",
    "REVISIONS_TABLE",
    "commit_section_reset",
    "commit_section_restore",
    "commit_section_save",
    "max_section_revisions",
    "oracle_section_revision_schema_sql",
    "section_revision_row",
    "stored_payload",
]
