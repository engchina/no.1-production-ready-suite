"""検索・回答プロファイルと KB の保存値から、定義から消した欄を取り除く migration の処理(#4784)。

プロファイルの ``query`` は適用する欄だけを持ち、定義に無い欄を検証エラーにする。保存値に消した欄が
残ると、プロファイル全体の復元が失敗して空の設定へ縮退するので、``query`` には残す欄だけを、
``language_pack`` には検索と回答で読む節だけを残す。KB の ``retrieval_config`` の ``query`` は
どの処理も読まないので消す。値の読み替えはしない。

残す欄と節の一覧は、この migration を足した時点の定義の写し(migration の SQL に書いて checksum に
入れる)。後で定義が変わっても、ここは変えない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

KEPT_QUERY_FIELDS = frozenset({"max_anchors_per_document", "support_guide_enabled"})
KEPT_LANGUAGE_PACK_SECTIONS = frozenset(
    {
        "search_terms",
        "question_cues",
        "evidence_cues",
        "answer_labels",
        "answer_check_cues",
        "grounding_cues",
    }
)
PROFILE_TABLE = "rag_search_answer_profiles"
KNOWLEDGE_BASE_TABLE = "rag_knowledge_bases"


def _kept(value: object, kept: frozenset[str]) -> dict[str, Any] | None:
    """kept に無い key を除いた写し。除くものが無ければ None。"""
    if not isinstance(value, Mapping) or not value.keys() - kept:
        return None
    return {key: item for key, item in value.items() if key in kept}


def without_retired_profile_fields(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """消した欄と節を除いた写し。除くものが無ければ None(同じ行を何回流しても変えない)。"""
    changed = {
        key: kept
        for key, names in (
            ("query", KEPT_QUERY_FIELDS),
            ("language_pack", KEPT_LANGUAGE_PACK_SECTIONS),
        )
        if (kept := _kept(config.get(key), names)) is not None
    }
    return {**config, **changed} if changed else None


def without_knowledge_base_query(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """``query`` を除いた写し。``query`` が無ければ None。"""
    if "query" not in config:
        return None
    return {key: item for key, item in config.items() if key != "query"}


def _strip(
    connection: Any,
    table: str,
    id_column: str,
    json_column: str,
    strip: Callable[[Mapping[str, Any]], dict[str, Any] | None],
) -> None:
    from app.clients.oracle import _execute, _fetch_all, _json_bind, _json_input_sizes, _json_loads

    for row in _fetch_all(
        connection,
        f"SELECT {id_column}, {json_column} FROM {table} "  # nosec B608 - 固定の表と列の名前
        f"WHERE {json_column} IS NOT NULL FOR UPDATE",
        {},
    ):
        stripped = strip(_json_loads(row[json_column]))
        if stripped is None:
            continue
        _execute(
            connection,
            f"UPDATE {table} SET {json_column}=:config WHERE {id_column}=:id",  # nosec B608
            {"id": row[id_column], "config": _json_bind(stripped)},
            input_sizes=_json_input_sizes("config"),
        )


def remove_retired_search_answer_profile_fields(connection: Any) -> None:
    """プロファイルと KB の保存値から、定義から消した欄を除く。表ごとに 1 transaction。"""
    from app.rag.language_pack_retired_fields import _table_exists

    for table, id_column, json_column, strip in (
        (
            PROFILE_TABLE,
            "search_answer_profile_id",
            "profile_config",
            without_retired_profile_fields,
        ),
        (
            KNOWLEDGE_BASE_TABLE,
            "knowledge_base_id",
            "retrieval_config",
            without_knowledge_base_query,
        ),
    ):
        if _table_exists(connection, table):
            _strip(connection, table, id_column, json_column, strip)
            connection.commit()
