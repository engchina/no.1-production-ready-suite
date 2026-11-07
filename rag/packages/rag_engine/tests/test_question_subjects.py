"""質問の対象の名前と役割の 2 段の契約と、説明・要約の照合（#4351）。入力はすべて合成、モデルの応答は録画の固定値。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from citation_unit_support import generated_fixture

from rag_engine.config import QUESTION_SUBJECT_ROLE_LLM_PROVIDER, LlmProviderSettings, get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.question_subjects import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_NAMED,
    STATUS_ROLES_UNAVAILABLE,
    SUBJECT_EXTRACTION_PROMPT,
    SUBJECT_ROLE_PROMPT,
    QuestionSubjectError,
    QuestionSubjects,
    SubjectMention,
    absent_question_names,
    extract_question_subjects,
    extraction_model,
    prepare_extraction,
    prepare_roles,
    project_extraction,
    question_sentences,
    trim_enclosing_pairs,
    unconfirmed_question_notices,
)

EXTRACT = "QuestionMentionAnchors"
ROLE = "QuestionEntityRoles"


def mention(question: str, text: str, designation: str = "named", *, nth: int = 0) -> dict[str, Any]:
    """録画応答の 1 提及（原文の写しと、その写しの ``nth`` 番目（0 から）の出現の順番）。"""
    assert text in question
    return {"text": text, "occurrence": nth + 1, "designation": designation, "members": []}


class Recorded:
    """schema の名前ごとの録画応答を返すスタブ。呼び出し（指示・入力・schema の名前）を残す。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def __call__(self, system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        self.calls.append((system, json.loads(prompt), schema.__name__))
        response = self.responses[schema.__name__]
        if isinstance(response, Exception):
            raise response
        return schema.model_validate(response)


def extract(question: str, responses: dict[str, Any]) -> tuple[QuestionSubjects, Recorded]:
    parse = Recorded(responses)
    return extract_question_subjects(question, object(), parse_text=parse), parse


# ---------- 文の区切り ----------


def test_sentences_cover_the_question_and_keep_closers_with_the_sentence() -> None:
    question = "前提です。「確認？」次の文\n最後"
    spans = question_sentences(question)
    assert [question[start:end] for start, end in spans] == ["前提です。", "「確認？」", "次の文\n", "最後"]
    assert spans[0][0] == 0 and spans[-1][1] == len(question)
    assert all(left[1] == right[0] for left, right in zip(spans, spans[1:], strict=False))


# ---------- 抽出の契約 ----------


def test_extraction_input_is_the_question_and_the_schema_asks_for_text_occurrence_and_designation() -> None:
    # 位置はコードが写しから決めるので、文字ごとの位置 ID は渡さない。
    prepared = prepare_extraction("装置ラムダ")
    assert prepared == {"question": "装置ラムダ"}
    schema = extraction_model(prepared).model_json_schema()
    anchor = next(iter(schema["$defs"].values()))
    assert set(anchor["properties"]) == set(anchor["required"]) == {"text", "occurrence", "designation", "members"}
    assert anchor["properties"]["occurrence"] == {
        "description": anchor["properties"]["occurrence"]["description"],
        "minimum": 1,
        "title": "Occurrence",
        "type": "integer",
    }


def _rows(question: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    """位置を確定した行（写しの中に無い collective の名前の数を除く）。"""
    rows, _ = project_extraction(question, raw)
    return rows


def anchor(text: str, occurrence: int = 1) -> dict[str, Any]:
    return {"text": text, "occurrence": occurrence, "designation": "named", "members": []}


@pytest.mark.parametrize(
    ("question", "text", "occurrence", "start"),
    [
        # 写しが 1 つだけ現れるなら、その位置（順番の値は位置を変えない）。
        ("前提の後に装置ラムダの手順は？", "装置ラムダ", 1, 5),
        ("前提の後に装置ラムダの手順は？", "装置ラムダ", 2, 5),
        # 同じ写しが複数現れるなら、occurrence 番目の出現。
        ("装置ラムダと装置ラムダの違いは？", "装置ラムダ", 1, 0),
        ("装置ラムダと装置ラムダの違いは？", "装置ラムダ", 2, 6),
        # 重なる出現も先頭から 1 つずつ数える。
        ("ガガガの手順は？", "ガガ", 2, 1),
    ],
    ids=["unique", "unique-occurrence-ignored", "repeated-first", "repeated-second", "overlapping"],
)
def test_code_resolves_the_position_from_exact_occurrences_of_the_text(question: str, text: str, occurrence: int, start: int) -> None:
    rows = _rows(question, {"mentions": [anchor(text, occurrence)]})
    assert rows == [{"start": start, "end": start + len(text), "text": text, "designation": "named"}]
    assert question[start : start + len(text)] == text


@pytest.mark.parametrize(
    ("question", "text", "occurrence", "code"),
    [
        ("装置ラムダと装置ラムダの違いは？", "装置ラムダ", 3, "occurrence_out_of_range"),
        # 写しが一字でも違えば、近い表記・部分への寄せをせず失敗にする。
        ("装置ラムダの手順は？", "装置ラムタ", 1, "text_not_in_question"),
        ("装置ラムダの手順は？", "装置 ラムダ", 1, "text_not_in_question"),
    ],
    ids=["out-of-range", "absent", "absent-with-space"],
)
def test_text_absent_or_occurrence_out_of_range_fails_without_snapping(question: str, text: str, occurrence: int, code: str) -> None:
    with pytest.raises(QuestionSubjectError) as caught:
        _rows(question, {"mentions": [anchor(text, occurrence)]})
    assert caught.value.code == code
    subjects, parse = extract(question, {EXTRACT: {"mentions": [anchor(text, occurrence)]}})
    assert (subjects.status, subjects.failure, subjects.calls) == (STATUS_FAILED, code, 1)
    assert [name for _, _, name in parse.calls] == [EXTRACT]


def test_two_mentions_resolving_to_the_same_range_fail() -> None:
    with pytest.raises(QuestionSubjectError) as caught:
        _rows("装置ラムダの手順は？", {"mentions": [anchor("装置ラムダ", 1), anchor("装置ラムダ", 2)]})
    assert caught.value.code == "exact_duplicate_range"


def test_occurrence_below_one_is_rejected_by_the_schema() -> None:
    subjects, _ = extract("装置ラムダの手順は？", {EXTRACT: {"mentions": [anchor("装置ラムダ", 0)]}})
    assert (subjects.status, subjects.failure) == (STATUS_FAILED, "ValidationError")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("『装置ラムダ』", "装置ラムダ"),
        ("「装置ラムダ」", "装置ラムダ"),
        ("（装置ラムダ）", "装置ラムダ"),
        ('"装置ラムダ"', "装置ラムダ"),
        ("「『装置ラムダ』」", "装置ラムダ"),
        # 対は Unicode の文字の性質（Ps / Pe・Pi / Pf と文字名）で決める。比較の記号（Sm）は括弧ではない。
        ("«装置ラムダ»", "装置ラムダ"),
        ("“装置ラムダ”", "装置ラムダ"),
        ("<装置ラムダ>", "<装置ラムダ>"),
        # 2 つの名前を一括した提及は、内側に同じ種類の記号があるので外さない。
        ("「装置A」と「装置B」", "「装置A」と「装置B」"),
        # 片側だけの記号・対にならない記号は外さない。
        ("「装置ラムダ", "「装置ラムダ"),
        ("「装置ラムダ』", "「装置ラムダ』"),
    ],
    ids=[
        "double-corner",
        "corner",
        "paren",
        "ascii-quote",
        "nested",
        "guillemet",
        "curly-quote",
        "math-sign-not-trimmed",
        "two-names-not-trimmed",
        "unpaired",
        "mismatched",
    ],
)
def test_enclosing_pairs_are_trimmed_only_when_they_wrap_the_whole_mention(text: str, expected: str) -> None:
    question = f"{text}の手順は？"
    rows = trim_enclosing_pairs(question, [{"start": 0, "end": len(text), "text": text, "designation": "named"}])
    assert [row["text"] for row in rows] == [expected]
    start = question.index(expected)
    assert (rows[0]["start"], rows[0]["end"]) == (start, start + len(expected))


