"""本文に拡張子つきのファイル名だけを書いた参照の発見・照合・補充(#5208)。

一覧の資料が「ファイル名だけの行」や「A と B を参照」で同じナレッジベースの別の文書を指すとき、
その文書を検索範囲(KB・権限・有効な版)の中のファイル名との完全一致で決め、参照の補充へ渡す。
参照先の文書は目次から選んだ章節で読み(#5263・#5348。ここでは代役が目次のすべての章節を選ぶ)、
読めた参照先は個数の上限で選ばずにすべて足し、原質問との rerank の点数の順に並べる(#5243)。
文書・語はすべて合成。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.answer_engine import _SearchState
from app.rag.cross_references import (
    annotate_cross_references,
    document_name_references,
    document_reference_rank,
    extract_references,
    reference_targets,
    same_document_title,
)
from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_cross_references import (
    FakeGenAi,
    ReferenceOracle,
    SectionMap,
    _chunk,
    _engine,
    _outline_engine,
    _retrieved,
)

_KB = "kb-synthetic"
_OTHER_KB = "kb-synthetic-other"
_QUESTION = "合成の作業の手順"


# ---- 発見(形式の解析) ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("参照先\n  手順A.txt\n  手順B.txt\n", ["手順A.txt", "手順B.txt"]),
        ("手順A.txt と 手順B.txt を参照する。", ["手順A.txt", "手順B.txt"]),
        ("・設定一覧-2.xlsx\n・setup_guide.v2.md", ["設定一覧-2.xlsx", "setup_guide.v2.md"]),
        ("詳しくは「手順A.txt」を参照。", ["手順A.txt"]),
        ("手順Ａ．ＴＸＴを見る", ["手順A.TXT"]),
        ("(手順A.txt)、手順B.txt。", ["手順A.txt", "手順B.txt"]),
    ],
    ids=["list-lines", "and-sentence", "bullets", "quoted", "full-width", "punctuation"],
)
def test_document_name_references_find_explicit_file_names(text: str, expected: list[str]) -> None:
    """区切り(行頭・空白・括弧・句読点)の間にある拡張子つきのファイル名を文書の参照にする。"""
    specs = document_name_references(text)

    assert [spec.document_title for spec in specs] == expected
    assert all(spec.kind == "document" and spec.key == "" for spec in specs)
    # 取込と同じ抽出(``extract_references``)も同じ参照を返す。
    assert [
        spec.document_title for spec in extract_references(text) if spec.kind == "document"
    ] == expected


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/files/guide.txt を参照",
        "C:\\work\\guide.txt を参照",
        "/var/data/guide.txt を参照",
        "連絡は user@example.com へ",
        "v1.2 と 3.5 の差分を参照",
        "e.g. 次の表を参照",
        "a.txt を参照",
    ],
    ids=["url", "windows-path", "posix-path", "email", "numbers", "abbr", "short"],
)
def test_document_name_references_ignore_paths_urls_and_unbounded_names(text: str) -> None:
    """URL・パス・メール・番号・略語、短すぎる名前は拾わない。

    外部の URL や任意のローカルのパスを参照先として扱わない(検索範囲の文書だけを照合する)。
    """
    assert document_name_references(text) == []


def test_unbounded_name_in_prose_is_not_cut_by_word_rules() -> None:
    """区切りの無い和文の中では名前の始まりを語の判定で切り出さない(区切りまでの全体のまま)。

    全体がファイル名に一致しないので、どの文書にも当たらない(範囲に無い参照として扱う)。
    """
    (spec,) = document_name_references("詳細は手順A.txtを参照")

    assert spec.document_title == "詳細は手順A.txt"
    assert document_reference_rank(spec.document_title, "手順A.txt") is None


# ---- 照合(ファイル名の完全一致) ----


@pytest.mark.parametrize(
    ("name", "file_name", "expected"),
    [
        ("手順A.txt", "手順A.txt", 0),
        ("手順a.TXT", "手順A.txt", 0),
        ("手順Ａ.txt", "手順A.txt", 0),
        ("手順A.txt", "手順A.pdf", None),
        ("手順A.txt", "手順A(旧).txt", None),
        ("手順A.txt", "サンプル社 手順A.txt", None),
        ("手順A.txt", "手順A_v2.txt", None),
        ("手順A.txt", "作業手順A.txt", None),
    ],
    ids=[
        "exact",
        "case",
        "nfkc",
        "other-extension",
        "parenthetical",
        "company-prefix",
        "edition",
        "longer-name",
    ],
)
def test_file_name_reference_matches_only_exact_file_name(
    name: str, file_name: str, expected: int | None
) -> None:
    """ファイル名の形の参照は、別名・部分一致でなくファイル名の完全一致だけで文書に当てる。"""
    assert document_reference_rank(name, file_name) == expected


def test_document_name_reference_keeps_alias_rules() -> None:
    """ファイル名の形でない文書名は、従来の別名の規則(``document_name_match``)のまま。"""
    assert document_reference_rank("定期保守計画", "plan.pdf", ["定期保守計画 2026年度"]) == 0
    assert document_reference_rank("保守計画", "plan.pdf", ["定期保守計画 2026年度"]) is None


def test_same_document_title_compares_file_names_exactly() -> None:
    """ファイル名の形の参照が参照元自身かは、ファイル名の一致で決める(包含で決めない)。"""
    assert same_document_title("一覧.xlsx", "一覧.xlsx")
    assert same_document_title("一覧.xlsx", "一覧")
    assert not same_document_title("一覧詳細.txt", "一覧.xlsx")
    assert not same_document_title("一覧.txt", "一覧.xlsx")


def test_annotate_keeps_file_name_references_unresolved_for_query_time() -> None:
    """取込は他のファイルへの参照を未解決の文書の参照として残し、自分のファイル名は残さない。"""
    chunks = [_chunk(0, "参照先\n  一覧.xlsx\n  手順A.txt", "参照先")]

    annotate_cross_references(chunks, document_title="一覧.xlsx")

    assert [
        (target.kind, target.document_title, target.section_path)
        for target in reference_targets(chunks[0].metadata)
    ] == [("document", "手順A.txt", None)]


def test_scoped_resolution_uses_exact_file_name_and_reports_ambiguity() -> None:
    """文書構造の参照の記録も同じ規則(完全一致・同名は曖昧・別名は当てない)。"""
    from app.rag.cross_references import ReferenceSpec

    spec = ReferenceSpec("手順A.txt", "document", "", "手順A.txt")
    source = ReferenceDocument("doc-list", "cs-list", "一覧.xlsx", ())
    exact = ReferenceDocument("doc-a", "cs-a", "手順A.txt", ())
    alias = ReferenceDocument("doc-a-old", "cs-old", "手順A(旧).txt", ())
    copy = ReferenceDocument("doc-a-copy", "cs-copy", "手順A.txt", ())

    resolved = resolve_scoped_reference(
        spec, source=source, citing_path="", documents=(exact, alias)
    )
    assert resolved.status == "resolved"
    assert resolved.target is not None and resolved.target.document_id == "doc-a"
    assert (
        resolve_scoped_reference(spec, source=source, citing_path="", documents=(alias,)).status
        == "not_found"
    )
    assert (
        resolve_scoped_reference(
            spec, source=source, citing_path="", documents=(exact, copy)
        ).status
        == "ambiguous"
    )


def test_reference_records_skip_file_names_outside_catalog() -> None:
    """文書構造の参照の記録も、範囲の文書に当たったファイル名だけを残す(範囲に無いものは捨てる)。"""
    from app.rag.chunking import Chunk
    from app.rag.reference_records import build_reference_records

    source = ReferenceDocument("doc-list", "cs-list", "一覧.xlsx", ("作業の一覧",))
    chunks = [
        Chunk(
            text="作業の参照先\n  手順A.txt\n  未登録.txt\n",
            index=0,
            start_offset=0,
            end_offset=20,
            metadata={"section_path": "作業の一覧"},
        )
    ]
    target = ReferenceDocument("doc-a", "cs-a", "手順A.txt", ("手順1",))

    records = build_reference_records(
        knowledge_base_id="kb", source=source, chunks=chunks, documents=(target,)
    )

    assert [(record.label, record.status, record.target_document_id) for record in records] == [
        ("手順A.txt", "resolved", "doc-a")
    ]


# ---- 回答の検索での補充 ----


def _list_chunk() -> RetrievedChunk:
    return _retrieved(
        "doc-list:cs-list:0",
        "作業の参照先\n  手順A.txt\n  手順B.txt\n",
        "作業の一覧",
        document_id="doc-list",
        file_name="一覧.xlsx",
        chunk_set_id="cs-list",
    )


def _procedure(
    document_id: str, file_name: str, chunk_set_id: str, body: str, count: int = 2
) -> list[RetrievedChunk]:
    return [
        _retrieved(
            f"{document_id}:{chunk_set_id}:{index}",
            f"{body}の手順{index + 1}",
            f"{file_name} > 手順{index + 1}",
            document_id=document_id,
            file_name=file_name,
            chunk_set_id=chunk_set_id,
        )
        for index in range(count)
    ]


def _corpus() -> list[RetrievedChunk]:
    return [
        *_procedure("doc-a", "手順A.txt", "cs-a", "作業Aの操作"),
        *_procedure("doc-b", "手順B.txt", "cs-b", "作業Bの操作"),
        # 別のナレッジベースにある同じ名前の文書(範囲の外)。
        *_procedure("doc-b-copy", "手順B.txt", "cs-b-copy", "範囲外の写し"),
        # 名前が似ているだけの文書(別名・部分一致では当てない)。
        *_procedure("doc-a-old", "手順A(旧).txt", "cs-a-old", "旧版の写し"),
    ]


async def _expand(
    monkeypatch: pytest.MonkeyPatch,
    oracle: ReferenceOracle,
    anchors: list[RetrievedChunk],
    genai: FakeGenAi | None = None,
    **settings: Any,
) -> tuple[dict[str, list[RetrievedChunk]], dict[str, Any], _SearchState, SectionMap]:
    state = _SearchState()
    receipt: dict[str, Any] = {}
    engine, sections, _llm = _outline_engine(monkeypatch, oracle, genai, **settings)
    result = await engine._reference_expansion(
        SearchRequest(query=_QUESTION, filters={"knowledge_base_id": _KB}),
        state,
        anchors,
        existing={anchor.chunk_id for anchor in anchors},
        question=(_QUESTION, [0.1] * 4),
        receipt=receipt,
    )
    return result, receipt, state, sections


def _read_documents(sections: SectionMap) -> set[str]:
    """目次から選んだ章節を読んだ文書。"""
    return {document_id for document_id, _section in sections.read_calls}


def _statuses(receipt: dict[str, Any]) -> dict[str, str]:
    return {item["target"]["document_title"]: item["status"] for item in receipt["targets"]}


async def test_list_of_bare_file_names_adds_both_procedures_from_same_kb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧の chunk のファイル名だけの行から、同じ KB の手順 A・B の原文を参照先として足す。"""
    anchor = _list_chunk()
    oracle = ReferenceOracle(
        [anchor],
        _corpus(),
        knowledge_bases={
            _KB: {"doc-list", "doc-a", "doc-b", "doc-a-old"},
            _OTHER_KB: {"doc-b-copy"},
        },
    )

    result, receipt, state, sections = await _expand(monkeypatch, oracle, [anchor])

    added = [row.chunk_id for row in result[anchor.chunk_id]]
    assert added == ["doc-a:cs-a:0", "doc-a:cs-a:1", "doc-b:cs-b:0", "doc-b:cs-b:1"]
    assert _statuses(receipt) == {"手順A.txt": "available", "手順B.txt": "available"}
    assert receipt["sources"][0]["dependency_status"] == "satisfied"
    assert {item["resolved_at"] for item in state.reference_expansions.values()} == {"query"}
    # 範囲外の写し・名前が似ているだけの文書は読まない。
    assert _read_documents(sections) == {"doc-a", "doc-b"}
    assert oracle.search_calls == []


