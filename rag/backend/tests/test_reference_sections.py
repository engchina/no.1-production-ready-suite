"""文書全体への参照の参照先を、目次から選んだ章節だけで読む(#5263・#5348)。

文書名・ファイル名だけの参照は、参照先の文書の目次を回答のモデルに 1 回渡して章節を選ばせ、
その章節だけを読む。選べない・読めない・設定が無効のときは何も読まず(文書の中の検索もしない)、
理由を受領記録に残す。番号つきの参照は今どおり番号で定位し、定位した節は節の大きさで読む
(回答のモデルの入力の上限の 1/2 以下なら全体、超えれば子の節に縮める。#5817)。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from pr_backend_core.observability.model_purpose import model_purpose_attributes

from app.rag import section_map_service
from app.rag.answer_engine import _SearchState
from app.rag.cross_references import ReferenceTarget
from app.rag.reference_sections import (
    DOCUMENT_NO_OUTLINE,
    DOCUMENT_OUTLINE_ERROR,
    READ_MODE_SECTIONS,
    REFERENCE_SECTION_PROMPT,
    SKIP_NO_SELECTION,
    SKIP_NOT_PLANNED,
    SKIP_OUTLINE_SELECTION_DISABLED,
    SKIP_SECTION_OVER_LIMIT,
    SKIP_SECTIONS_EMPTY,
    SKIP_SELECTION_ERROR,
    SKIP_UNKNOWN_IDS,
    DocumentReadPlan,
    ReferenceSectionChoice,
    build_catalog,
    fit_reference_section,
    normalized_section_label,
    resolve_choice,
    without_descendants,
)
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.search_expansion_support import search_with_expansions
from tests.test_cross_references import (
    CHAPTER3,
    FakeGenAi,
    FakeLlm,
    ReferenceOracle,
    SectionMap,
    _engine,
    _node,
    _retrieved,
    _section_chunks,
)

DOC_A, DOC_B = "doc-a", "doc-b"
FILE_A, FILE_B = "ops-a.txt", "ops-b.txt"
TARGET_A = ReferenceTarget(FILE_A, "document", "", FILE_A)
TARGET_B = ReferenceTarget(FILE_B, "document", "", FILE_B)
QUESTION = "調査の進め方を、資料の具体的な操作まで含めて教えて"


def _doc_chunks(document_id: str, file_name: str, count: int) -> list[RetrievedChunk]:
    """設定項目の説明が続き、最後の章節にだけ操作の本文がある合成の文書。"""
    rows = [
        _retrieved(
            f"{document_id}:cs:{n}",
            f"設定項目 {n} の説明。",
            f"設定項目 {n}",
            document_id=document_id,
            file_name=file_name,
            chunk_set_id="cs",
        )
        for n in range(count)
    ]
    rows.append(
        _retrieved(
            f"{document_id}:cs:body",
            "操作の本文: tool-x run --since 2h を実行する。",
            "操作の手順",
            document_id=document_id,
            file_name=file_name,
            chunk_set_id="cs",
        )
    )
    return rows


def _label(context: str, title: str) -> str:
    """目次の行から章節の名前の ID(``S12``)を引く(モデルが選ぶ代わり)。"""
    line = next(line for line in context.splitlines() if line.strip().endswith(title))
    return line.split("[", 1)[1].split("]", 1)[0]


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outlines: set[str],
    pick: Any,
    targets: list[ReferenceTarget] | None = None,
    enabled: bool = True,
    **settings: Any,
) -> tuple[Any, ReferenceOracle, SectionMap, FakeLlm]:
    chunks = [*_doc_chunks(DOC_A, FILE_A, 30), *_doc_chunks(DOC_B, FILE_B, 30)]
    targets = targets or [TARGET_A, TARGET_B]
    anchor = _retrieved(
        "doc-index:a",
        "調査は次の資料に従う。" + "、".join(target.label for target in targets),
        "索引",
        document_id="doc-index",
        file_name="index.md",
        targets=targets,
    )
    oracle = ReferenceOracle([anchor], chunks)
    oracle.documents = [(DOC_A, FILE_A, ()), (DOC_B, FILE_B, ())]
    sections = SectionMap(chunks, outlines=outlines)
    monkeypatch.setattr(section_map_service, "section_outline", sections.outline)
    monkeypatch.setattr(section_map_service, "read_section", sections.read)
    engine = _engine(
        oracle, FakeGenAi(), rag_reference_outline_selection_enabled=enabled, **settings
    )
    llm = FakeLlm(pick)
    engine._reference_llm = llm
    return engine, oracle, sections, llm


async def _search(engine: Any) -> tuple[Any, _SearchState]:
    state = _SearchState()
    result = await search_with_expansions(
        engine, SearchRequest(query=QUESTION), state, retrieval_queries=[QUESTION]
    )
    return result, state


def _selections(state: _SearchState) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], state.reference_sources[0]["section_selections"])


def _reads(state: _SearchState) -> dict[str, dict[str, Any]]:
    (record,) = state.reference_sources
    reads = cast(list[dict[str, Any]], record["document_reads"])
    return {str(item["document_id"]): item for item in reads}


async def test_selected_sections_are_read_with_one_call_for_all_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 つの参照先の目次を 1 回の呼び出しで渡し、選んだ章節(操作の本文)を読む。"""

    def pick(context: str) -> list[str]:
        # 章節選びは画像の読み取り(document.read)ではなく、専用の用途で記録する(#5771)。
        assert model_purpose_attributes() == {"pr.gen_ai.purpose": "section.select"}
        # 2 つの文書の「操作の手順」は別の ID になる(文書をまたいで一意)。
        labels = [
            line.split("[", 1)[1].split("]", 1)[0]
            for line in context.splitlines()
            if line.strip().endswith("操作の手順")
        ]
        assert len(set(labels)) == 2
        return labels

    engine, oracle, sections, llm = _setup(monkeypatch, outlines={DOC_A, DOC_B}, pick=pick)

    result, state = await _search(engine)

    added = {child.chunk_uid for child in result.expansion_chunks}
    assert added == {f"{DOC_A}:cs:body", f"{DOC_B}:cs:body"}
    # 文書の中の検索はしない。章節は rag_read_section と同じ読取で読む。
    assert oracle.search_calls == []
    assert sorted(sections.read_calls) == [
        (DOC_A, f"{DOC_A}#操作の手順"),
        (DOC_B, f"{DOC_B}#操作の手順"),
    ]
    (call,) = llm.calls
    assert call["prompt"] == QUESTION
    assert call["system_prompt"] == REFERENCE_SECTION_PROMPT
    assert call["response_schema"] == ReferenceSectionChoice.model_json_schema()
    # 目次は文書ごとのブロックで、各行に ID を付ける。
    assert f'<document name="{FILE_A}">' in call["context"]
    assert "[S1] 設定項目 0" in call["context"]
    reads = _reads(state)
    assert {item["read_mode"] for item in reads.values()} == {READ_MODE_SECTIONS}
    assert {item["skip_reason"] for item in reads.values()} == {None}
    assert reads[DOC_A]["selected_sections"] == [
        {"section_id": f"{DOC_A}#操作の手順", "title": "操作の手順"}
    ]
    # 選ばなかった章節(設定項目の説明)は読まない。
    assert f"{DOC_A}:cs:0" not in added
    (selection,) = _selections(state)
    assert selection["outcome"] == "selected"
    assert selection["documents"] == 2