def test_trim_merges_same_range_and_rejects_conflicting_designation() -> None:
    question = "『装置ラムダ』の手順は？"
    inner = {"start": 1, "end": 6, "text": "装置ラムダ", "designation": "named"}
    outer = {"start": 0, "end": 7, "text": "『装置ラムダ』", "designation": "named"}
    assert trim_enclosing_pairs(question, [outer, inner]) == [inner]
    with pytest.raises(QuestionSubjectError) as caught:
        trim_enclosing_pairs(question, [outer, {**inner, "designation": "generic"}])
    assert caught.value.code == "conflicting_designation_after_trim"


def test_repeated_wrapped_mention_is_resolved_by_occurrence_then_trimmed() -> None:
    question = "『装置ラムダ』は停止中です。『装置ラムダ』の再開手順は？"
    subjects, parse = extract(
        question,
        {
            EXTRACT: {"mentions": [mention(question, "『装置ラムダ』", nth=1)]},
            ROLE: {"entities": [{"entity_id": "N1", "roles": ["asked"]}]},
        },
    )
    assert subjects.status == STATUS_COMPLETED
    assert [(m.start, m.end, m.text, m.sentence_id) for m in subjects.mentions] == [(15, 20, "装置ラムダ", "S2")]
    assert parse.calls[1][1]["entities"] == [{"entity_id": "N1", "start": 15, "end": 20, "text": "装置ラムダ", "sentence_id": "S2"}]


def test_wrapped_mention_reaches_the_role_stage_without_brackets() -> None:
    question = "『手順デルタ』の実行条件は？"
    subjects, parse = extract(
        question,
        {EXTRACT: {"mentions": [mention(question, "『手順デルタ』")]}, ROLE: {"entities": [{"entity_id": "N1", "roles": ["asked"]}]}},
    )
    assert subjects.status == STATUS_COMPLETED
    assert [(m.start, m.end, m.text) for m in subjects.mentions] == [(1, 6, "手順デルタ")]
    role_input = parse.calls[1][1]
    assert role_input["entities"] == [{"entity_id": "N1", "start": 1, "end": 6, "text": "手順デルタ", "sentence_id": "S1"}]


# ---------- 役割の契約 ----------


# ---------- 複数の対象をまとめた表記（collective） ----------

RANGE_QUESTION = "部品P1〜P4は点検済みです。部品P5の扱いは？"
RANGE_SOURCE = "部品P1、部品P2、部品P3、部品P4、部品P5は点検の対象である。"


def collective(question: str, text: str, *members: str | tuple[str, int], nth: int = 0) -> dict[str, Any]:
    """録画応答の collective の提及（個々の名前は、提及の写しの中で数えた出現の順番つきの写し）。"""
    assert text in question
    rows = [member if isinstance(member, tuple) else (member, 1) for member in members]
    return {
        "text": text,
        "occurrence": nth + 1,
        "designation": "collective",
        "members": [{"text": member, "occurrence": occurrence} for member, occurrence in rows],
    }


def test_collective_mention_is_not_a_name_and_its_members_are_named_positions() -> None:
    """範囲の表記は 1 つの名前として照合せず、その中に写された個々の名前だけを named の位置にする（#4584）。"""
    question = RANGE_QUESTION
    rows = _rows(question, {"mentions": [collective(question, "部品P1〜P4", "部品P1"), mention(question, "部品P5")]})
    assert [(row["text"], row["designation"], question[row["start"] : row["end"]]) for row in rows] == [
        ("部品P1", "named", "部品P1"),
        ("部品P1〜P4", "collective", "部品P1〜P4"),
        ("部品P5", "named", "部品P5"),
    ]
    subjects, parse = extract(
        question,
        {
            EXTRACT: {"mentions": [collective(question, "部品P1〜P4", "部品P1"), mention(question, "部品P5")]},
            ROLE: {"entities": [{"entity_id": "N1", "roles": ["given"]}, {"entity_id": "N2", "roles": ["asked"]}]},
        },
    )
    assert [entity["text"] for entity in parse.calls[1][1]["entities"]] == ["部品P1", "部品P5"]
    assert [(m.text, m.roles) for m in subjects.mentions] == [("部品P1", ("given",)), ("部品P5", ("asked",))]
    assert subjects.diagnostics() == {
        "status": STATUS_COMPLETED,
        "calls": 2,
        "named_mentions": 2,
        "verified_mentions": 2,
        "asked_mentions": 1,
        "collective_mentions": 1,
        "collective_members": 1,
    }


def test_member_position_is_counted_inside_the_collective_mention() -> None:
    # 個々の名前の出現の順番は、まとめた提及の写しの中で数える（同じ写しが提及の外にあっても位置を取り違えない）。
    question = "部品Pの点検は？部品P・部品Qは済みです。"
    rows = _rows(question, {"mentions": [collective(question, "部品P・部品Q", "部品P", "部品Q")]})
    start = question.index("部品P・部品Q")
    assert [(row["start"], row["text"]) for row in rows if row["designation"] == "named"] == [(start, "部品P"), (start + 4, "部品Q")]


def test_member_also_returned_as_a_named_mention_is_one_position() -> None:
    question = RANGE_QUESTION
    rows = _rows(question, {"mentions": [mention(question, "部品P1"), collective(question, "部品P1〜P4", "部品P1")]})
    assert [(row["text"], row["designation"]) for row in rows] == [("部品P1", "named"), ("部品P1〜P4", "collective")]
    # 単独でも返した名前も、まとめた表記の中の名前として数える（実モデルの応答の形）。
    subjects, _ = extract(
        question,
        {
            EXTRACT: {"mentions": [mention(question, "部品P1"), collective(question, "部品P1〜P4", "部品P1")]},
            ROLE: RuntimeError("synthetic"),
        },
    )
    assert (len(subjects.mentions), subjects.collective_mentions, subjects.collective_members) == (1, 1, 1)