async def test_ingested_file_name_references_are_followed_without_requery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取込で残したファイル名の参照(未解決の文書の参照)も同じく辿る。"""
    staged = [_chunk(0, _list_chunk().text, "作業の一覧")]
    annotate_cross_references(staged, document_title="一覧.xlsx")
    anchor = _list_chunk().model_copy(
        update={"metadata": {**_list_chunk().metadata, **staged[0].metadata}}
    )
    oracle = ReferenceOracle(
        [anchor], _corpus(), knowledge_bases={_KB: {"doc-list", "doc-a", "doc-b"}}
    )

    result, receipt, state, sections = await _expand(monkeypatch, oracle, [anchor])

    assert {row.document_id for row in result[anchor.chunk_id]} == {"doc-a", "doc-b"}
    assert {item["resolved_at"] for item in state.reference_expansions.values()} == {"ingest"}


async def test_file_name_reference_distinguishes_ambiguous_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名が複数・有効な版が複数の文書は足さず、受領記録で理由を分ける(依存は満たさない)。"""
    corpus = [
        *_procedure("doc-a", "手順A.txt", "cs-a", "作業Aの操作"),
        *_procedure("doc-a2", "手順A.txt", "cs-a2", "同名の別文書"),
        *_procedure("doc-b", "手順B.txt", "cs-b1", "作業Bの版1", count=1),
        *_procedure("doc-b", "手順B.txt", "cs-b2", "作業Bの版2", count=1),
    ]
    anchor = _list_chunk()
    oracle = ReferenceOracle(
        [anchor], corpus, knowledge_bases={_KB: {"doc-list", "doc-a", "doc-a2", "doc-b"}}
    )

    result, receipt, _state, sections = await _expand(monkeypatch, oracle, [anchor])

    assert result == {}
    assert _statuses(receipt) == {
        "手順A.txt": "document_ambiguous",
        "手順B.txt": "document_version_unconfirmed",
    }
    assert receipt["sources"][0]["dependency_status"] == "unsatisfied"
    assert set(receipt["sources"][0]["dependency_reasons"]) == {
        "document_ambiguous",
        "document_version_unconfirmed",
    }
    assert set(receipt["stop_reasons"]) == {"document_ambiguous", "document_version_unconfirmed"}
    assert sections.outline_calls == []


