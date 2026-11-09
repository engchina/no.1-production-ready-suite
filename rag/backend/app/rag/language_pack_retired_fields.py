"""言語資源の保存データから、廃止した項目を取り除く migration の処理(#4666・#4714)。

公開版と下書きは全項目のスナップショットで、`build_pack` は余分な項目を拒否する。公開版の
`content_hash` は全項目から計算するので、項目を消したら計算し直す必要があり、SQL だけでは書けない。
`ingest_hash` は `ingest=False` の項目を含まないので変わらない。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from rag_parser_core.language import build_pack

from app.clients import language_pack_release_store as release_store

logger = logging.getLogger(__name__)

RETIRED_QUESTION_CUE_FIELDS = frozenset(
    {
        "wish_endings",
        "tried_action_cues",
        "done_state_verbs",
        "observed_state_cues",
        "passive_state_endings",
        "means_cues",
        "withdrawn_cues",
        "uncertainty_cues",
        "clause_split_chars",
        "contrast_clause_endings",
        "condition_cues",
    }
)
# 回答の生成後の確認をやめて、どの処理も読まなくなった項目と節(#4714)。
RETIRED_ANSWER_LABEL_FIELDS = frozenset(
    {
        "removed_section",
        "removed_section_note",
        "marked_section",
        "marked_section_note",
        "unsourced_section",
        "unsourced_section_note",
        "marked_rules_section",
        "marked_rules_section_note",
        "passage_note_template",
        "answer_warning_template",
        "passage_reason_labels",
        "off_goal_gap",
        "subject_unconfirmed_gap",
        "cause_gap",
        "missing_request_template",
    }
)
RETIRED_ANSWER_CHECK_CUE_FIELDS = frozenset(
    {
        "completion_negations",
        "heading_screen_words",
        "field_label_followers",
        "guarantee_verbs",
        "guarantee_endings",
        "guarantee_exceptions",
        "match_claims",
        "speculation_words",
        "report_suffixes",
        "screen_object_suffixes",
        "proviso_words",
        "navigation_residue_words",
        "version_units",
        "abolish_words",
        "abolish_particles",
        "abolished_forms",
        "version_question_cues",
        "quote_operation_words",
        "evidence_id_labels",
        "output_artifact_words",
        "output_field_words",
        "heading_bracket_pairs",
    }
)
RETIRED_SECTIONS = frozenset({"guide_check_cues"})
_RETIRED_BY_SECTION = {
    "question_cues": RETIRED_QUESTION_CUE_FIELDS,
    "answer_labels": RETIRED_ANSWER_LABEL_FIELDS,
    "answer_check_cues": RETIRED_ANSWER_CHECK_CUE_FIELDS,
}
PROFILE_TABLE = "rag_search_answer_profiles"


def without_retired_fields(pack: Mapping[str, Any]) -> dict[str, Any] | None:
    """廃止した項目を除いた写しを返す。除くものが無ければ None(同じ行を何回流しても変えない)。"""
    stripped: dict[str, Any] = {}
    for section, retired in _RETIRED_BY_SECTION.items():
        fields = pack.get(section)
        if isinstance(fields, Mapping) and retired & fields.keys():
            stripped[section] = {k: v for k, v in fields.items() if k not in retired}
    dropped = RETIRED_SECTIONS & pack.keys()
    if not stripped and not dropped:
        return None
    return {k: v for k, v in {**pack, **stripped}.items() if k not in dropped}


def _table_exists(connection: Any, table: str) -> bool:
    from app.clients.oracle import _fetch_all

    return bool(
        _fetch_all(
            connection,
            "SELECT 1 AS found FROM user_tables WHERE table_name = :name",
            {"name": table.upper()},
        )
    )


def _strip_releases(connection: Any) -> None:
    from app.clients.oracle import _execute, _fetch_all, _json_bind, _json_input_sizes, _json_loads

    table = release_store.RELEASE_TABLE
    for row in _fetch_all(
        connection, f"SELECT release_id, effective_json FROM {table} FOR UPDATE", {}
    ):
        stripped = without_retired_fields(_json_loads(row["effective_json"]))
        if stripped is None:
            continue
        try:
            content_hash = build_pack(stripped).hash
        except ValueError:
            # 別の項目の欠けで今の契約では読めない版。項目だけを消し、hash は変えない。
            logger.warning(
                "language_pack_retired_fields_release_unreadable",
                extra={"release_id": row["release_id"]},
            )
            _execute(
                connection,
                f"UPDATE {table} SET effective_json=:effective WHERE release_id=:id",
                {"id": row["release_id"], "effective": _json_bind(stripped)},
                input_sizes=_json_input_sizes("effective"),
            )
            continue
        _execute(
            connection,
            f"UPDATE {table} SET effective_json=:effective, content_hash=:content_hash "
            "WHERE release_id=:id",
            {
                "id": row["release_id"],
                "effective": _json_bind(stripped),
                "content_hash": content_hash,
            },
            input_sizes=_json_input_sizes("effective"),
        )


def _strip_draft(connection: Any) -> None:
    from app.clients.oracle import _execute, _fetch_all, _json_bind, _json_input_sizes, _json_loads

    table = release_store.STATE_TABLE
    for row in _fetch_all(
        connection, f"SELECT singleton_id, draft_effective_json FROM {table} FOR UPDATE", {}
    ):
        stripped = without_retired_fields(_json_loads(row["draft_effective_json"]))
        if stripped is not None:
            _execute(
                connection,
                f"UPDATE {table} SET draft_effective_json=:effective WHERE singleton_id=:id",
                {"id": row["singleton_id"], "effective": _json_bind(stripped)},
                input_sizes=_json_input_sizes("effective"),
            )


def _strip_profiles(connection: Any) -> None:
    from app.clients.oracle import _execute, _fetch_all, _json_bind, _json_input_sizes, _json_loads

    for row in _fetch_all(
        connection,
        f"SELECT search_answer_profile_id, profile_config FROM {PROFILE_TABLE} "
        "WHERE JSON_EXISTS(profile_config, '$.language_pack') FOR UPDATE",
        {},
    ):
        config = _json_loads(row["profile_config"])
        overrides = config.get("language_pack")
        stripped = without_retired_fields(overrides) if isinstance(overrides, Mapping) else None
        if stripped is None:
            continue
        # 上書きが無くなった節は残さない(「上書きなし」と同じ保存値にする)。
        stripped = {section: fields for section, fields in stripped.items() if fields}
        _execute(
            connection,
            f"UPDATE {PROFILE_TABLE} SET profile_config=:config WHERE search_answer_profile_id=:id",
            {
                "id": row["search_answer_profile_id"],
                "config": _json_bind({**config, "language_pack": stripped}),
            },
            input_sizes=_json_input_sizes("config"),
        )


def remove_retired_language_pack_fields(connection: Any) -> None:
    """公開版・下書き・プロファイルの上書きから廃止した項目を除く。表ごとに 1 transaction。"""
    for table, strip in (
        (release_store.RELEASE_TABLE, _strip_releases),
        (release_store.STATE_TABLE, _strip_draft),
        (PROFILE_TABLE, _strip_profiles),
    ):
        if _table_exists(connection, table):
            strip(connection)
            connection.commit()