def test_members_not_written_in_the_collective_mention_are_not_used() -> None:
    """範囲を補って展開した名前・提及の外の名前は原文の写しでないので照合に使わず、件数だけを残す（実モデルの応答の形）。"""
    question = RANGE_QUESTION
    expanded = collective(question, "部品P1〜P4", "部品P1", "部品P2", "部品P3", ("部品P4", 4), "部品P5")
    rows, unwritten = project_extraction(question, {"mentions": [expanded, mention(question, "部品P5")]})
    assert ([(row["text"], row["designation"]) for row in rows], unwritten) == (
        [("部品P1", "named"), ("部品P1〜P4", "collective"), ("部品P5", "named")],
        4,
    )
    subjects, _ = extract(question, {EXTRACT: {"mentions": [expanded, mention(question, "部品P5")]}, ROLE: RuntimeError("synthetic")})
    assert [m.text for m in subjects.mentions] == ["部品P1", "部品P5"]
    assert unconfirmed_question_notices(subjects, [RANGE_SOURCE]) == []
    assert subjects.diagnostics() == {
        "status": STATUS_ROLES_UNAVAILABLE,
        "calls": 2,
        "named_mentions": 2,
        "verified_mentions": 2,
        "collective_mentions": 1,
        "collective_members": 1,
        "collective_unwritten_members": 4,
        "failure": "RuntimeError",
    }


def test_collective_without_a_written_member_has_no_named_position() -> None:
    question = RANGE_QUESTION
    subjects, parse = extract(question, {EXTRACT: {"mentions": [collective(question, "P1〜P4")]}})
    assert (subjects.status, subjects.mentions, [name for _, _, name in parse.calls]) == (STATUS_NO_NAMED, (), [EXTRACT])
    assert subjects.diagnostics() == {"status": STATUS_NO_NAMED, "calls": 1, "named_mentions": 0, "collective_mentions": 1}


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (collective(RANGE_QUESTION, "部品P1〜P4", ("P", 3)), "member_occurrence_out_of_range"),
        (collective(RANGE_QUESTION, "部品P1〜P4", "部品P1〜P4"), "member_is_whole_mention"),
        (collective(RANGE_QUESTION, "部品P1〜P4", "部品P1", "部品P1"), "exact_duplicate_range"),
        ({**mention(RANGE_QUESTION, "部品P5"), "members": [{"text": "P5", "occurrence": 1}]}, "members_on_single_mention"),
    ],
    ids=["member-occurrence", "member-whole", "member-duplicate", "members-on-named"],
)
def test_collective_member_contract_violations_fail_without_snapping(row: dict[str, Any], code: str) -> None:
    with pytest.raises(QuestionSubjectError) as caught:
        _rows(RANGE_QUESTION, {"mentions": [row]})
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # 範囲の中の名前が引いた原文に個々にあれば、範囲の表記そのものが原文に無くても伝えない。
        (RANGE_SOURCE, []),
        # 写された個々の名前は照合する（まとめた表記の中でも、資料に無い名前は従来どおり伝える）。
        ("部品P2、部品P5は点検の対象である。", [("unknown", ["部品P1"])]),
    ],
    ids=["members-in-source", "written-member-absent"],
)
def test_collective_mention_verifies_written_members_not_the_range(source: str, expected: list[tuple[str, list[str]]]) -> None:
    question = RANGE_QUESTION
    subjects, _ = extract(
        question,
        {
            EXTRACT: {"mentions": [collective(question, "部品P1〜P4", "部品P1"), mention(question, "部品P5")]},
            ROLE: RuntimeError("synthetic"),
        },
    )
    assert subjects.status == STATUS_ROLES_UNAVAILABLE
    notices = unconfirmed_question_notices(subjects, [source])
    assert [(notice.kind, list(notice.phrases)) for notice in notices] == expected
    assert absent_question_names("部品P1〜P4は点検済みです。", subjects, [source]) == [
        phrase for _, phrases in expected for phrase in phrases
    ]


def test_repeated_name_at_two_positions_keeps_distinct_spans_and_roles() -> None:
    question = "装置ラムダを導入しました。装置ラムダの保守期限は？"
    subjects, parse = extract(
        question,
        {
            EXTRACT: {"mentions": [mention(question, "装置ラムダ"), mention(question, "装置ラムダ", nth=1)]},
            ROLE: {"entities": [{"entity_id": "N1", "roles": ["given"]}, {"entity_id": "N2", "roles": ["asked"]}]},
        },
    )
    assert subjects.status == STATUS_COMPLETED and subjects.calls == 2
    assert [(m.start, m.end, m.sentence_id, m.roles) for m in subjects.mentions] == [
        (0, 5, "S1", ("given",)),
        (13, 18, "S2", ("asked",)),
    ]
    (extract_system, extract_input, _), (role_system, role_input, _) = parse.calls
    assert extract_system == SUBJECT_EXTRACTION_PROMPT and role_system == SUBJECT_ROLE_PROMPT
    assert extract_input == {"question": question}
    # 段の間はコードが確定した位置と文の ID だけを渡す。文はコードが区切る。
    assert role_input["sentences"] == [
        {"sentence_id": "S1", "text": "装置ラムダを導入しました。"},
        {"sentence_id": "S2", "text": "装置ラムダの保守期限は？"},
    ]
    assert [(e["entity_id"], e["start"], e["sentence_id"]) for e in role_input["entities"]] == [("N1", 0, "S1"), ("N2", 13, "S2")]


def test_mention_crossing_a_sentence_boundary_is_an_error() -> None:
    question = "装置ラムダ。保守期限は？"
    with pytest.raises(QuestionSubjectError) as caught:
        prepare_roles(question, [{"start": 2, "end": 8, "text": question[2:8], "designation": "named"}])
    assert caught.value.code == "crosses_sentence_boundary"
    subjects, parse = extract(question, {EXTRACT: {"mentions": [mention(question, "ラムダ。保守")]}})
    assert (subjects.status, subjects.failure) == (STATUS_FAILED, "crosses_sentence_boundary")
    assert [name for _, _, name in parse.calls] == [EXTRACT]


def test_no_named_mention_skips_the_role_stage() -> None:
    question = "保守期限の手順は？"
    subjects, parse = extract(question, {EXTRACT: {"mentions": [mention(question, "保守期限", "generic")]}})
    assert (subjects.status, subjects.calls, subjects.mentions) == (STATUS_NO_NAMED, 1, ())
    assert [name for _, _, name in parse.calls] == [EXTRACT]
    assert absent_question_names("保守期限は5年です。", subjects, ["無関係"]) == []