async def test_ids_not_in_the_outline_are_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """返った ID のうち目次に無いものは捨て、目次にある ID だけを読む。"""
    engine, _oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: ["S9999", "第3章", f"[{_label(context, '操作の手順')}]"],
    )

    result, state = await _search(engine)

    assert [child.chunk_uid for child in result.expansion_chunks] == [f"{DOC_A}:cs:body"]
    assert sections.read_calls == [(DOC_A, f"{DOC_A}#操作の手順")]
    (selection,) = _selections(state)
    assert selection["discarded_ids"] == ["S9999", "第3章"]


def _assert_read_nothing(result: Any, state: _SearchState, reason: str) -> dict[str, Any]:
    """参照先を何も読まず(参照の記述の chunk は根拠に残る)、理由を受領記録に残したこと。"""
    assert result.expansion_chunks == []
    assert [child.chunk_uid for child in result.child_chunks] == ["doc-index:a"]
    (record,) = state.reference_sources
    assert record["added_count"] == 0
    (source,) = cast(list[dict[str, Any]], record["sources"])
    assert (source["target_outcomes"], source["dependency_reasons"]) == ([reason], [reason])
    read = _reads(state)[DOC_A]
    assert (read["read_mode"], read["skip_reason"], read["read_chunk_count"]) == (None, reason, 0)
    return read


