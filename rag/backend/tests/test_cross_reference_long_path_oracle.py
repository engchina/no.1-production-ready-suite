"""検索の交差参照が、4000 バイトを超える section path の節を辿れるかを実 Oracle で確かめる(#4144)。

AI は決定論スタブ(oracle_db fixture)で、SQL だけを実 DB で評価する。文書・見出しは合成のもの。
"""

from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.chunking import Chunk
from app.rag.cross_references import annotate_cross_references
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi
from tests.test_answer_scope_oracle import _chunk, _indexed_document

_TARGET_HEADING = "第5章 準備"
_TARGET_TEXT = "合成の準備には項目Aを使います。"

# JSON_VALUE の既定の RETURNING VARCHAR2(4000) を超える 3 つの長さ。
_OVER_RETURNING_LIMIT = [
    pytest.param("合成の長い親階層" * 170, id="over_4000_bytes_under_4000_chars"),
    pytest.param("synthetic-long-parent-" * 230, id="over_4000_chars"),
    pytest.param("合成の長い親階層" * 1400, id="over_the_32k_bind_size"),
]


def _source_text(document_title: str | None) -> str:
    target = f"『{document_title}』の第5章" if document_title else "第5章"
    return f"詳細は{target}を参照。"


def _annotated(*chunks: Chunk) -> list[Chunk]:
    listed = list(chunks)
    annotate_cross_references(listed)
    return listed


async def _expand_from(
    oracle: OracleClient, scope: dict[str, str], source_id: str
) -> list[tuple[str, str, str]]:
    """起点の節の chunk から参照を辿り、足した chunk の(文書, 節, 本文)を返す。"""
    anchors = await oracle.retrieval_reference_chunks(
        scope, document_id=source_id, section_path="第1章 概要", limit=1
    )
    assert len(anchors) == 1
    engine = AnswerEngine(Settings(), oracle=oracle, genai=FakeGenAi())  # type: ignore[arg-type]
    added = await engine._reference_expansion(
        SearchRequest(query="合成の準備は？", filters=scope),
        _SearchState(chunks={anchors[0].chunk_id: anchors[0]}),
        anchors,
        existing={anchors[0].chunk_id},
    )
    return [
        (chunk.document_id, str(chunk.metadata["section_path"]), chunk.text)
        for chunk in added.get(anchors[0].chunk_id, [])
    ]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _OVER_RETURNING_LIMIT)
@pytest.mark.parametrize("annotate", [False, True], ids=["from_text", "from_ingest"])
async def test_same_document_reference_follows_long_target_path(
    parent: str, annotate: bool
) -> None:
    oracle = OracleClient()
    target_path = f"{parent} > {_TARGET_HEADING}"
    assert len(target_path.encode()) > 4000
    chunks = [
        _chunk(0, _source_text(None), "第1章 概要"),
        _chunk(1, _TARGET_TEXT, target_path),
    ]
    kb = await oracle.create_knowledge_base(name=f"合成参照 KB {uuid4().hex[:8]}")
    document_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-long-reference-{uuid4().hex[:8]}.pdf",
        chunks=_annotated(*chunks) if annotate else chunks,
        knowledge_base_ids=[kb.id],
    )

    assert await _expand_from(oracle, {"knowledge_base_id": kb.id}, document_id) == [
        (document_id, target_path, _TARGET_TEXT)
    ]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _OVER_RETURNING_LIMIT)
async def test_other_document_reference_follows_long_target_path(parent: str) -> None:
    oracle = OracleClient()
    token = uuid4().hex[:8]
    title = f"合成参照資料B{token}"
    target_path = f"{parent} > {_TARGET_HEADING}"
    kb = await oracle.create_knowledge_base(name=f"合成参照 KB {token}")
    source_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-long-reference-{token}.pdf",
        chunks=_annotated(_chunk(0, _source_text(title), "第1章 概要")),
        knowledge_base_ids=[kb.id],
    )
    target_id = await _indexed_document(
        oracle,
        file_name=f"{title}.pdf",
        chunks=[_chunk(0, _TARGET_TEXT, target_path)],
        knowledge_base_ids=[kb.id],
    )

    assert await _expand_from(oracle, {"knowledge_base_id": kb.id}, source_id) == [
        (target_id, target_path, _TARGET_TEXT)
    ]