@pytest.mark.parametrize(
    ("entities", "code"),
    [
        ([], "ValidationError"),
        ([{"entity_id": "N1", "roles": ["asked", "asked"]}], "invalid_roles"),
    ],
    ids=["missing-entity", "duplicate-role"],
)
def test_role_output_violating_the_contract_keeps_the_names_without_roles(entities: list[dict[str, Any]], code: str) -> None:
    # 役割の段が失敗しても、抽出した名前は役割なしで残す（名前は一律に照合する。役割は文面の選び分けだけ）。
    question = "装置ラムダの保守期限は？"
    subjects, _ = extract(question, {EXTRACT: {"mentions": [mention(question, "装置ラムダ")]}, ROLE: {"entities": entities}})
    assert (subjects.status, subjects.failure, subjects.calls) == (STATUS_ROLES_UNAVAILABLE, code, 2)
    assert [(m.start, m.end, m.text, m.sentence_id, m.roles) for m in subjects.mentions] == [(0, 5, "装置ラムダ", "S1", ())]
    assert subjects.diagnostics() == {
        "status": STATUS_ROLES_UNAVAILABLE,
        "calls": 2,
        "named_mentions": 1,
        "verified_mentions": 1,
        "failure": code,
    }


def test_role_call_failure_keeps_the_names_without_roles() -> None:
    question = "装置ラムダの保守期限は？"
    subjects, _ = extract(question, {EXTRACT: {"mentions": [mention(question, "装置ラムダ")]}, ROLE: TimeoutError("synthetic")})
    assert (subjects.status, subjects.failure, [m.roles for m in subjects.mentions]) == (STATUS_ROLES_UNAVAILABLE, "TimeoutError", [()])


def test_model_call_failure_is_returned_not_raised() -> None:
    subjects, _ = extract("装置ラムダの保守期限は？", {EXTRACT: TimeoutError("synthetic")})
    assert (subjects.status, subjects.failure) == (STATUS_FAILED, "TimeoutError")
    assert subjects.diagnostics() == {"status": STATUS_FAILED, "calls": 1, "named_mentions": 0, "failure": "TimeoutError"}


# ---------- 工程の推論の量（抽出の段の呼び出しだけ） ----------


def provider_settings(registered: str, **overrides: Any) -> Any:
    """モデルの登録の推論の量が ``registered`` の provider と、別のモデルの provider ``other`` を持つ合成の設定（接続先は偽）。"""
    base = get_settings(environ={}, dotenv_path=None, **overrides)

    def provider(provider_id: str, model: str) -> LlmProviderSettings:
        return LlmProviderSettings(
            provider_id=provider_id,
            label="synthetic",
            region="",
            project_id="",
            api_key="synthetic",
            model=model,
            endpoint="https://synthetic.invalid/v1",
            reasoning_effort=registered,
        )

    providers = {base.default_answer_llm: provider(base.default_answer_llm, "synthetic-model"), "other": provider("other", "other-model")}
    return replace(base, llm_providers=providers)


def sent_calls(settings: Any, provider_id: str | None = None) -> list[tuple[str, str, str | None]]:
    """2 段の呼び出しの schema の名前・各呼び出しに渡した設定の推論の量（送る値）・provider ID。"""
    sent: list[tuple[str, str, str | None]] = []
    question = "装置ラムダを導入しました。装置ラムダの保守期限は？"
    responses = {
        EXTRACT: {"mentions": [mention(question, "装置ラムダ"), mention(question, "装置ラムダ", nth=1)]},
        ROLE: {"entities": [{"entity_id": "N1", "roles": ["given"]}, {"entity_id": "N2", "roles": ["asked"]}]},
    }

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        target = options.get("provider_id") or call_settings.default_answer_llm
        sent.append((schema.__name__, call_settings.llm_providers[target].reasoning_effort, options.get("provider_id")))
        return schema.model_validate(responses[schema.__name__])

    assert extract_question_subjects(question, settings, provider_id=provider_id, parse_text=parse).status == STATUS_COMPLETED
    return sent


def sent_efforts(settings: Any) -> list[tuple[str, str]]:
    return [(name, effort) for name, effort, _ in sent_calls(settings)]


def test_each_stage_call_uses_its_own_step_reasoning_effort() -> None:
    # 既定は抽出の段だけ medium、役割の段はモデルの登録値。渡された設定（モデルの登録値）は変えない。
    settings = provider_settings("low")
    assert (settings.question_subject_extraction_reasoning_effort, settings.question_subject_role_reasoning_effort) == ("medium", "")
    assert sent_efforts(settings) == [(EXTRACT, "medium"), (ROLE, "low")]
    assert settings.llm_providers[settings.default_answer_llm].reasoning_effort == "low"
    # 段ごとに別に決まる（片方だけ変えても他方は変わらない）。
    assert sent_efforts(provider_settings("low", question_subject_role_reasoning_effort="high")) == [(EXTRACT, "medium"), (ROLE, "high")]
    assert sent_efforts(provider_settings("low", question_subject_extraction_reasoning_effort="")) == [(EXTRACT, "low"), (ROLE, "low")]


@pytest.mark.parametrize(
    ("registered", "step", "expected"),
    [("low", "high", "high"), ("low", "", "low"), ("low", "model", "low"), ("low", "none", ""), ("", "medium", "")],
    ids=["step-high", "empty-keeps-registered", "model-keeps-registered", "none-omits", "registered-none-never-sends"],
)
def test_step_reasoning_effort_is_a_setting_with_the_shared_override_rules(registered: str, step: str, expected: str) -> None:
    settings = provider_settings(registered, question_subject_extraction_reasoning_effort=step, question_subject_role_reasoning_effort=step)
    assert sent_efforts(settings) == [(EXTRACT, expected), (ROLE, expected)]


def test_role_stage_model_is_a_step_setting_and_extraction_keeps_the_caller_provider() -> None:
    # 既定（空）は両方の段が呼び出し側の provider。
    settings = provider_settings("low")
    caller = settings.default_answer_llm
    assert [(name, provider) for name, _, provider in sent_calls(settings, caller)] == [(EXTRACT, caller), (ROLE, caller)]
    # 段のモデルの設定は役割の段の呼び出しだけを、その provider で呼ぶ。
    settings = provider_settings("low", question_subject_role_llm="other")
    assert [(name, provider) for name, _, provider in sent_calls(settings, caller)] == [(EXTRACT, caller), (ROLE, "other")]


def test_unknown_role_stage_model_fails_without_raising() -> None:
    from rag_engine.adapters.oci import parse_text_response

    settings = provider_settings("low", question_subject_role_llm="missing")
    question = "装置ラムダの保守期限は？"

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        if schema.__name__ == EXTRACT:
            return schema.model_validate({"mentions": [mention(question, "装置ラムダ")]})
        return parse_text_response(system, prompt, call_settings, schema, **options)

    subjects = extract_question_subjects(question, settings, parse_text=parse)
    assert (subjects.status, subjects.calls, len(subjects.mentions)) == (STATUS_ROLES_UNAVAILABLE, 2, 1)


# ---------- 役割の段のモデル（設定の env と録画の応答） ----------

ROLE_RECORDED = json.loads((Path(__file__).parent / "fixtures" / "question_subject_role_grok_recorded.json").read_text(encoding="utf-8"))


