"""検索の上位の文書の目次から回答のモデルが選んだ章節を、rerank の枠の外で根拠に足す(#5324)。

全体設定が有効なら、検索の上位の文書のうち章節の木を持つ文書の目次を回答のモデルに 1 回だけ渡し、
目次にある ID の章節を ``read_section`` で読んで検索拡張の chunk(``expansion_chunks``)として渡す。
無効なら今の挙動と同じで、LLM の呼び出しは増えない。fixture はすべて合成の内容。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from rag_engine.resources.deadline import Deadline

from app.config import Settings
from app.rag import section_map_service
from app.rag.answer_engine import _SearchState
from app.rag.reference_sections import (
    OUTLINE_SECTION_PROMPT,
    OUTLINE_SELECTION_KEY,
    READ_STOP_CHAR_LIMIT,
    REFERENCE_SECTION_PROMPT,
    SKIP_DEADLINE,
    SKIP_NO_OUTLINE,
    SKIP_SELECTION_ERROR,
    SKIP_UNKNOWN_IDS,
    ReferenceSectionChoice,
    allot_catalog_chars,
    fit_catalog_lines,
)
from app.rag.section_chunk_map import ROOT_SECTION_ID, SectionNode
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_cross_references import FakeGenAi, ReferenceOracle, _engine, _retrieved
from tests.test_reference_sections import FakeLlm, SectionMap, _label

QUESTION = "手続の期限の扱いを教えて"
TAIL = "補則"


def _document(document_id: str, count: int, *, body_chars: int = 20) -> list[RetrievedChunk]:
    """本文の章節が続き、最後の章節(本文の外の補則)に答えの本文がある合成の文書。"""
    rows = [
        _retrieved(
            f"{document_id}:cs:{n}",
            f"手続の説明 {n}。" + "あ" * body_chars,
            f"本文 {n}",
            document_id=document_id,
            file_name=f"{document_id}.md",
            chunk_set_id="cs",
        )
        for n in range(count)
    ]
    rows.append(
        _retrieved(
            f"{document_id}:cs:tail",
            "前の版の期限はこの補則の日から 30 日とする。",
            TAIL,
            document_id=document_id,
            file_name=f"{document_id}.md",
            chunk_set_id="cs",
        )
    )
    return rows


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    documents: tuple[str, ...] = ("doc-a",),
    outlines: set[str] | None = None,
    pick: Any = None,
    hits_per_document: int = 3,
    body_chars: int = 20,
    **settings: Any,
) -> tuple[Any, ReferenceOracle, SectionMap, FakeLlm]:
    chunks = [
        chunk
        for document_id in documents
        for chunk in _document(document_id, 6, body_chars=body_chars)
    ]
    # 検索の上位は本文の章節だけ(補則は検索の字面で当たらない)。
    hits = [
        chunk
        for document_id in documents
        for chunk in chunks
        if chunk.document_id == document_id and not chunk.chunk_id.endswith(":tail")
    ]
    hits = [
        hit
        for document_id in documents
        for hit in [h for h in hits if h.document_id == document_id][:hits_per_document]
    ]
    oracle = ReferenceOracle(hits, chunks)
    sections = SectionMap(chunks, outlines=set(documents) if outlines is None else outlines)
    monkeypatch.setattr(section_map_service, "section_outline", sections.outline)
    monkeypatch.setattr(section_map_service, "read_section", sections.read)
    settings.setdefault("rag_outline_selection_enabled", True)
    engine = _engine(oracle, FakeGenAi(), **settings)
    llm = FakeLlm(pick if pick is not None else lambda context: [_label(context, TAIL)])
    engine._reference_llm = llm
    return engine, oracle, sections, llm


async def _search(engine: Any, state: _SearchState | None = None) -> tuple[Any, _SearchState]:
    state = state or _SearchState()
    result = await engine._search(
        SearchRequest(query=QUESTION), state, retrieval_queries=[QUESTION]
    )
    return result, state


def _record(state: _SearchState) -> dict[str, Any]:
    assert state.outline_selection is not None
    return cast(dict[str, Any], state.outline_selection)


async def test_disabled_setting_adds_no_model_call_and_no_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既定は無効。目次も読まず、モデルも呼ばず、検索の結果は今と同じ。"""
    assert Settings(_env_file=None).rag_outline_selection_enabled is False
    engine, _oracle, sections, llm = _setup(monkeypatch, rag_outline_selection_enabled=False)

    result, state = await _search(engine)

    assert llm.calls == [] and sections.outline_calls == [] and sections.read_calls == []
    assert result.expansion_chunks == []
    assert [child.chunk_uid for child in result.child_chunks] == [
        "doc-a:cs:0",
        "doc-a:cs:1",
        "doc-a:cs:2",
    ]
    assert state.outline_selection is None


