"""JSON_VALUE の既定の VARCHAR2(4000) を超える見出しの値の読み取りと絞り込み(#4157)。

文書・見出しは合成のもの。AI は決定論スタブ(oracle_db fixture)で、SQL だけを実 DB で評価する。
"""

from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, _metadata_text_filter, _oracle_retrieval_where
from app.config import Settings
from app.rag.chunking import Chunk
from app.rag.structure_index import structure_index_plan
from tests.test_answer_scope_oracle import _chunk, _indexed_document

# 既定の RETURNING VARCHAR2(4000) を超える 3 つの長さ。
_LONG = [
    pytest.param("合成の長い親階層" * 170, id="over_4000_bytes_under_4000_chars"),
    pytest.param("synthetic-long-parent-" * 230, id="over_4000_chars"),
    pytest.param("合成の長い親階層" * 1400, id="over_the_32k_bind_size"),
]


def _metadata_chunk(index: int, text: str, **metadata: object) -> Chunk:
    return Chunk(
        index=index,
        text=text,
        start_offset=0,
        end_offset=len(text),
        metadata=cast(Any, metadata),
    )


async def _filtered_texts(client: OracleClient, filters: dict[str, str]) -> list[str]:
    """検索と同じ条件で絞った chunk の本文を読み順に返す。"""
    where_sql, binds = _oracle_retrieval_where(filters)
    rows = await client._fetch_all(
        f"""SELECT c.chunk_text FROM rag_chunks c
        JOIN rag_documents d ON d.document_id = c.document_id
        WHERE {where_sql} ORDER BY c.chunk_index""",
        binds,
    )
    return [str(row["chunk_text"]) for row in rows]


def _count_queries(client: OracleClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetch_all = client._fetch_all
    statements: list[str] = []

    async def counting(statement: str, binds: dict[str, object] | None = None) -> object:
        statements.append(statement)
        return await fetch_all(statement, binds)

    monkeypatch.setattr(client, "_fetch_all", counting)
    return statements


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _LONG)
async def test_reading_index_keeps_long_section_path(
    parent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = OracleClient()
    long_path = f"{parent} > 第2章"
    document_id = await _indexed_document(
        client,
        file_name=f"outline-{uuid4().hex[:12]}.pdf",
        chunks=[
            _chunk(0, "短い節", "第1章"),
            _chunk(1, "長い節", long_path),
            _metadata_chunk(2, "長い配列", section_path=[parent, "第3章"]),
            _chunk(3, "見出しの無い本文"),
        ],
    )
    statements = _count_queries(client, monkeypatch)

    _chunk_set, rows = await client.document_reading_index(document_id)

    assert [row["section_path"] for row in rows] == ["第1章", long_path, None, None]
    assert len(statements) == 1


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        pytest.param(["{title} > {long}"], "{title}", id="only_long_path"),
        pytest.param(["{long}"], "{long}", id="long_heading_without_separator"),
        # 最初の見出しのある chunk のタイトルを使う。長いだけで後の短い節に譲らない。
        pytest.param(["{title} > {long}", "別の見出し"], "{title}", id="long_before_short"),
        pytest.param([None, "{title} > {long}"], "{title}", id="no_heading_before_long"),
        pytest.param(["{title} > 第1章", "別の見出し"], "{title}", id="short_only"),
        pytest.param([None], None, id="no_heading"),
    ],
)
async def test_older_version_title_reads_long_section_path(
    paths: list[str | None], expected: str | None
) -> None:
    client = OracleClient()
    token = uuid4().hex[:12]
    title, long = f"合成の旧版のタイトル{token}", "合成の長い親階層" * 170

    def fill(value: str) -> str:
        return value.format(title=title, long=long)

    older_id = await _indexed_document(
        client,
        file_name=f"older-{token}.pdf",
        chunks=[
            _chunk(index, f"旧版の本文{index}", fill(path) if path else None)
            for index, path in enumerate(paths)
        ],
    )
    newer_id = await _indexed_document(
        client, file_name=f"newer-{token}.pdf", chunks=[_chunk(0, "今の版の本文", "第1章")]
    )
    await client.set_document_superseded_by(older_id, newer_id)

    versions = await client.retrieval_older_versions({"file_name": token}, [newer_id], limit=5)

    assert [(version.document_id, version.title) for version in versions] == [
        (older_id, fill(expected) if expected else None)
    ]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("field", ["section_path", "section_title"])