def role_model_env(**extra: str) -> dict[str, str]:
    """回答のモデルと役割の段のモデルの接続（合成の接続先）。"""
    return {
        "OCI_ENTERPRISE_AI_ENDPOINT": "https://answer.invalid/openai/v1",
        "OCI_ENTERPRISE_AI_API_KEY": "synthetic-answer",
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "synthetic-answer-model",
        "OCI_ENTERPRISE_AI_REASONING_EFFORT": "low",
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE_MODEL": ROLE_RECORDED["model"],
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE_ENDPOINT": "https://role.invalid/openai/v1",
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE_API_KEY": "synthetic-role",
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE_REASONING_EFFORT": ROLE_RECORDED["reasoning_effort"],
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE_MAX_OUTPUT_TOKENS": "32768",
        **extra,
    }


def test_role_model_env_builds_the_role_stage_provider() -> None:
    settings = get_settings(environ=role_model_env(), dotenv_path=None)
    provider = settings.llm_providers[QUESTION_SUBJECT_ROLE_LLM_PROVIDER]
    assert settings.question_subject_role_llm == QUESTION_SUBJECT_ROLE_LLM_PROVIDER
    assert (provider.model, provider.base_url, provider.reasoning_effort, provider.max_output_tokens) == (
        "xai.grok-4.7",
        "https://role.invalid/openai/v1",
        "low",
        32768,
    )
    # 設定が無ければ provider を作らず、役割の段は呼び出し側の provider のまま。
    plain = get_settings(environ={k: v for k, v in role_model_env().items() if "QUESTION_SUBJECT_ROLE" not in k}, dotenv_path=None)
    assert QUESTION_SUBJECT_ROLE_LLM_PROVIDER not in plain.llm_providers and plain.question_subject_role_llm == ""


def _responses_body(model: str, schema_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """合成の Responses API の応答（抽出の段の応答に使う）。"""
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_synthetic",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(payload, ensure_ascii=False), "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.parametrize("case", ROLE_RECORDED["cases"], ids=[case["case"] for case in ROLE_RECORDED["cases"]])
def test_role_stage_calls_its_model_and_parses_the_recorded_response(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import openai
    from pr_backend_core.observability.model_purpose import model_purpose_attributes

    from rag_engine.adapters import oci

    question = case["question"]
    sent: list[tuple[str, str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["text"]["format"]["name"]
        purpose = model_purpose_attributes().get("pr.gen_ai.purpose", "")
        sent.append((name, body["model"], body.get("reasoning", {}).get("effort", ""), purpose))
        if name == EXTRACT:
            mentions = [
                {
                    "text": question[start:end],
                    "occurrence": 1 + question[:start].count(question[start:end]),
                    "designation": "named",
                    "members": [],
                }
                for start, end in case["spans"]
            ]
            return httpx.Response(200, json=_responses_body(body["model"], name, {"mentions": mentions}))
        return httpx.Response(200, json=case["response"])

    client = openai.OpenAI(
        api_key="synthetic",
        base_url="https://synthetic.invalid/openai/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda provider, settings: client)
    settings = get_settings(environ=role_model_env(), dotenv_path=None)

    subjects = extract_question_subjects(question, settings, parse_text=oci.parse_text_response)

    assert sent == [
        (EXTRACT, "synthetic-answer-model", "medium", "query.subjects"),
        (ROLE, "xai.grok-4.7", "low", "query.subject_roles"),
    ]
    assert subjects.status == STATUS_COMPLETED
    assert [[m.start, m.end] for m in subjects.mentions] == case["spans"]
    assert [list(m.roles) for m in subjects.mentions] == case["expected_roles"]
    assert [row["roles"] for row in case["recorded_output"]["entities"]] == case["expected_roles"]


def test_role_stage_model_without_a_connection_fails_and_the_answer_continues() -> None:
    from rag_engine.adapters import oci

    # backend は登録していないモデルの接続を渡さない（model だけ）。
    missing = ("RAG_ENGINE_QUESTION_SUBJECT_ROLE_ENDPOINT", "RAG_ENGINE_QUESTION_SUBJECT_ROLE_API_KEY")
    settings = get_settings(environ={k: v for k, v in role_model_env().items() if k not in missing}, dotenv_path=None)
    question = ROLE_RECORDED["cases"][0]["question"]
    start, end = ROLE_RECORDED["cases"][0]["spans"][0]

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        if schema.__name__ == EXTRACT:
            return schema.model_validate(
                {"mentions": [{"text": question[start:end], "occurrence": 1, "designation": "named", "members": []}]}
            )
        return oci.parse_text_response(system, prompt, call_settings, schema, **options)

    subjects = extract_question_subjects(question, settings, parse_text=parse)
    assert (subjects.status, subjects.failure, subjects.calls) == (STATUS_ROLES_UNAVAILABLE, "RuntimeError", 2)
    assert [(m.text, m.roles) for m in subjects.mentions] == [(question[start:end], ())]


# ---------- 名前の照合（モデルを呼ばない） ----------

RULE = "利用者が外部サービスを使う場合、管理部が承認したものに限る。"
QUESTION = "業務でServiceXは使ってよいですか？"
LEAP = "ServiceXは管理部が承認した外部サービスなので利用できます。"
GENERAL = "外部サービスは、管理部が承認したものに限り利用できます。"
GAP = "ServiceXが管理部の承認を受けたかは資料で確認できません。"


def subjects_of(question: str, *rows: tuple[str, tuple[str, ...]]) -> QuestionSubjects:
    """役割まで確定した結果（照合の単体テスト用）。"""
    mentions = []
    cursor = 0
    bounds = question_sentences(question)
    for text, roles in rows:
        start = question.index(text, cursor)
        cursor = start + len(text)
        sentence = next(index for index, (first, last) in enumerate(bounds) if first <= start < last)
        mentions.append(SubjectMention(start, start + len(text), text, f"S{sentence + 1}", roles))
    return QuestionSubjects(question, STATUS_COMPLETED, tuple(mentions))


def roles_unavailable(question: str, *texts: str) -> QuestionSubjects:
    """役割の段が失敗した結果（名前の位置だけ。役割なし）。"""
    full = subjects_of(question, *((text, ()) for text in texts))
    return QuestionSubjects(question, STATUS_ROLES_UNAVAILABLE, full.mentions, failure="TimeoutError", calls=2)


@pytest.mark.parametrize(
    ("text", "subjects", "sources", "expected"),
    [
        (LEAP, subjects_of(QUESTION, ("ServiceX", ("asked",))), [RULE], ["ServiceX"]),
        (GENERAL, subjects_of(QUESTION, ("ServiceX", ("asked",))), [RULE], []),
        (LEAP, subjects_of(QUESTION, ("ServiceX", ("asked",))), ["ＳＥＲＶＩＣＥＸは承認済みである。"], []),
        ("端末ミモザは承認済みです。", subjects_of("端末ミモザは使えますか？", ("端末ミモザ", ("asked",))), [RULE], ["端末ミモザ"]),
        # 前提として述べた名前も、例の名前も、役割に関わらず照合する（利用者の決定。名前は一律に照合）。
        (
            LEAP,
            subjects_of("ServiceXを導入しました。ServiceXの条件は？", ("ServiceX", ("given",)), ("ServiceX", ("asked",))),
            [RULE],
            ["ServiceX"],
        ),
        ("ServiceX等は利用できません。", subjects_of("ServiceXなどの外部サービスは？", ("ServiceX", ("example",))), [RULE], ["ServiceX"]),
        # 1 文で名前を問う質問の役割を given と誤って判定しても、名前は照合する（製品の経路の受け入れで見つけた形）。
        (LEAP, subjects_of(QUESTION, ("ServiceX", ("given",))), [RULE], ["ServiceX"]),
        (LEAP, roles_unavailable(QUESTION, "ServiceX"), [RULE], ["ServiceX"]),
        (LEAP, QuestionSubjects(QUESTION, STATUS_FAILED, failure="TimeoutError"), [RULE], []),
        (LEAP, None, [RULE], []),
    ],
    ids=[
        "leap",
        "not-written",
        "width-case",
        "katakana",
        "given-and-asked",
        "example",
        "misjudged-given",
        "roles-unavailable",
        "failed",
        "not-extracted",
    ],
)
def test_absent_question_names(text: str, subjects: QuestionSubjects | None, sources: list[str], expected: list[str]) -> None:
    assert absent_question_names(text, subjects, sources) == expected


@pytest.mark.parametrize(
    ("subjects", "sources", "gaps", "expected"),
    [
        (subjects_of(QUESTION, ("ServiceX", ("asked",))), [RULE], [], [("asked", ["ServiceX"])]),
        (
            subjects_of("Acme ServiceXは使えますか？", ("Acme", ("asked",)), ("ServiceX", ("asked",))),
            [RULE],
            [],
            [("asked", ["Acme ServiceX"])],
        ),
        (
            subjects_of("ServiceXとToolYは？", ("ServiceX", ("asked",)), ("ToolY", ("asked",))),
            [RULE],
            [],
            [("asked", ["ServiceX", "ToolY"])],
        ),
        (subjects_of(QUESTION, ("ServiceX", ("asked",))), ["ServiceXは承認済みである。"], [], []),
        (subjects_of(QUESTION, ("ServiceX", ("asked",))), [RULE], [GAP], []),
        # 役割は文面の選び分けだけ。前提・例・役割の誤り・役割の段の失敗でも名前は照合する。
        (subjects_of(QUESTION, ("ServiceX", ("given",))), [RULE], [], [("given", ["ServiceX"])]),
        (
            subjects_of("ToolYを使っています。ServiceXは使えますか？", ("ToolY", ("given",)), ("ServiceX", ("asked",))),
            [RULE],
            [],
            [("given", ["ToolY"]), ("asked", ["ServiceX"])],
        ),
        (
            subjects_of("ServiceXを導入しました。ServiceXの条件は？", ("ServiceX", ("given",)), ("ServiceX", ("asked",))),
            [RULE],
            [],
            [("asked", ["ServiceX"])],
        ),
        (subjects_of("ServiceXなどの外部サービスは？", ("ServiceX", ("example",))), [RULE], [], [("example", ["ServiceX"])]),
        (roles_unavailable(QUESTION, "ServiceX"), [RULE], [], [("unknown", ["ServiceX"])]),
        (
            subjects_of("ToolYを使っています。ServiceXは使えますか？", ("ToolY", ("given",)), ("ServiceX", ("asked",))),
            ["ToolYの設定"],
            [],
            [("asked", ["ServiceX"])],
        ),
    ],
    ids=[
        "one",
        "adjacent-merged",
        "two",
        "in-source",
        "already-in-gap",
        "misjudged-given",
        "premise-and-asked",
        "same-name-given-then-asked",
        "example",
        "roles-unavailable",
        "premise-in-source",
    ],
)
def test_unconfirmed_question_notices(
    subjects: QuestionSubjects, sources: list[str], gaps: list[str], expected: list[tuple[str, list[str]]]
) -> None:
    assert [(notice.kind, list(notice.phrases)) for notice in unconfirmed_question_notices(subjects, sources, gaps)] == expected


# ---------- 回答の流れ（生成・是正・要約・gap） ----------

SUBJECTS = {EXTRACT: {"mentions": [mention(QUESTION, "ServiceX")]}, ROLE: {"entities": [{"entity_id": "N1", "roles": ["asked"]}]}}


def record(text: str = RULE) -> AnswerRecord:
    return AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="c1",
        source_run_id="run",
        metadata={"section_path": ["第1節"]},
    )


def draft(text: str, summary: str = "", *, gap: str = "") -> dict[str, Any]:
    gaps = [{"kind": "gap", "request_id": "Q1", "text": gap, "citations": []}] if gap else []
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "対象", "relation": "direct"}]}],
        "items": [
            {"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": "E1", "line_start": 1, "line_end": 1}]},
            *gaps,
        ],
        "summary": summary or text,
        "summary_item_indexes": [0],
        "confidence": "medium",
    }