async def test_selected_section_is_added_outside_the_rerank_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選んだ章節は検索の候補(rerank の候補)に入れず、検索拡張の chunk として必ず渡す。"""
    engine, _oracle, sections, llm = _setup(monkeypatch)

    result, state = await _search(engine)

    assert [child.chunk_uid for child in result.child_chunks] == [
        "doc-a:cs:0",
        "doc-a:cs:1",
        "doc-a:cs:2",
    ]
    (added,) = result.expansion_chunks
    assert added.chunk_uid == "doc-a:cs:tail"
    assert added.metadata["retrieval_admission_stage"] == "outline_selection"
    # rerank の点数を付けない(rerank で並べ直さない)。
    assert "rerank" not in added.metadata
    assert added.metadata[OUTLINE_SELECTION_KEY] == {
        "document_id": "doc-a",
        "section_id": f"doc-a#{TAIL}",
        "title": TAIL,
    }
    assert sections.read_calls == [("doc-a", f"doc-a#{TAIL}")]
    (call,) = llm.calls
    assert call["prompt"] == QUESTION
    assert call["system_prompt"] == OUTLINE_SECTION_PROMPT
    assert call["response_schema"] == ReferenceSectionChoice.model_json_schema()
    assert call["response_schema_name"] == "outline_sections"
    assert '<document name="doc-a.md">' in call["context"]
    record = _record(state)
    assert record["skip_reason"] is None
    assert record["added_count"] == 1
    assert record["read_chars"] == len(added.text)
    (document,) = record["documents"]
    assert document["read_mode"] == "outline_selection"
    assert document["selected_sections"] == [{"section_id": f"doc-a#{TAIL}", "title": TAIL}]
    assert document["read_chars"] == len(added.text)
    assert record["selection"]["outcome"] == "selected"


async def test_selection_runs_once_per_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ回答の 2 回目の検索は選び直さず、同じ chunk を返す。"""
    engine, _oracle, _sections, llm = _setup(monkeypatch)

    first, state = await _search(engine)
    second, _ = await _search(engine, state)

    assert len(llm.calls) == 1
    assert [c.chunk_uid for c in second.expansion_chunks] == [
        c.chunk_uid for c in first.expansion_chunks
    ]


async def test_ids_not_in_the_outline_are_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _oracle, sections, _llm = _setup(
        monkeypatch, pick=lambda context: ["S999", "補則", f"[{_label(context, TAIL)}]"]
    )

    result, state = await _search(engine)

    assert [c.chunk_uid for c in result.expansion_chunks] == ["doc-a:cs:tail"]
    assert sections.read_calls == [("doc-a", f"doc-a#{TAIL}")]
    assert _record(state)["selection"]["discarded_ids"] == ["S999", "補則"]


@pytest.mark.parametrize(
    ("pick", "reason"),
    [
        (lambda context: ["S999"], SKIP_UNKNOWN_IDS),
        (lambda context: RuntimeError("model down"), SKIP_SELECTION_ERROR),
        (lambda context: "not json", SKIP_SELECTION_ERROR),
    ],
    ids=["all-ids-unknown", "model-error", "invalid-output"],
)
async def test_unusable_selection_adds_nothing(
    monkeypatch: pytest.MonkeyPatch, pick: Any, reason: str
) -> None:
    """選べないときは何も足さず(文書の全体も読まない)、今の経路で回答する。"""
    engine, oracle, sections, llm = _setup(monkeypatch, pick=pick)

    result, state = await _search(engine)

    assert result.expansion_chunks == []
    assert sections.read_calls == [] and oracle.search_calls == []
    assert len(llm.calls) == 1
    assert _record(state)["skip_reason"] == reason


async def test_short_deadline_skips_the_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """回答の期限の残りが選択と読取の予約に足りなければ、目次も読まずに飛ばす。"""
    engine, _oracle, sections, llm = _setup(monkeypatch)
    state = _SearchState()
    # 予約は生成の 1 回の見積もり(既定 120 秒)の 2 回分と確定の分。残り 200 秒では足りない。
    state.deadline = Deadline(200.0)

    result, state = await _search(engine, state)

    assert result.expansion_chunks == []
    assert llm.calls == [] and sections.outline_calls == []
    record = _record(state)
    assert record["skip_reason"] == SKIP_DEADLINE
    assert record["deadline_reserve_seconds"] > 200


async def test_enough_deadline_runs_the_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _oracle, _sections, llm = _setup(monkeypatch)
    state = _SearchState()
    state.deadline = Deadline(600.0)

    result, _ = await _search(engine, state)

    assert len(llm.calls) == 1
    assert [c.chunk_uid for c in result.expansion_chunks] == ["doc-a:cs:tail"]


async def test_document_without_section_tree_is_not_a_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """章節の木の無い文書は対象にしない。どれも無ければモデルを呼ばない。"""
    engine, _oracle, sections, llm = _setup(monkeypatch, outlines=set())

    result, state = await _search(engine)

    assert result.expansion_chunks == [] and llm.calls == []
    assert sections.outline_calls == ["doc-a"]
    record = _record(state)
    assert record["skip_reason"] == SKIP_NO_OUTLINE
    assert [d["fallback_reason"] for d in record["documents"]] == ["no_outline"]