@pytest.mark.parametrize("parent", _LONG)
async def test_section_filter_matches_long_values(field: str, parent: str) -> None:
    """絞り込みは値の先頭・中間・末尾のどこに語があっても当たり、当たらない行は含めない。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    long_value = f"Head語{parent}中間語{parent}末尾Tail"
    await _indexed_document(
        client,
        file_name=f"filter-{token}.pdf",
        chunks=[
            _metadata_chunk(0, "短い一致", **{field: "短い節のhead語"}),
            _metadata_chunk(1, "長い一致", **{field: long_value}),
            _metadata_chunk(2, "長い不一致", **{field: parent}),
            _metadata_chunk(3, "短い不一致", **{field: "別の節"}),
            _chunk(4, "値の無い本文"),
        ],
    )

    async def matched(word: str) -> list[str]:
        return await _filtered_texts(client, {"file_name": f"filter-{token}", field: word})

    assert await matched("HEAD語") == ["短い一致", "長い一致"]
    assert await matched("中間語") == ["長い一致"]
    assert await matched("末尾tail") == ["長い一致"]
    assert await matched("どこにも無い語") == []


@pytest.mark.usefixtures("oracle_db")
async def test_section_filter_accepts_value_over_bind_limit() -> None:
    """絞り込みの語そのものが CLOB の bind になる長さでも失敗せず、全体を含む行だけに当たる。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    word = "合成の長い親階層" * 170
    await _indexed_document(
        client,
        file_name=f"filter-{token}.pdf",
        chunks=[
            _chunk(0, "長い一致", f"前 > {word} > 後"),
            _chunk(1, "先頭だけ同じ", word[:-1] + "違"),
            _chunk(2, "短い節", "第1章"),
        ],
    )

    assert await _filtered_texts(
        client, {"file_name": f"filter-{token}", "section_path": word}
    ) == ["長い一致"]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("field", ["section_path", "section_title"])
@pytest.mark.parametrize("parent", _LONG)
async def test_structure_plan_counts_long_heading(field: str, parent: str) -> None:
    """見出しが長いだけの文書を「構造が無い」にしない。"""
    client = OracleClient()
    token = uuid4().hex[:8]
    kb = await client.create_knowledge_base(name=f"合成参照 KB {token}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    document_id = await _indexed_document(
        client,
        file_name=f"plan-{token}.pdf",
        chunks=[_metadata_chunk(0, "長い見出しの本文", **{field: parent})],
        knowledge_base_ids=[kb.id],
    )

    [item] = await structure_index_plan(client, kb.id, document_ids=[document_id])

    assert item["reason"] is None


def test_section_filter_uses_like_for_short_value_and_instr_for_large_value() -> None:
    """短い語は従来の LIKE のまま超えた行だけ CLOB でも比べ、CLOB の bind は LIKE に渡さない。"""
    clause, bind = _metadata_text_filter("section_path", "A_b%")
    assert bind == "%a\\_b\\%%"
    assert "LOWER(JSON_VALUE(c.metadata_json, '$.section_path')) LIKE" in clause
    assert "RETURNING CLOB)) LIKE :filter_section_path" in clause

    word = "合" * 1400
    clause, bind = _metadata_text_filter("section_title", "Ａ" + word)
    assert bind == ("Ａ" + word).casefold()
    assert clause.startswith("INSTR(LOWER(JSON_VALUE(c.metadata_json, '$.section_title'")
    assert "LIKE" not in clause


async def test_reading_index_and_older_versions_use_one_query_for_long_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OracleClient(settings=Settings())
    long_path = "合成タイトル > " + "長" * 2000
    statements: list[str] = []

    async def fake_fetch_all(statement: str, binds: Any = None) -> list[dict[str, Any]]:
        statements.append(statement)
        if "root_heading" in statement:
            return [
                {
                    "document_id": "old-1",
                    "file_name": "old.pdf",
                    "superseded_by_document_id": "new-1",
                    "root_heading": None,
                    "long_section_path": long_path,
                }
            ]
        return [
            {
                "chunk_set_id": "cs-1",
                "chunk_index": 0,
                "chunk_id": "c-1",
                "section_path": None,
                "long_section_path": long_path,
                "chars": 1,
            }
        ]

    monkeypatch.setattr(client, "_fetch_all", fake_fetch_all)

    [version] = await client.retrieval_older_versions(
        {"knowledge_base_id": "kb-1"}, ["new-1"], limit=1
    )
    _chunk_set, rows = await client.document_reading_index("doc-1")

    assert version.title == "合成タイトル"
    assert rows[0]["section_path"] == long_path
    assert len(statements) == 2