def answer(
    *outputs: dict[str, Any],
    subjects: dict[str, Any] | None = None,
    inject: bool = True,
    question: str = QUESTION,
    source: str = RULE,
) -> tuple[Any, list[str], list[str]]:
    """監査なしの回答を、生成の録画応答と対象の抽出の録画応答で通す。(結果, 生成の入力, 対象の抽出の schema の名前)。

    生成（``GroundedDraftWire``）は ``outputs`` を順に返す。録画の無い呼び出し（当てはめの監査など）は失敗させる。
    """
    prompts: list[str] = []
    subject_calls: list[str] = []
    values = iter(outputs)
    recorded = SUBJECTS if subjects is None else subjects

    def parse(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        if schema.__name__ in recorded:
            subject_calls.append(schema.__name__)
            if isinstance(recorded[schema.__name__], Exception):
                raise recorded[schema.__name__]
            return schema.model_validate(recorded[schema.__name__])
        if schema.__name__ != "GroundedDraftWire":
            raise RuntimeError("録画の無い呼び出し")
        prompts.append(prompt)
        return generated_fixture(schema, prompt, next(values))

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("追加の外部呼び出しは不可")

    context = AnswerContext(records=[record(source)], text=source)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none")
    dependencies = AnswerDependencies(
        unused, unused, parse, unused, unused, question_subjects=extract_question_subjects if inject else None
    )
    with bind_dependencies(dependencies):
        result = synthesize_grounded_answer(question, context, settings, image_prompt_mode="text_only").response
    return result, prompts, subject_calls


UNCONFIRMED = answer_labels().subject_unconfirmed_gap.format(names="ServiceX")


def test_answer_says_the_asked_name_is_unconfirmed_even_without_a_model_gap() -> None:
    """#4345: モデルが gap を書かなくても、問うた対象が引いた原文で確認できないことを本文で伝える。規則の説明も残す。"""
    result, prompts, subject_calls = answer(draft(GENERAL))
    assert len(prompts) == 1 and subject_calls == [EXTRACT, ROLE]
    assert RULE in result.answer_text
    assert UNCONFIRMED in result.answer_text
    assert answer_labels().gaps_section in result.answer_text


def test_summary_with_an_absent_name_is_withheld_regenerated_once_and_extracted_once() -> None:
    result, prompts, subject_calls = answer(draft(LEAP), draft(LEAP))
    assert len(prompts) == 2
    # 是正の round でも抽出をやり直さない（回答の中で 1 回）。
    assert subject_calls == [EXTRACT, ROLE]
    feedback = prompts[1][prompts[1].index('{"subject_name_issues"') :]
    assert feedback.startswith('{"subject_name_issues": [{"text": "' + LEAP + '", "absent_names": ["ServiceX"]')
    assert LEAP not in result.answer_text
    assert answer_labels().neutral_summary in result.answer_text
    assert UNCONFIRMED in result.answer_text


def test_gap_written_by_the_model_is_not_repeated() -> None:
    result, _, subject_calls = answer(draft(GENERAL, gap=GAP), draft(GENERAL, gap=GAP))
    assert subject_calls == [EXTRACT, ROLE]
    assert GAP in result.answer_text
    assert UNCONFIRMED not in result.answer_text


@pytest.mark.parametrize(
    ("roles", "kind"),
    [
        # 製品の経路の受け入れの形: 1 文で名前を問う質問の役割を given と誤って判定しても、名前は照合して伝える。
        ({"entities": [{"entity_id": "N1", "roles": ["given"]}]}, "given"),
        ({"entities": [{"entity_id": "N1", "roles": ["example"]}]}, "example"),
        ({"entities": [{"entity_id": "N1", "roles": ["asked"]}]}, "asked"),
        # 役割の段が失敗しても照合する。
        (RuntimeError("synthetic"), "unknown"),
    ],
    ids=["misjudged-given", "example", "asked", "roles-unavailable"],
)
def test_every_named_subject_is_verified_whatever_the_role(roles: Any, kind: str) -> None:
    """利用者の決定（2026-11-07）: 名前は役割に関わらず照合し、無ければ伝える。文面は中立の 1 文、役割の種類は診断に残す。"""
    result, _, subject_calls = answer(draft(GENERAL), draft(GENERAL), subjects={EXTRACT: SUBJECTS[EXTRACT], ROLE: roles})
    assert subject_calls == [EXTRACT, ROLE]
    assert UNCONFIRMED in result.answer_text
    assert [{"kind": kind, "names": 1}] in _values_of(result.generation_trace, "subject_unconfirmed")


@pytest.mark.parametrize(
    ("range_mention", "notice"),
    [
        # 範囲の表記を 1 つの名前として返すと、その表記は原文に無いので確認できないと伝える（#4584 の誤った通知の形）。
        (mention(RANGE_QUESTION, "部品P1〜P4"), True),
        # まとめた表記（collective）として返せば、範囲の表記は照合せず、写された個々の名前だけを照合する。
        (collective(RANGE_QUESTION, "部品P1〜P4", "部品P1"), False),
    ],
    ids=["range-as-one-name", "range-as-collective"],
)
def test_range_mention_with_members_in_the_cited_source_is_not_reported_unconfirmed(range_mention: dict[str, Any], notice: bool) -> None:
    roles = {"entities": [{"entity_id": "N1", "roles": ["given"]}, {"entity_id": "N2", "roles": ["asked"]}]}
    subjects = {EXTRACT: {"mentions": [range_mention, mention(RANGE_QUESTION, "部品P5")]}, ROLE: roles}
    result, _, subject_calls = answer(draft(RANGE_SOURCE), subjects=subjects, question=RANGE_QUESTION, source=RANGE_SOURCE)
    assert subject_calls == [EXTRACT, ROLE]
    unconfirmed = answer_labels().subject_unconfirmed_gap.format(names="部品P1〜P4")
    assert (unconfirmed in result.answer_text) is notice
    assert ("subject_unconfirmed" in json.dumps(result.generation_trace, ensure_ascii=False)) is notice


def _values_of(node: Any, key: str) -> list[Any]:
    """入れ子の dict / list から ``key`` の値をすべて集める（実行記録の中の位置に依らず確かめる）。"""
    found: list[Any] = []
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                found.append(value)
            found.extend(_values_of(value, key))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.extend(_values_of(value, key))
    return found


def test_summary_naming_a_misjudged_given_name_is_corrected() -> None:
    result, prompts, _ = answer(
        draft(LEAP), draft(LEAP), subjects={EXTRACT: SUBJECTS[EXTRACT], ROLE: {"entities": [{"entity_id": "N1", "roles": ["given"]}]}}
    )
    assert len(prompts) == 2 and '"absent_names": ["ServiceX"]' in prompts[1]
    assert LEAP not in result.answer_text


@pytest.mark.parametrize(
    ("subjects", "inject", "calls"),
    [
        ({EXTRACT: {"mentions": []}}, True, [EXTRACT]),
        ({EXTRACT: {"mentions": [anchor("ServiceY")]}}, True, [EXTRACT]),
        (SUBJECTS, False, []),
    ],
    ids=["no-named-mention", "text-not-in-question", "not-injected"],
)
def test_no_check_without_an_extracted_name(subjects: dict[str, Any], inject: bool, calls: list[str]) -> None:
    result, prompts, subject_calls = answer(draft(LEAP), subjects=subjects, inject=inject)
    assert subject_calls == calls
    assert all("subject_name_issues" not in prompt for prompt in prompts)
    assert UNCONFIRMED not in result.answer_text


def test_item_check_marks_an_asked_name_absent_from_the_cited_text() -> None:
    """説明（item）の文に書いた問うた対象の名前を、引いた原文に照らす（verify の検査）。"""
    subjects = subjects_of(QUESTION, ("ServiceX", ("asked",)))
    leap = grounded.CheckedItem(grounded.GroundedItem(kind="rule", text=LEAP), span={"text": RULE})
    general = grounded.CheckedItem(grounded.GroundedItem(kind="rule", text=GENERAL), span={"text": RULE})
    gap = grounded.CheckedItem(grounded.GroundedItem(kind="gap", text=GAP))
    assert grounded._item_absent_names(subjects, leap, [RULE]) == ["ServiceX"]
    assert grounded._item_absent_names(subjects, general, [RULE]) == []
    assert grounded._item_absent_names(subjects, gap, [RULE]) == []
    assert grounded._item_absent_names(None, leap, [RULE]) == []


def test_the_correction_is_part_of_the_audit_none_feedback() -> None:
    feedback = {"subject_name_issues": [{"text": LEAP, "absent_names": ["ServiceX"]}]}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE) == feedback
    assert grounded.feedback_signature(feedback)["subject_name_issues"] == 1
    assert grounded.pending_feedback_labels(feedback) == ["引用原文に無い対象の名前 1 件"]