async def test_max_documents_limits_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """対象は検索の上位の文書を順位の順に設定の数まで。目次は 1 回の呼び出しにまとめる。"""
    engine, _oracle, sections, llm = _setup(
        monkeypatch,
        documents=("doc-a", "doc-b", "doc-c"),
        outlines={"doc-a", "doc-c"},
        hits_per_document=1,
        rag_outline_selection_max_documents=2,
    )

    _result, state = await _search(engine)

    # doc-c は 3 番目なので対象の外。doc-b は章節の木が無いので数に入れたまま対象にしない。
    # (3 回目の doc-a は、選んだ章節を読む read_section の中の目次の読み込み。)
    assert sections.outline_calls[:2] == ["doc-a", "doc-b"]
    assert "doc-c" not in sections.outline_calls
    (call,) = llm.calls
    assert '<document name="doc-a.md">' in call["context"]
    assert "doc-b.md" not in call["context"] and "doc-c.md" not in call["context"]
    assert [d["document_id"] for d in _record(state)["documents"]] == ["doc-a", "doc-b"]


async def test_read_char_limit_stops_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """読む章節の字数の上限に達したら止め、読まなかった章節を記録する。"""

    def pick(context: str) -> list[str]:
        return [_label(context, "本文 4"), _label(context, "本文 5"), _label(context, TAIL)]

    engine, _oracle, _sections, _llm = _setup(
        monkeypatch, pick=pick, body_chars=1500, rag_outline_selection_read_max_chars=2000
    )

    result, state = await _search(engine)

    # 本文 4(約 1500 字)だけが入り、本文 5 は上限を超えるので読まない。
    assert [c.chunk_uid for c in result.expansion_chunks] == ["doc-a:cs:4"]
    record = _record(state)
    assert record["read_chars"] <= 2000
    assert record["read_stop_reason"] == READ_STOP_CHAR_LIMIT
    assert [item["section_id"] for item in record["unread_sections"]] == [
        "doc-a#本文 5",
        f"doc-a#{TAIL}",
    ]


async def test_selected_sections_are_read_in_the_returned_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _oracle, sections, _llm = _setup(
        monkeypatch, pick=lambda context: [_label(context, TAIL), _label(context, "本文 5")]
    )

    result, _state = await _search(engine)

    assert sections.read_calls == [("doc-a", f"doc-a#{TAIL}"), ("doc-a", "doc-a#本文 5")]
    assert {c.chunk_uid for c in result.expansion_chunks} == {"doc-a:cs:tail", "doc-a:cs:5"}


def _node(section_id: str, level: int) -> SectionNode:
    return SectionNode(section_id, section_id, level, 1, ROOT_SECTION_ID, None, None, chunk_count=1)


def test_catalog_drops_deep_levels_before_the_document_tail() -> None:
    """目次が上限に入らなければ深い階層から外し、文書の終わりの章節を残す。"""
    nodes = [_node("c1", 1), _node("c1-1", 2), _node("c1-2", 2), _node("tail", 1)]
    lines = ["c1", "  c1-1" + "x" * 40, "  c1-2" + "x" * 40, TAIL]

    assert fit_catalog_lines(nodes, lines, 1000) == [0, 1, 2, 3]
    assert fit_catalog_lines(nodes, lines, 20) == [0, 3]
    # いちばん浅い階層だけでも入らなければ、先頭から入る分だけ。
    assert fit_catalog_lines(nodes, lines, 3) == [0]


def test_catalog_chars_go_to_longer_documents() -> None:
    assert allot_catalog_chars([100, 5000, 5000], 4000) == [100, 1950, 1950]
    assert allot_catalog_chars([100, 200], 4000) == [100, 200]


def test_prompts_share_the_catalog_reading_and_use_steps() -> None:
    assert OUTLINE_SECTION_PROMPT.startswith("## Step 1: ")
    assert "## Step 2: " in OUTLINE_SECTION_PROMPT
    # 目次の読み方と章節の選び方は文書全体への参照と同じ文(何の目次かだけが違う)。
    shared = REFERENCE_SECTION_PROMPT.split("## Step 2: 読む章節を選ぶ\n", 1)[1]
    assert shared in OUTLINE_SECTION_PROMPT


@pytest.mark.parametrize(("configured", "expected"), [(None, 60), (9, 9)], ids=["default", "set"])
async def test_catalog_lead_chars_follow_the_setting(
    monkeypatch: pytest.MonkeyPatch, configured: int | None, expected: int
) -> None:
    """目次の行に添える本文の書き出しの字数は設定 rag_outline_lead_chars(既定 60。#5345)。

    文書全体への参照の目次の選択と同じ 1 つの設定を読む。
    """
    from app.rag import section_catalog

    leads: list[int] = []
    original = section_catalog.compact_section_lines

    async def recording(*args: Any, **kwargs: Any) -> tuple[list[str], int]:
        leads.append(args[3])
        return await original(*args, **kwargs)

    monkeypatch.setattr(section_catalog, "compact_section_lines", recording)
    settings: dict[str, Any] = {} if configured is None else {"rag_outline_lead_chars": configured}
    engine, _oracle, _sections, _llm = _setup(monkeypatch, **settings)

    await _search(engine)

    assert leads == [expected]