@pytest.mark.usefixtures("oracle_db")
async def test_other_document_found_by_title_only_in_long_path() -> None:
    """文書名に無いタイトルが、長い見出しの列の先頭の見出しにだけあっても参照先の文書が決まる。"""
    oracle = OracleClient()
    token = uuid4().hex[:8]
    title = f"合成参照資料B{token}"
    target_path = f"{title} > {'合成の長い親階層' * 170} > {_TARGET_HEADING}"
    kb = await oracle.create_knowledge_base(name=f"合成参照 KB {token}")
    source_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-long-reference-{token}.pdf",
        chunks=_annotated(_chunk(0, _source_text(title), "第1章 概要")),
        knowledge_base_ids=[kb.id],
    )
    target_id = await _indexed_document(
        oracle,
        file_name=f"opaque-{token}.pdf",
        chunks=[_chunk(0, _TARGET_TEXT, target_path)],
        knowledge_base_ids=[kb.id],
    )

    assert await _expand_from(oracle, {"knowledge_base_id": kb.id}, source_id) == [
        (target_id, target_path, _TARGET_TEXT)
    ]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _OVER_RETURNING_LIMIT)
async def test_screen_sections_keep_long_paths_in_reading_order(parent: str) -> None:
    client = OracleClient()
    token = uuid4().hex[:12]
    long_path = f"{parent} > 第2章"
    first, second = f"sections-{token}-a.pdf", f"sections-{token}-b.pdf"
    await _indexed_document(
        client,
        file_name=first,
        chunks=[
            _chunk(0, "前", "第1章"),
            _chunk(1, "長い節の 1", long_path),
            _chunk(2, "長い節の 2", long_path),
            _chunk(3, "後", "第3章"),
            _chunk(4, "見出しの無い本文"),
        ],
    )
    await _indexed_document(client, file_name=second, chunks=[_chunk(0, "別", "第1章")])

    assert await client.retrieval_screen_sections({"file_name": f"sections-{token}"}) == [
        (first, "第1章", 1),
        (first, long_path, 2),
        (first, "第3章", 1),
        (second, "第1章", 1),
    ]


@pytest.mark.usefixtures("oracle_db")
async def test_screen_sections_do_not_add_a_query_without_long_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OracleClient()
    token = uuid4().hex[:12]
    await _indexed_document(
        client,
        file_name=f"queries-{token}.pdf",
        chunks=[
            _chunk(0, "短い節", "第1章"),
            _chunk(1, "3999 バイトの節", "合" * 1333),
            _chunk(2, "4000 バイトの節", "合" * 1333 + "a"),
            _chunk(3, "見出しの無い本文"),
        ],
    )
    fetch_all = client._fetch_all
    statements: list[str] = []

    async def counting(statement: str, binds: dict[str, object] | None = None) -> object:
        statements.append(statement)
        return await fetch_all(statement, binds)

    monkeypatch.setattr(client, "_fetch_all", counting)
    sections = await client.retrieval_screen_sections({"file_name": f"queries-{token}"})

    assert [(path, count) for _name, path, count in sections] == [
        ("第1章", 1),
        ("合" * 1333, 1),
        ("合" * 1333 + "a", 1),
    ]
    assert len(statements) == 1