# ---------- ハードコードの検査 ----------

# 契約を調整した実験の質問と、その名前の sha256（文字数 → hash）。文字列そのものは repo に置かない。
_TUNING_DIGESTS = {
    4: {"47deedaa61dbb3847bebd3864cb9c42bdd3b224fad4f05c6d0751cc07d81e6be"},
    5: {
        "d2756d651b9b85ebee9ee40974f65aec5de278f9a516cfddfbba2dbcbd93a9c0",
        "7ca0f15efafad5837842c02eff52ad7d4f0a9eb523d805e70932f4d918169859",
        "61b992cdadcb87a7485c7406d9790064f4e397d34825d1b518362e6509402800",
        "cb88808faabb116a77affda54aae288ae7af3382bcf3aef990485e91c1fd451f",
    },
    6: {"8d930d803284b330e0dfab3555b9c301c6290c5804b2f293e50f4b95ff1ad486"},
    21: {"c0d2e6072afc389d80b2955f8317c9e92c0914a39f23c224ffa4c46630e510fc"},
    27: {"b95e9ddc9f8a084d5c1036ca460f7769eeff66021c72e90f313193e4dc544334"},
    31: {"bf0fdd835f561b8eb7894b2eca3e82f3b3fa84c7eb29a43fe0d5861ad5b9dbbb"},
    35: {"c5a632d892cf2ba83c77dab85d76f3f0bf2d2eed2ab93f28015fffd4fc0da418"},
    42: {"72b06d0570bd67885849f907669dd054a98236f82a1226613e4cb217658d7fe1"},
}
# 契約の確認に使う未知の合成の質問と、その名前の sha256（調整に使わない評価の組。コード・指示・fixture へ漏らさない）。
_UNSEEN_DIGESTS = {
    5: {
        "6cc0c767623e200aaa2fae2f3725f0dc10f1efa89bccac2ed390b38a28a8f815",
        "1304cd276bd8dfff82cb80f51c52d9d9371dcc38c47c19ae15a7fd6f862693a4",
        "4facc826062ce9c5b2b867c19e23f78967cc97aedeab24a9de36a079ccbbb95a",
        "94f662f328bdf8eeb5c3a1570a5f6ea9340469dd286c246d833792565f3c5965",
        "bfeabd8b4d2267341f8cc2ae6185ce2d92984f07712a3857566133267d7af5a3",
        "c4dc97c43831caecc021d54a8bc8cd9e155482c7db50b1b1d687e279aef903a1",
        "ed9fc8cbe367a5133c1d76ebcdf05e994b5817f281a22247d518f0c7d41681ea",
    },
    6: {
        "ab1a49a5eea3334ea4d8a128cb51c39c9df9b6cd0a756559ceb38e62841561d4",
        "e9a8de2fcee9c284dd6855bf2191911e60cac0bdca3d61af8373ea87231e835f",
        "eef5d26290862f12272246d2aff070c445f2500bcbce0b6498f83b6b96a21d88",
        # #4584 の実モデルの確認の回帰の題の名前（以下の 8・19・22・28 も同じ確認の名前と質問）。
        "f9d91be521da5ae961aa7afbc16f4c636df401c8ae4575a7dfc15c5412e959f9",
    },
    8: {"60a617b3cdacbd8ef9d98c69993e8519e044a97e6ab25e2344f612a45d4bf3a2"},
    19: {"282b37fd2383682d2659e75491dff91f386786ae507fef758a5a9f1cacb9fdc8"},
    21: {"173d67c99ee9434a81128378fb24cbb65879c0f13b194b5679a738813ce1499a"},
    22: {
        "2464ca0dbc33317dbf3f580ec1664c56029805353b8236b728100c0c830aaada",
        "f5641b436d12a52cd9f002d10d32f29b06be2b22f43964b55fa85fc47cba7570",
    },
    25: {"26f039a9c360c62697c580af4aa61ffd83fbf4573ae6211dfbd86db0214f6b99"},
    28: {"b06281659284d4139d7aa72d37661dc8fd203b74eebb28b337773ba9bf5ca40b"},
    34: {
        "7d5e6cf705c0900fbdadd1ea9e351b57b6f40bc74ed76a5dbd8a0efaa215bcd6",
        "d23e2fc589f69f4a90cbc9aa3dbd0797c58182b87f9566ff82c43df8cf309261",
        "dd3b5e85b55ccaf5f2314ef11c042efd0e280101dc54ca837f8b02f9755dff11",
    },
    36: {"ed803dfbc5f777fdc208e503ee12d3322d5d8130ce0adbd12540294d85e7d259"},
}
_EXPERIMENT_DIGESTS = {
    length: _TUNING_DIGESTS.get(length, set()) | _UNSEEN_DIGESTS.get(length, set()) for length in {*_TUNING_DIGESTS, *_UNSEEN_DIGESTS}
}
_ENGINE = Path(__file__).resolve().parents[1]
_SCANNED = (
    _ENGINE / "rag_engine" / "generation" / "question_subjects.py",
    _ENGINE / "rag_engine" / "generation" / "grounded.py",
    _ENGINE / "rag_engine" / "generation" / "answering.py",
    _ENGINE / "rag_engine" / "evaluation" / "answer_eval.py",
    _ENGINE / "rag_engine" / "dependencies.py",
    _ENGINE / "tests" / "test_question_subjects.py",
    _ENGINE / "tests" / "test_answer_evaluation.py",
    _ENGINE / "tests" / "judge_stub.py",
    _ENGINE / "tests" / "fixtures" / "question_subject_role_grok_recorded.json",
    _ENGINE.parent / "rag_parser_core" / "rag_parser_core" / "language" / "packs" / "ja.json",
)