async def test_file_name_outside_scope_is_not_a_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """範囲に無いファイル名(未登録・KB や権限の外)は参照として数えず、ほかの参照先は足す。

    範囲の外と未登録は、存在を漏らさないよう同じ「範囲に無い」として受領記録に残す。
    """
    anchor = _list_chunk().model_copy(update={"text": "作業の参照先\n  手順A.txt\n  手順C.txt\n"})
    corpus = [
        *_procedure("doc-a", "手順A.txt", "cs-a", "作業Aの操作"),
        *_procedure("doc-c", "手順C.txt", "cs-c", "範囲外"),
    ]
    oracle = ReferenceOracle(
        [anchor], corpus, knowledge_bases={_KB: {"doc-list", "doc-a"}, _OTHER_KB: {"doc-c"}}
    )

    result, receipt, _state, sections = await _expand(monkeypatch, oracle, [anchor])

    assert [row.chunk_id for row in result[anchor.chunk_id]] == ["doc-a:cs-a:0", "doc-a:cs-a:1"]
    assert _statuses(receipt) == {"手順A.txt": "available", "手順C.txt": "document_not_found"}
    assert receipt["sources"][0]["dependency_status"] == "satisfied"
    assert receipt["sources"][0]["dependency_reasons"] == []
    assert receipt["stop_reasons"] == ["declared_source_range_complete"]
    assert _read_documents(sections) == {"doc-a"}