@pytest.mark.usefixtures("oracle_db")
async def test_screen_sections_ignore_paths_that_are_not_strings() -> None:
    """配列・JSON null の section_path は従来どおり目録に入れず、長い配列でも失敗しない。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    paths: list[object] = [None, ["第1章", "第2章"], ["合成の長い親階層" * 170, "第2章"]]
    chunks = [
        Chunk(
            index=index,
            text=f"本文{index}",
            start_offset=0,
            end_offset=3,
            metadata=cast(Any, {"section_path": path}),
        )
        for index, path in enumerate(paths)
    ]
    chunks.append(_chunk(len(paths), "文字列の節", "第3章"))
    file_name = f"shapes-{token}.pdf"
    await _indexed_document(client, file_name=file_name, chunks=chunks)

    assert await client.retrieval_screen_sections({"file_name": f"shapes-{token}"}) == [
        (file_name, "第3章", 1)
    ]


@dataclass(frozen=True)
class _ReferencePaths:
    target: str
    child: str
    sibling: str


def _reference_paths(target: str, sibling: str | None = None) -> _ReferencePaths:
    return _ReferencePaths(target, f"{target} > 子の節", sibling or f"{target}の続き")


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    "paths",
    [
        pytest.param(_reference_paths("第5章 準備"), id="short"),
        pytest.param(
            _ReferencePaths(
                "第5章 準備", "第5章 準備 > " + "合成の長い子の節" * 170, "第5章 準備の続き"
            ),
            id="short_target_long_child",
        ),
        # 3960 バイトは全部が VARCHAR2 の範囲。3996 は下の節が超え、3997 は「 > 」を足した接頭辞が
        # CLOB の bind になり、4000 は節そのものが CLOB の bind になるが JSON_VALUE では読める。
        pytest.param(_reference_paths("合" * 1320), id="3960_bytes"),
        pytest.param(_reference_paths("合" * 1332), id="3996_bytes"),
        pytest.param(_reference_paths("合" * 1332 + "a"), id="3997_bytes"),
        pytest.param(_reference_paths("合" * 1333), id="3999_bytes"),
        pytest.param(_reference_paths("合" * 1333 + "a"), id="4000_bytes"),
        pytest.param(_reference_paths("合" * 1333 + "aa"), id="4001_bytes"),
        pytest.param(_reference_paths("synthetic-long-parent-" * 230), id="over_4000_chars"),
        pytest.param(_reference_paths("合成の長い親階層" * 1400), id="over_the_32k_bind_size"),
        # CLOB の長さは UTF-16 の単位で数える。4 バイト文字の接頭辞の比較の長さを誤ると、同じ文字で
        # 始まる長い隣の節まで当たる。
        pytest.param(
            _reference_paths("\U00020bb7" * 1400, "\U00020bb7" * 1401), id="supplementary"
        ),
    ],
)
async def test_reference_chunks_compare_whole_path(paths: _ReferencePaths) -> None:
    """節とその下の節だけを読み順に返す。先頭や末尾だけが同じ節は含めない。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    document_id = await _indexed_document(
        client,
        file_name=f"reference-{token}.pdf",
        chunks=[
            _chunk(0, "隣の節", paths.sibling),
            _chunk(1, "参照先の節", paths.target),
            _chunk(2, "下の節", paths.child),
            _chunk(3, "別の節", "第9章"),
        ],
    )
    filters = {"file_name": f"reference-{token}"}

    rows = await client.retrieval_reference_chunks(
        filters, document_id=document_id, section_path=paths.target, limit=10
    )
    assert [row.text for row in rows] == ["参照先の節", "下の節"]

    limited = await client.retrieval_reference_chunks(
        filters, document_id=document_id, section_path=paths.target, limit=1
    )
    assert [row.text for row in limited] == ["参照先の節"]


@pytest.mark.usefixtures("oracle_db")
async def test_screen_chunks_read_heading_only_in_long_path() -> None:
    """目録に入る見出しが長い見出しの列にだけあっても、その chunk を読める。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    screen = "（２）合成の画面"
    file_name = f"screen-{token}.pdf"
    await _indexed_document(
        client,
        file_name=file_name,
        chunks=[
            _chunk(0, "短い節の本文", "第1章"),
            _chunk(1, "画面の本文", f"{'合成の長い親階層' * 170} > {screen}"),
            _chunk(2, "長い別の節の本文", f"{'合成の長い親階層' * 170} > （３）別の画面"),
        ],
    )

    chunks = await client.retrieval_screen_chunks(
        {"file_name": f"screen-{token}"}, file_name=file_name, heading=screen, limit=10
    )
    assert [chunk.text for chunk in chunks] == ["画面の本文"]