def _tuning_strings_in(text: str) -> list[str]:
    """``text`` の中で、調整・未知の評価に使った質問・名前と hash が一致する部分。

    いずれも ASCII を含まないので、非 ASCII の連なりだけを見る。
    """
    found: list[str] = []
    run: list[str] = []
    for char in [*text, "\n"]:
        if not char.isascii():
            run.append(char)
            continue
        joined = "".join(run)
        for length, digests in _EXPERIMENT_DIGESTS.items():
            for start in range(len(joined) - length + 1):
                window = joined[start : start + length]
                if hashlib.sha256(window.encode("utf-8")).hexdigest() in digests:
                    found.append(window)
        run = []
    return found


def test_no_tuning_question_or_name_in_code_prompts_or_fixtures() -> None:
    """契約の調整と未知の評価に使った質問・名前を、製品のコード・指示・テストの fixture に書かない（ハードコードしない）。"""
    texts = {str(path.relative_to(_ENGINE.parent)): path.read_text(encoding="utf-8") for path in _SCANNED}
    texts["SUBJECT_EXTRACTION_PROMPT"] = SUBJECT_EXTRACTION_PROMPT
    texts["SUBJECT_ROLE_PROMPT"] = SUBJECT_ROLE_PROMPT
    assert {name: found for name, text in texts.items() if (found := _tuning_strings_in(text))} == {}