async def test_unknown_file_name_does_not_block_section_reference() -> None:
    """本文のファイル名らしい語(範囲に無い)で、同じ chunk の章の参照の補充を止めない。"""
    from tests.test_cross_references import CHAPTER3

    source = _retrieved(
        "doc-1:cs-1:a",
        "承認は第3章を参照。設定は config.yaml に書く。",
        "第1章 総則",
        targets=[CHAPTER3],
    )
    chapter = [_retrieved(f"doc-1:cs-1:c{n}", "承認の本文", "第3章 承認") for n in range(2)]
    oracle = ReferenceOracle([source], chapter)
    state = _SearchState()
    receipt: dict[str, Any] = {}

    result = await _engine(oracle)._reference_expansion(
        SearchRequest(query=_QUESTION),
        state,
        [source],
        existing={source.chunk_id},
        question=(_QUESTION, [0.1] * 4),
        receipt=receipt,
    )

    assert [row.chunk_id for row in result[source.chunk_id]] == ["doc-1:cs-1:c0", "doc-1:cs-1:c1"]
    assert receipt["sources"][0]["dependency_status"] == "satisfied"


async def test_file_name_references_respect_target_read_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読む参照先の数の上限(読取の安全の上限)は残す。読めた参照先は足し、読まなかった理由を残す。"""
    anchor = _list_chunk()
    oracle = ReferenceOracle(
        [anchor], _corpus(), knowledge_bases={_KB: {"doc-list", "doc-a", "doc-b"}}
    )

    result, receipt, _state, sections = await _expand(
        monkeypatch, oracle, [anchor], rag_reference_expansion_max_targets=1
    )

    # 一部の参照先を読めなくても、読めた参照先(手順 A)は足す(すべてか無しかにしない)。
    assert [row.chunk_id for row in result[anchor.chunk_id]] == ["doc-a:cs-a:0", "doc-a:cs-a:1"]
    assert _statuses(receipt) == {"手順A.txt": "available", "手順B.txt": "target_limit"}
    assert receipt["sources"][0]["dependency_status"] == "partial"
    assert receipt["sources"][0]["dependency_reasons"] == ["target_limit"]
    assert receipt["stop_reasons"] == ["target_read_limit"]


def _list_of(names: list[str]) -> RetrievedChunk:
    return _list_chunk().model_copy(
        update={"text": "作業の参照先\n" + "".join(f"  {name}\n" for name in names)}
    )


@pytest.mark.parametrize("count", [3, 4, 5], ids=["three", "four", "five"])
async def test_list_of_many_file_names_adds_every_document_in_relevance_order(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """範囲の文書に当たるファイル名を 3〜5 件並べた一覧の chunk から、全部の文書を足す(#5243)。

    個数の上限(旧 RAG_REFERENCE_EXPANSION_MAX_CHUNKS、既定 4)で依存群ごと捨てない。足す chunk は
    1 回の rerank で原質問との点数の高い順に並べ、点数を印に残す(回答の生成が予算に入れる順)。
    """
    from app.rag.cross_references import REFERENCE_SCORE_KEY

    letters = "ABCDE"[:count]
    names = [f"手順{letter}.txt" for letter in letters]
    corpus = [
        row
        for letter in letters
        for row in _procedure(
            f"doc-{letter.lower()}",
            f"手順{letter}.txt",
            f"cs-{letter.lower()}",
            f"作業{letter}の操作",
        )
    ]
    anchor = _list_of(names)
    oracle = ReferenceOracle(
        [anchor],
        corpus,
        knowledge_bases={_KB: {"doc-list", *(f"doc-{letter.lower()}" for letter in letters)}},
    )
    genai = FakeGenAi({"作業Cの操作": 0.9, "作業Aの操作": 0.5})

    result, receipt, state, sections = await _expand(monkeypatch, oracle, [anchor], genai)

    added = [row.chunk_id for row in result[anchor.chunk_id]]
    expected = [
        f"doc-{letter.lower()}:cs-{letter.lower()}:{index}"
        for letter in letters
        for index in (0, 1)
    ]
    assert sorted(added) == sorted(expected)
    # 点数の高い順(C → A)、同点は読んだ順。
    assert added[:4] == ["doc-c:cs-c:0", "doc-c:cs-c:1", "doc-a:cs-a:0", "doc-a:cs-a:1"]
    assert state.chunks["doc-c:cs-c:0"].metadata[REFERENCE_SCORE_KEY] == 0.9
    assert state.chunks["doc-b:cs-b:0"].metadata[REFERENCE_SCORE_KEY] == 0.0
    # 参照先ごとでなく、足す chunk をまとめて 1 回だけ rerank する。
    assert len(genai.rerank_calls) == 1
    assert len(genai.rerank_calls[0][1]) == 2 * count
    assert receipt["added_count"] == 2 * count
    assert receipt["ranking"] == "rerank"
    assert [item["relevance_rank"] for item in receipt["added_chunks"]] == list(
        range(1, 2 * count + 1)
    )
    assert receipt["sources"][0]["dependency_status"] == "satisfied"
    assert receipt["stop_reasons"] == ["declared_source_range_complete"]
    # 参照先ごとの受領記録に、その参照先から足した chunk(関連度の順)を残す。
    for item in receipt["targets"]:
        prefix = f"doc-{item['target']['document_title'][2].lower()}:"
        assert item["added_chunk_ids"] == [row for row in added if row.startswith(prefix)]
