"""言語資源の保存データから、廃止した項目を取り除く migration の処理(#4666・#4714・#4668)。

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
# 動いていない語の判定を消して、どの処理も読まなくなった項目と節(#4668)。#4714 の migration は上の
# 一覧を SQL に書いて checksum にしているので、上の一覧は変えずに別の一覧にする。
RETIRED_UNREAD_SEARCH_TERM_FIELDS = frozenset({"feedback_split_words", "feedback_strip_suffixes"})
RETIRED_UNREAD_EVIDENCE_CUE_FIELDS = frozenset(
    {
        "link_verbs",
        "link_negations",
        "navigation_words",
        "navigation_evidence_words",
        "screen_place_words",
        "place_particles",
        "entry_action_words",
    }
)
RETIRED_UNREAD_ANSWER_LABEL_FIELDS = frozenset(
    {
        "condition_template",
        "condition_suffix",
        "condition_link",
        "predicate_endings",
    }
)
RETIRED_UNREAD_ANSWER_CHECK_CUE_FIELDS = frozenset(
    {
        "unexplained_words",
        "condition_sentence_endings",
        "condition_clause_nouns",
        "condition_time_nouns",
        "condition_time_followers",
        "condition_strip_particles",
        "condition_trailing_particles",
        "condition_range_particles",
        "condition_topic_particles",
        "condition_adverb_kanji",
        "condition_adverb_particles",
        "deletion_words",
    }
)
RETIRED_UNREAD_SECTIONS = frozenset({"operation_audit_cues"})
# 根拠の語の印と本文の語の書き換えをやめて、読み手が無くなった項目(#4668)。上の migration の一覧は
# checksum に入っているので変えず、別の一覧にする。
RETIRED_EVIDENCE_TAG_QUESTION_CUE_FIELDS = frozenset({"mixed_number_notation_cues"})
RETIRED_EVIDENCE_TAG_EVIDENCE_CUE_FIELDS = frozenset({"aggregate_value_words", "detail_words"})
RETIRED_EVIDENCE_TAG_ANSWER_CHECK_CUE_FIELDS = frozenset(
    {
        "completion_verbs",
        "closing_quote_chars",
        "button_words",
        "press_words",
        "completion_connectors",
        "completion_words",
        "constraint_words",
        "condition_preceders",
        "condition_nouns",
        "condition_followers",
        "process_mode_words",
        "mode_topic_particles",
        "mode_condition_words",
    }
)
RETIRED_EVIDENCE_TAG_GROUNDING_CUE_FIELDS = frozenset({"amount_units"})
RETIRED_EVIDENCE_TAG_FIELDS = (
    RETIRED_EVIDENCE_TAG_QUESTION_CUE_FIELDS
    | RETIRED_EVIDENCE_TAG_EVIDENCE_CUE_FIELDS
    | RETIRED_EVIDENCE_TAG_ANSWER_CHECK_CUE_FIELDS
    | RETIRED_EVIDENCE_TAG_GROUNDING_CUE_FIELDS
)
RETIRED_UNREAD_FIELDS = (
    RETIRED_UNREAD_SEARCH_TERM_FIELDS
    | RETIRED_UNREAD_EVIDENCE_CUE_FIELDS
    | RETIRED_UNREAD_ANSWER_LABEL_FIELDS
    | RETIRED_UNREAD_ANSWER_CHECK_CUE_FIELDS
)
# 質問の種類の語の判定と根拠の順位づけの語をやめて、読み手が無くなった項目と節(#4668)。
# 上の migration の一覧は checksum に入っているので変えず、別の一覧にする。
RETIRED_QUESTION_WORD_QUESTION_CUE_FIELDS = frozenset(
    {
        "object_suffixes",
        "document_kind_chars",
        "existential_locatives",
        "existential_particles",
        "existential_predicates",
        "requester_terms",
        "requester_sides",
        "requester_particles",
        "requester_verbs",
        "past_markers",
        "contrast_particles",
        "change_words",
        "changed_field_particles",
        "changed_field_endings",
        "hypothesis_cues",
        "done_marks",
        "recipient_subject_words",
        "recipient_action_words",
        "recipient_list_words",
        "list_words",
        "list_output_words",
        "choice_words",
        "absence_words",
        "count_cues",
        "member_cues",
        "basis_cues",
        "change_verbs",
        "change_verb_endings",
        "change_wish_cues",
        "method_cues",
        "procedure_cues",
        "requested_action_words",
        "output_format_words",
        "explicit_condition_cues",
        "aggregation_words",
        "member_subject_words",
        "cause_cues",
        "cause_nouns",
        "cause_noun_question_endings",
        "cause_noun_question_leads",
        "generic_target_words",
        "object_particles",
        "manner_words",
        "action_target_verbs",
        "action_request_endings",
        "error_words",
        "error_connectors",
        "error_clause_particles",
        "error_clause_particle_exceptions",
        "question_final_particles",
        "request_cues",
        "facet_method_cues",
        "facet_impact_cues",
        "facet_output_cues",
        "facet_necessity_cues",
        "facet_withdrawal_objects",
        "facet_withdrawal_words",
        "topic_particles",
        "parallel_connectors",
        "target_particles",
        "split_operation_words",
        "chain_connectors",
        "count_query_words",
        "list_query_words",
        "hidden_field_words",
        "hidden_field_states",
        "fetch_failure_cues",
        "grouping_extra_words",
        "grouping_suffixes",
        "filter_words",
        "update_words",
        "software_version_words",
        "answer_instruction_cues",
        "answer_instruction_endings",
    }
)
RETIRED_QUESTION_WORD_EVIDENCE_CUE_FIELDS = frozenset(
    {
        "operation_section_words",
        "focus_particles",
        "target_stop_terms",
        "step_number_closers",
        "operation_text_cues",
        "default_question_cues",
        "default_text_cues",
        "list_text_cues",
        "list_action_words",
        "list_execute_words",
        "step_condition_cues",
        "step_action_words",
    }
)
RETIRED_QUESTION_WORD_GROUNDING_CUE_FIELDS = frozenset({"native_operation_words"})
RETIRED_QUESTION_WORD_ANSWER_LABEL_FIELDS = frozenset({"crag_definition_aspect_template"})
RETIRED_QUESTION_WORD_SECTIONS = frozenset({"definition_cues", "enumeration_cues"})
RETIRED_QUESTION_WORD_FIELDS = (
    RETIRED_QUESTION_WORD_QUESTION_CUE_FIELDS
    | RETIRED_QUESTION_WORD_EVIDENCE_CUE_FIELDS
    | RETIRED_QUESTION_WORD_GROUNDING_CUE_FIELDS
    | RETIRED_QUESTION_WORD_ANSWER_LABEL_FIELDS
    | RETIRED_QUESTION_WORD_SECTIONS
)
_RETIRED_BY_SECTION = {
    "question_cues": (
        RETIRED_QUESTION_CUE_FIELDS
        | RETIRED_EVIDENCE_TAG_QUESTION_CUE_FIELDS
        | RETIRED_QUESTION_WORD_QUESTION_CUE_FIELDS
    ),
    "answer_labels": (
        RETIRED_ANSWER_LABEL_FIELDS
        | RETIRED_UNREAD_ANSWER_LABEL_FIELDS
        | RETIRED_QUESTION_WORD_ANSWER_LABEL_FIELDS
    ),
    "answer_check_cues": (
        RETIRED_ANSWER_CHECK_CUE_FIELDS
        | RETIRED_UNREAD_ANSWER_CHECK_CUE_FIELDS
        | RETIRED_EVIDENCE_TAG_ANSWER_CHECK_CUE_FIELDS
    ),
    "grounding_cues": (
        RETIRED_EVIDENCE_TAG_GROUNDING_CUE_FIELDS | RETIRED_QUESTION_WORD_GROUNDING_CUE_FIELDS
    ),
    "search_terms": RETIRED_UNREAD_SEARCH_TERM_FIELDS,
    "evidence_cues": (
        RETIRED_UNREAD_EVIDENCE_CUE_FIELDS
        | RETIRED_EVIDENCE_TAG_EVIDENCE_CUE_FIELDS
        | RETIRED_QUESTION_WORD_EVIDENCE_CUE_FIELDS
    ),
}
_ALL_RETIRED_SECTIONS = RETIRED_SECTIONS | RETIRED_UNREAD_SECTIONS | RETIRED_QUESTION_WORD_SECTIONS
PROFILE_TABLE = "rag_search_answer_profiles"


def without_retired_fields(pack: Mapping[str, Any]) -> dict[str, Any] | None:
    """廃止した項目を除いた写しを返す。除くものが無ければ None(同じ行を何回流しても変えない)。"""
    stripped: dict[str, Any] = {}
    for section, retired in _RETIRED_BY_SECTION.items():
        fields = pack.get(section)
        if isinstance(fields, Mapping) and retired & fields.keys():
            stripped[section] = {k: v for k, v in fields.items() if k not in retired}
    dropped = _ALL_RETIRED_SECTIONS & pack.keys()
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
