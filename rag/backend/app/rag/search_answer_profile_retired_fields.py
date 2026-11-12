"""検索・回答プロファイルと KB の保存値から、定義から消した欄を取り除く migration の処理(#4784)。

プロファイルの ``query`` は適用する欄だけを持ち、定義に無い欄を検証エラーにする。保存値に消した欄が
残ると、プロファイル全体の復元が失敗して空の設定へ縮退するので、``query`` には残す欄だけを、
``language_pack`` には検索と回答で読む節だけを残す。KB の ``retrieval_config`` の ``query`` は
どの処理も読まないので消す。値の読み替えはしない。

残す欄と節の一覧は、この migration を足した時点の定義の写し(migration の SQL に書いて checksum に
入れる)。後で定義が変わっても、ここは変えない。

適用の前の確認(読み取りだけの dry-run)は
``python -m app.rag.search_answer_profile_retired_fields``。行の ID と、消える欄・節の名前だけを出す
(値は出さない)。
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


_TARGETS: tuple[tuple[str, str, str, Callable[[Mapping[str, Any]], dict[str, Any] | None]], ...] = (
    (PROFILE_TABLE, "search_answer_profile_id", "profile_config", without_retired_profile_fields),
    (KNOWLEDGE_BASE_TABLE, "knowledge_base_id", "retrieval_config", without_knowledge_base_query),
)


def _pending(
    connection: Any, *, for_update: bool
) -> list[tuple[str, str, str, Any, dict[str, Any], dict[str, Any]]]:
    """直す行の (表, ID の列, JSON の列, ID, 今の値, 直した値)。表が無ければ飛ばす。"""
    from app.clients.oracle import _fetch_all, _json_loads
    from app.rag.language_pack_retired_fields import _table_exists

    pending = []
    for table, id_column, json_column, strip in _TARGETS:
        if not _table_exists(connection, table):
            continue
        for row in _fetch_all(
            connection,
            f"SELECT {id_column}, {json_column} FROM {table} "  # nosec B608 - 固定の表と列の名前
            f"WHERE {json_column} IS NOT NULL" + (" FOR UPDATE" if for_update else ""),
            {},
        ):
            current = _json_loads(row[json_column])
            stripped = strip(current)
            if stripped is not None:
                pending.append((table, id_column, json_column, row[id_column], current, stripped))
    return pending


def removed_names(current: Mapping[str, Any], stripped: Mapping[str, Any]) -> list[str]:
    """消える key の名前(``query.<欄>``・``language_pack.<節>``・``query``)。値は含めない。"""
    names: list[str] = []
    for key, value in current.items():
        if key not in stripped:
            names.append(key)
        elif value != stripped[key]:
            names.extend(f"{key}.{name}" for name in value.keys() - stripped[key].keys())
    return sorted(names)


def remove_retired_search_answer_profile_fields(connection: Any) -> None:
    """プロファイルと KB の保存値から、定義から消した欄を除く。"""
    from app.clients.oracle import _execute, _json_bind, _json_input_sizes

    for table, id_column, json_column, row_id, _current, stripped in _pending(
        connection, for_update=True
    ):
        _execute(
            connection,
            f"UPDATE {table} SET {json_column}=:config WHERE {id_column}=:id",  # nosec B608
            {"id": row_id, "config": _json_bind(stripped)},
            input_sizes=_json_input_sizes("config"),
        )
    connection.commit()


if __name__ == "__main__":  # pragma: no cover - 読み取りだけの dry-run
    import json

    import oracledb
    from pr_backend_core.oracle_dsn import enable_cli_connect_mode

    from app.clients.oracle import shared_pool_connect_kwargs

    enable_cli_connect_mode()
    with oracledb.connect(**shared_pool_connect_kwargs()) as _connection:
        for _table, _, _, _row_id, _current, _stripped in _pending(_connection, for_update=False):
            print(
                json.dumps(
                    {"table": _table, "id": _row_id, "removed": removed_names(_current, _stripped)},
                    ensure_ascii=False,
                )
            )