@pytest.mark.parametrize(
    ("outlines", "pick", "reason"),
    [
        (set(), lambda context: [], DOCUMENT_NO_OUTLINE),
        ({DOC_A}, lambda context: RuntimeError("model down"), SKIP_SELECTION_ERROR),
        ({DOC_A}, lambda context: "not json", SKIP_SELECTION_ERROR),
        ({DOC_A}, lambda context: [], SKIP_NO_SELECTION),
        ({DOC_A}, lambda context: ["S9999", "x"], SKIP_UNKNOWN_IDS),
    ],
    ids=["no-outline", "model-error", "invalid-output", "nothing-selected", "all-ids-unknown"],
)
async def test_reads_nothing_when_no_section_is_selected(
    monkeypatch: pytest.MonkeyPatch, outlines: set[str], pick: Any, reason: str
) -> None:
    """目次が無い・選べない・何も選ばない・ID がすべて目次に無いときは、参照先を何も読まない。

    文書全体を読む退避も、文書の中の検索もしない。理由は受領記録に残す(#5348)。
    """
    engine, oracle, sections, llm = _setup(
        monkeypatch, outlines=outlines, targets=[TARGET_A], pick=pick
    )

    result, state = await _search(engine)

    _assert_read_nothing(result, state, reason)
    assert oracle.search_calls == []
    assert sections.read_calls == []
    assert len(llm.calls) == (0 if reason == DOCUMENT_NO_OUTLINE else 1)


async def test_reads_nothing_when_the_outline_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目次を読めなければ、その参照先を読まず理由を残す(モデルも呼ばない)。"""
    engine, oracle, _sections, llm = _setup(
        monkeypatch, outlines={DOC_A}, targets=[TARGET_A], pick=lambda context: []
    )

    async def broken(document_id: str, **kwargs: Any) -> None:
        raise RuntimeError("outline down")

    monkeypatch.setattr(section_map_service, "section_outline", broken)

    result, state = await _search(engine)

    _assert_read_nothing(result, state, DOCUMENT_OUTLINE_ERROR)
    assert oracle.search_calls == [] and llm.calls == []


async def test_selected_sections_that_read_nothing_add_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選んだ章節がどれも読めなければ、何も足さず理由を残す(文書全体を読まない)。"""
    engine, oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: [_label(context, "操作の手順")],
    )
    sections.empty_sections.add(f"{DOC_A}#操作の手順")

    result, state = await _search(engine)

    read = _assert_read_nothing(result, state, SKIP_SECTIONS_EMPTY)
    assert read["selected_sections"] == []
    assert sections.read_calls == [(DOC_A, f"{DOC_A}#操作の手順")]
    assert oracle.search_calls == []


async def test_only_selected_document_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 回の呼び出しで片方の文書だけ選んだら、選んだ文書だけを読み、もう片方は読まない。"""
    engine, oracle, sections, llm = _setup(
        monkeypatch,
        outlines={DOC_A, DOC_B},
        pick=lambda context: [_label(context.split("</document>")[0], "操作の手順")],
    )

    result, state = await _search(engine)

    assert len(llm.calls) == 1
    assert [child.chunk_uid for child in result.expansion_chunks] == [f"{DOC_A}:cs:body"]
    reads = _reads(state)
    assert (reads[DOC_A]["read_mode"], reads[DOC_A]["skip_reason"]) == (READ_MODE_SECTIONS, None)
    assert (reads[DOC_B]["read_mode"], reads[DOC_B]["skip_reason"]) == (None, SKIP_NO_SELECTION)
    assert sections.read_calls == [(DOC_A, f"{DOC_A}#操作の手順")]
    assert oracle.search_calls == []


async def test_unplanned_document_reference_reads_nothing() -> None:
    """読む前の計画が無い参照先(章節を選んでいない)は読まず、理由を残す。"""
    chunks = _doc_chunks(DOC_A, FILE_A, 2)
    oracle = ReferenceOracle([], chunks)
    oracle.documents = [(DOC_A, FILE_A, ())]
    engine = _engine(oracle, rag_reference_outline_selection_enabled=True)
    state = _SearchState()
    anchor = _retrieved("doc-index:a", "資料を参照。", "索引", document_id="doc-index")

    rows = await engine._document_reference_chunks({}, state, anchor, TARGET_A, key=("k",))

    assert rows == [] and oracle.search_calls == []
    assert state.reference_reads[("k",)].skip_reason == SKIP_NOT_PLANNED


async def test_numbered_reference_does_not_use_the_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """番号つきの参照(第 3 章)は今どおり番号で定位し、目次もモデルも使わない。"""
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _section_chunks(2))
    sections = SectionMap([], outlines=set())
    monkeypatch.setattr(section_map_service, "section_outline", sections.outline)
    engine = _engine(oracle, rag_reference_outline_selection_enabled=True)
    llm = FakeLlm(lambda context: [])
    engine._reference_llm = llm
    state = _SearchState()

    result = await search_with_expansions(
        engine, SearchRequest(query="q"), state, retrieval_queries=["q"]
    )

    assert [child.chunk_uid for child in result.expansion_chunks] == [
        "doc-1:cs-1:s0",
        "doc-1:cs-1:s1",
    ]
    assert sections.outline_calls == []
    assert llm.calls == []
    assert state.reference_reads == {}


def _numbered_section(*children: tuple[str, int], lead: int = 0) -> list[RetrievedChunk]:
    """番号で定位する参照先の節(「第3章 承認」)の合成の chunk。``lead`` は節そのものの本文の
    chunk の数、``children`` は (子の節の見出し, chunk の数)。本文は 1 chunk = 10 文字。"""
    rows = [
        _retrieved(f"doc-1:cs-1:lead{n}", f"第3章の前書き{n:03d}", "第3章 承認")
        for n in range(lead)
    ]
    for title, count in children:
        rows.extend(
            _retrieved(
                f"doc-1:cs-1:{title}:{n}",
                f"本文{title[:3]}{n:03d}".ljust(10, "。"),
                f"第3章 承認 > {title}",
            )
            for n in range(count)
        )
    return rows


@pytest.mark.parametrize("enabled", [True, False], ids=["outline-on", "outline-off"])
async def test_numbered_reference_reads_the_whole_section_within_the_limit(enabled: bool) -> None:
    """番号つきの参照の節は、文字数が回答のモデルの入力の上限の 1/2 以下なら節の全体を読む(#5817)。

    chunk の数では切らない(以前の既定の 8 chunk を超えても読む)。目次の設定に関わらない。
    """
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _section_chunks(30))
    engine = _engine(oracle, rag_reference_outline_selection_enabled=enabled)
    state = _SearchState()

    result = await search_with_expansions(
        engine, SearchRequest(query="q"), state, retrieval_queries=["q"]
    )

    assert [child.chunk_uid for child in result.expansion_chunks] == [
        f"doc-1:cs-1:s{n}" for n in range(30)
    ]
    # 節の全体を読む(読取の件数の上限を渡さない)。
    assert [call.get("limit", "unset") for call in oracle.reference_calls] == [None]
    (record,) = state.reference_sources
    assert record["section_limit_chars"] == engine._outline_limit_chars()
    (read,) = cast(list[dict[str, Any]], record["section_reads"])
    assert read["label"] == "第3章"
    assert read["section_path"] == "第3章 承認"
    assert read["limit_chars"] == engine._outline_limit_chars()
    assert read["chunk_count"] == read["read_chunk_count"] == 30
    assert read["shrunk_sections"] == read["unread_sections"] == []


async def test_numbered_reference_over_the_limit_reads_child_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """節が上限を超えれば子の節に縮め、子も超えればさらに下へ。超える葉の節は読まず診断に残す(#5817)。"""
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    rows = _numbered_section(("3.1 申請", 3), ("3.2 決裁", 6), lead=1)
    oracle = ReferenceOracle([anchor], rows)
    engine = _engine(oracle)
    # 上限 40 字: 節の全体(100 字)と「3.2 決裁」(60 字)は超え、「3.1 申請」(30 字)は入る。
    monkeypatch.setattr(engine, "_outline_limit_chars", lambda: 40)
    state = _SearchState()

    result = await search_with_expansions(
        engine, SearchRequest(query="q"), state, retrieval_queries=["q"]
    )

    # 節そのものの前書きと、超える葉の節「3.2 決裁」は読まない。
    assert [child.chunk_uid for child in result.expansion_chunks] == [
        f"doc-1:cs-1:3.1 申請:{n}" for n in range(3)
    ]
    (record,) = state.reference_sources
    (read,) = cast(list[dict[str, Any]], record["section_reads"])
    assert read["chars"] == 100
    assert read["read_chunk_count"] == 3
    assert read["read_chars"] == 30
    assert [item["section_id"] for item in read["shrunk_sections"]] == ["第3章 承認"]
    assert read["unread_sections"] == [
        {
            "section_id": "第3章 承認 > 3.2 決裁",
            "title": "3.2 決裁",
            "chars": 60,
            "reason": "over_limit",
        }
    ]


async def test_numbered_reference_leaf_over_the_limit_is_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子の節を持たずに上限を超える節は読まず、参照先の理由を section_over_limit にする(#5817)。"""
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _numbered_section(lead=5))
    engine = _engine(oracle)
    monkeypatch.setattr(engine, "_outline_limit_chars", lambda: 40)
    state = _SearchState()

    result = await search_with_expansions(
        engine, SearchRequest(query="q"), state, retrieval_queries=["q"]
    )

    assert result.expansion_chunks == []
    (record,) = state.reference_sources
    (read,) = cast(list[dict[str, Any]], record["section_reads"])
    assert read["read_chunk_count"] == 0
    assert [item["reason"] for item in read["unread_sections"]] == ["over_limit"]
    outcomes = [
        outcome
        for source in cast(list[dict[str, Any]], record["sources"])
        for outcome in cast(list[str], source.get("target_outcomes", []))
    ]
    assert outcomes == [SKIP_SECTION_OVER_LIMIT]


def test_fit_reference_section_builds_the_tree_from_heading_paths() -> None:
    """見出しの列から節の木を作り、選んだ章節と同じ規則(``fit_selected_sections``)で縮める(#5817)。"""
    root = ("第3章",)
    rows = [
        (("第3章",), 5),
        (("第3章", "3.1"), 10),
        (("第3章", "3.1", "(1)"), 10),
        (("第3章", "3.2"), 30),
        (("第3章", "3.2", "(1)"), 20),
        (("第3章", "3.2", "(2)"), 40),
        (("第4章",), 99),
    ]

    whole = fit_reference_section(root, rows, 200)
    assert whole.chars == 115
    assert whole.indexes == [0, 1, 2, 3, 4, 5]
    assert whole.fitted.section_ids == ["第3章"]

    shrunk = fit_reference_section(root, rows, 30)
    # 3.1(20 字)は全体、3.2(90 字)は子へ。3.2 の前書き(30 字)は読まず、(2)(40 字)は超える葉。
    assert shrunk.fitted.section_ids == ["第3章 > 3.1", "第3章 > 3.2 > (1)"]
    assert shrunk.indexes == [1, 2, 4]
    assert [item["section_id"] for item in shrunk.fitted.shrunk] == ["第3章", "第3章 > 3.2"]
    assert [item["section_id"] for item in shrunk.fitted.unread] == ["第3章 > 3.2 > (2)"]

    assert fit_reference_section(("第9章",), rows, 30).indexes == []


def test_resolve_choice_keeps_only_catalog_ids_once() -> None:
    plan = DocumentReadPlan(DOC_A, "cs", FILE_A, sections=[_node("x1", "一"), _node("x2", "二")])
    catalog = build_catalog([plan], [["一", "二"]], [0])

    result = resolve_choice(
        ReferenceSectionChoice(section_ids=["Ｓ２", "s2", "[S1]", "S3", "一"]), catalog
    )

    assert result.selected == {0: ["x2", "x1"]}
    assert result.discarded == ["S3", "一"]
    assert normalized_section_label(" [ｓ１] ") == "S1"


def test_without_descendants_reads_the_parent_once() -> None:
    nodes = [_node("p", "親"), _node("c", "子", parent="p", level=2), _node("g", "孫", parent="c")]

    assert without_descendants(nodes, ["c", "p", "g"]) == ["p"]
    assert without_descendants(nodes, ["g"]) == ["g"]


def test_prompt_uses_steps_and_schema_holds_the_field_meaning() -> None:
    assert REFERENCE_SECTION_PROMPT.startswith("## Step 1: ")
    assert "## Step 2: " in REFERENCE_SECTION_PROMPT
    schema = ReferenceSectionChoice.model_json_schema()
    assert schema["properties"]["section_ids"]["description"]
    assert schema.get("additionalProperties") is False


def test_limits_default_to_the_previous_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """目次の字数・読む章節の数・読む chunk の上限の設定は無い(#5816。入力の上限の 1/2 で決める)。

    配備済みの .env に残った削除した設定は読み捨てる(起動を止めない)。
    """
    from app.config import Settings

    removed = (
        "rag_reference_outline_selection_catalog_max_chars",
        "rag_reference_outline_selection_max_sections",
        "rag_reference_outline_selection_read_max_chunks",
        "rag_outline_selection_catalog_max_chars",
        "rag_outline_selection_read_max_chars",
        # 番号つきの参照の節から読む chunk の上限(#5348)も削除した(節の大きさで読む。#5817)。
        "rag_reference_section_read_max_chunks",
    )
    for name in removed:
        monkeypatch.setenv(name.upper(), "12")
    settings = Settings(_env_file=None)
    assert not any(hasattr(settings, name) for name in removed)
    # 目次の行の書き出しの字数も、設定にする前の定数と同じ(#5345)。
    assert settings.rag_outline_lead_chars == 60


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [
        ("rag_outline_lead_chars", 0, 200),
    ],
)
def test_limits_reject_values_out_of_range(name: str, low: int, high: int) -> None:
    from pydantic import ValidationError

    from app.config import Settings

    def build(value: int) -> Settings:
        values: dict[str, Any] = {name: value}
        return Settings(_env_file=None, **values)

    assert getattr(build(low), name) == low
    assert getattr(build(high), name) == high
    for invalid in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            build(invalid)


async def test_catalog_limit_is_half_of_the_answer_model_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目次の字数の上限は回答のモデルの入力の上限の 1/2(#5816。起点の文書の選択と同じ)。

    目次の全体の行を作ってから参照先に分け、入らない分は載せない(章節が 1 段なら先頭から入る分)。
    """
    from app.config import enterprise_ai_default_model_id, enterprise_ai_max_input_tokens
    from app.rag import section_catalog

    limits: list[int] = []
    leads: list[int] = []
    original = section_catalog.compact_section_lines

    async def recording(*args: Any, max_chars: int, **kwargs: Any) -> tuple[list[str], int]:
        limits.append(max_chars)
        leads.append(args[3])
        return await original(*args, max_chars=max_chars, **kwargs)

    monkeypatch.setattr(section_catalog, "compact_section_lines", recording)
    engine, _oracle, _sections, _llm = _setup(
        monkeypatch, outlines={DOC_A, DOC_B}, pick=lambda context: []
    )
    settings = engine._settings
    assert engine._outline_limit_chars() == (
        enterprise_ai_max_input_tokens(settings, enterprise_ai_default_model_id(settings)) // 2
    )

    _result, state = await _search(engine)

    # 目次の行は字数で切らずに作り、上限は 2 つの参照先の合計に掛ける。
    assert limits == [2**31 - 1, 2**31 - 1]
    # 目次の行の書き出しの字数は既定の設定(設定にする前の定数と同じ 60。#5345)。
    assert leads == [60, 60]
    (whole,) = _selections(state)
    assert whole["catalog_omitted_sections"] == 0

    engine, _oracle, _sections, _llm = _setup(
        monkeypatch, outlines={DOC_A, DOC_B}, pick=lambda context: []
    )
    monkeypatch.setattr(engine, "_outline_limit_chars", lambda: 100)
    _result, state = await _search(engine)
    (small,) = _selections(state)
    assert small["catalog_omitted_sections"] > 0
    assert small["catalog_chars"] < whole["catalog_chars"]


async def test_catalog_lead_chars_follow_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """目次の行に添える本文の書き出しの字数は設定 rag_outline_lead_chars の値(#5345)。"""
    from app.rag import section_catalog

    leads: list[int] = []
    original = section_catalog.compact_section_lines

    async def recording(*args: Any, **kwargs: Any) -> tuple[list[str], int]:
        leads.append(args[3])
        return await original(*args, **kwargs)

    monkeypatch.setattr(section_catalog, "compact_section_lines", recording)
    engine, _oracle, _sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: [],
        rag_outline_lead_chars=7,
    )

    await _search(engine)

    assert leads == [7]


async def test_every_selected_section_is_read_without_count_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選んだ章節は、章節の数・chunk の数で切らずにすべて読む(#5816。旧 12 章節・400 chunk)。"""

    def pick_all(context: str) -> list[str]:
        return [
            line.split("[", 1)[1].split("]", 1)[0] for line in context.splitlines() if "[S" in line
        ]

    engine, _oracle, sections, _llm = _setup(
        monkeypatch, outlines={DOC_A}, targets=[TARGET_A], pick=pick_all
    )
    result, state = await _search(engine)
    # 合成の文書は 31 章節(1 章節に 1 chunk)。
    assert len(sections.read_calls) == 31
    assert len(result.expansion_chunks) == 31
    assert _reads(state)[DOC_A]["read_chunk_count"] == 31


async def test_selected_section_over_the_limit_is_shrunk_to_its_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入力の上限の 1/2 を超える章節は子の章節に縮め、超える葉の章節は読まない(#5816)。"""
    engine, _oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: [_label(context, "手順の章")],
    )
    original = sections.sections

    def nested(document_id: str) -> list[Any]:
        nodes = original(document_id)
        parent = f"{document_id}#手順の章"
        small, large = f"{document_id}#操作の手順", f"{document_id}#設定項目 0"
        changed = [
            replace(
                node,
                parent_section_id=parent,
                level=2,
                chars=40 if node.section_id == small else 500,
            )
            if node.section_id in (small, large)
            else node
            for node in nodes
        ]
        head = _node(parent, "手順の章")
        return [changed[0], replace(head, chars=540, chunk_count=2), *changed[1:]]

    monkeypatch.setattr(sections, "sections", nested)
    monkeypatch.setattr(engine, "_outline_limit_chars", lambda: 100)

    result, state = await _search(engine)

    # 手順の章(540 字)は 1/2(100 字)を超えるので子の章節に縮め、操作の手順(40 字)だけを読む。
    assert sections.read_calls == [(DOC_A, f"{DOC_A}#操作の手順")]
    assert [child.chunk_uid for child in result.expansion_chunks] == [f"{DOC_A}:cs:body"]
    read = _reads(state)[DOC_A]
    assert [item["section_id"] for item in read["shrunk_sections"]] == [f"{DOC_A}#手順の章"]
    assert read["unread_sections"] == [
        {
            "section_id": f"{DOC_A}#設定項目 0",
            "title": "設定項目 0",
            "chars": 500,
            "reason": "over_limit",
        }
    ]


async def test_disabled_setting_reads_nothing_for_document_name_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定が無効なら、文書名だけの参照の参照先は何も読まない(#5348)。

    目次・モデル・文書の中の検索のどれも使わず(LLM・検索の呼び出しは増えない)、参照の記述の
    chunk はそのまま根拠に残る。理由は受領記録に残す。
    """
    from app.config import Settings

    assert Settings(_env_file=None).rag_reference_outline_selection_enabled is True
    engine, oracle, sections, llm = _setup(
        monkeypatch, outlines={DOC_A}, targets=[TARGET_A], pick=lambda context: [], enabled=False
    )

    result, state = await _search(engine)

    _assert_read_nothing(result, state, SKIP_OUTLINE_SELECTION_DISABLED)
    assert oracle.search_calls == []
    assert sections.outline_calls == [] and sections.read_calls == [] and llm.calls == []
    (record,) = state.reference_sources
    assert record["section_selections"] == []
