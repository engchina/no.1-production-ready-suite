"""文書の本文から語を字のとおりに探す MCP のツール（`rag_grep`。#5293）のテスト。

Oracle は決定論の fake に差し替える。fake の ``grep_chunks`` は、SQL の見え方の条件と同じく監査
context のナレッジベースの範囲の外の文書を返さない。SQL がその条件を必ず含むことは
``SectionMapStore.grep_chunks`` の SQL を直接確かめ、実 Oracle での振る舞いは
``test_rag_grep_oracle.py`` が確かめる。内容はすべて合成。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch

from app.clients.section_map_store import GrepChunkRow, SectionMapStore
from app.config import get_settings
from app.main import app
from app.mcp import profile_scope
from app.mcp import tools as mcp_tools
from app.rag.request_context import (
    AuditRequestContext,
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.rag.section_chunk_map import ROOT_SECTION_ID
from tests.security_support import ProductionAuth, enable_production_auth
from tests.support import AsgiTestClient
from tests.test_mcp_api import SECRET
from tests.test_security_scope import ScopedViewOracle

client = AsgiTestClient(app)


@dataclass(frozen=True)
class _Chunk:
    knowledge_base_id: str
    row: GrepChunkRow


def _row(
    document_id: str,
    index: int,
    text: str,
    *,
    section: tuple[str, str] | None = ("sec-1", "合成の章"),
) -> GrepChunkRow:
    return GrepChunkRow(
        document_id=document_id,
        file_name=f"synthetic-{document_id}.txt",
        chunk_id=f"{document_id}:cs:{index}",
        chunk_index=index,
        text=text,
        section_id=section[0] if section else None,
        section_title=section[1] if section else None,
    )


class _GrepStore:
    """``grep_chunks`` の fake。SQL と同じ順・範囲・候補の絞り込み（``contains``）で返す。"""

    chunks: list[_Chunk] = []
    calls: list[dict[str, Any]] = []
    contexts: list[AuditRequestContext] = []

    def __init__(self, oracle: object | None = None) -> None:
        del oracle

    async def grep_chunks(
        self,
        *,
        document_ids: Sequence[str] | None,
        contains: str | None,
        after: tuple[str, int] | None,
        limit: int,
    ) -> list[GrepChunkRow]:
        context = current_audit_request_context()
        self.contexts.append(context)
        self.calls.append({"document_ids": document_ids, "contains": contains, "after": after})
        allowed = context.allowed_knowledge_base_ids
        rows = [
            chunk.row
            for chunk in self.chunks
            if (allowed is None or chunk.knowledge_base_id in allowed)
            and (not document_ids or chunk.row.document_id in document_ids)
            and (contains is None or contains in chunk.row.text)
        ]
        rows.sort(key=lambda row: (row.document_id, row.chunk_index))
        if after is not None:
            rows = [row for row in rows if (row.document_id, row.chunk_index) > after]
        return rows[:limit]


class _GrepOracle:
    """指定の文書が見られるか（``accessible_document_ids``）だけの fake。"""

    async def accessible_document_ids(self, document_ids: Sequence[str]) -> set[str]:
        allowed = current_audit_request_context().allowed_knowledge_base_ids
        return {
            chunk.row.document_id
            for chunk in _GrepStore.chunks
            if chunk.row.document_id in document_ids
            and (allowed is None or chunk.knowledge_base_id in allowed)
        }


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> ProductionAuth:
    production = enable_production_auth(monkeypatch)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SECRET)
    monkeypatch.setattr(mcp_tools, "OracleClient", _GrepOracle)
    monkeypatch.setattr(mcp_tools, "SectionMapStore", _GrepStore)
    _GrepStore.chunks = []
    _GrepStore.calls = []
    _GrepStore.contexts = []
    return production


def _headers(user_uuid: str, profile_ids: list[str] | None = None) -> dict[str, str]:
    token = issue_service_token(
        SECRET,
        subject=user_uuid,
        audience="rag",
        issuer="agent",
        claims={"run_id": "run-1", "agent_id": "agent-1"},
        profile_ids=profile_ids,
    )
    return {"Authorization": f"Bearer {token}"}


def _grep(arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "rag_grep", "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _searcher(auth: ProductionAuth, knowledge_base_ids: list[str] | None = None) -> dict[str, str]:
    user = auth.user_with_permissions(
        f"searcher-{uuid4().hex[:8]}",
        ["menu.search"],
        knowledge_base_ids=knowledge_base_ids or ["kb-1"],
    )
    return _headers(user.user_uuid)


def test_grep_never_returns_documents_outside_the_user_scope(auth: ProductionAuth) -> None:
    """見られない文書（利用者のナレッジベースの外）の一致は、語が同じでも返さない。"""
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", 0, "合成の語を含む行")),
        _Chunk("kb-2", _row("doc-b", 0, "合成の語を含む行")),
    ]
    result = _grep({"text": "合成の語"}, _searcher(auth))

    assert result["isError"] is False, result
    assert [match["document_id"] for match in result["structuredContent"]["matches"]] == ["doc-a"]
    assert {context.allowed_knowledge_base_ids for context in _GrepStore.contexts} == {
        frozenset({"kb-1"})
    }
    # 見られない文書を名指ししても読まない（見つからない文書と同じエラー）。
    named = _grep({"text": "合成の語", "document_ids": ["doc-a", "doc-b"]}, _searcher(auth))
    assert named["isError"] is True
    assert named["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE
    assert "doc-b" not in named["structuredContent"]["message"]


def test_grep_reads_only_the_profile_scope_of_the_calling_agent(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """業務 Agent のデータの範囲（claim）があれば、範囲のプロファイルの参照先の文書だけを探す。"""
    views = {
        "bv-1": SearchAnswerProfileConfig(knowledge_base_ids=["kb-1"]),
        "bv-2": SearchAnswerProfileConfig(knowledge_base_ids=["kb-2"]),
    }
    monkeypatch.setattr(profile_scope, "OracleClient", lambda *_a, **_k: ScopedViewOracle(views))
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", 0, "合成の語")),
        _Chunk("kb-2", _row("doc-b", 0, "合成の語")),
    ]
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        search_answer_profile_ids=["bv-1", "bv-2"],
        knowledge_base_ids=["kb-1", "kb-2"],
    )

    result = _grep({"text": "合成の語"}, _headers(user.user_uuid, ["bv-1"]))

    assert [match["document_id"] for match in result["structuredContent"]["matches"]] == ["doc-a"]
    assert _GrepStore.contexts[-1].allowed_knowledge_base_ids == frozenset({"kb-1"})


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("abc-12", "型 ＡＢＣ－１２ の行"),
        ("ＡＢＣ－１２", "型 abc-12 の行"),
        ("ABC", "小文字の abc の行"),
        ("ガイド", "半角の ｶﾞｲﾄﾞ の行"),
        ("ｶﾞｲﾄﾞ", "全角の ガイド の行"),
    ],
    ids=["fullwidth-text", "fullwidth-query", "case", "halfwidth-kana-text", "halfwidth-query"],
)
def test_grep_treats_case_and_width_as_the_same(auth: ProductionAuth, text: str, line: str) -> None:
    _GrepStore.chunks = [_Chunk("kb-1", _row("doc-a", 0, f"前の行\n{line}\n後の行"))]
    result = _grep({"text": text}, _searcher(auth))

    assert [match["line"] for match in result["structuredContent"]["matches"]] == [line]


@pytest.mark.parametrize(
    ("text", "contains"),
    [("合成の語", "合成の語"), ("AB 合成", "合成"), ("abc", None), ("ガイド", None)],
    ids=["kanji", "mixed", "ascii", "katakana"],
)
def test_grep_narrows_candidates_only_by_characters_written_as_is(
    auth: ProductionAuth, text: str, contains: str | None
) -> None:
    """DB で候補を絞る字の列は、別の字（全角・半角・大文字）で書かれ得ない字だけ。"""
    _grep({"text": text}, _searcher(auth))

    assert _GrepStore.calls[0]["contains"] == contains


def test_grep_matches_literally_without_patterns(auth: ProductionAuth) -> None:
    _GrepStore.chunks = [_Chunk("kb-1", _row("doc-a", 0, "abc の行\na.c の行\na*c の行"))]

    for text, expected in [("a.c", ["a.c の行"]), ("a*c", ["a*c の行"]), (".*", [])]:
        result = _grep({"text": text}, _searcher(auth))
        assert [match["line"] for match in result["structuredContent"]["matches"]] == expected


def test_grep_returns_two_lines_before_and_after(auth: ProductionAuth) -> None:
    text = "\n".join(["一", "二", "三", "四", "一致の行", "六", "七", "八"])
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", 0, text)),
        # chunk の端では、ある行だけを返す。
        _Chunk("kb-1", _row("doc-a", 1, "一致の行\n次の行")),
    ]
    result = _grep({"text": "一致"}, _searcher(auth))
    first, second = result["structuredContent"]["matches"]

    assert (first["before"], first["line"], first["after"]) == (
        ["三", "四"],
        "一致の行",
        ["六", "七"],
    )
    assert (second["before"], second["after"]) == ([], ["次の行"])
    assert first["section_id"] == "sec-1" and first["section_title"] == "合成の章"
    assert first["chunk_id"] == "doc-a:cs:0" and first["file_name"] == "synthetic-doc-a.txt"


def test_grep_stops_at_the_limit_and_tells_how_to_narrow(auth: ProductionAuth) -> None:
    limit = mcp_tools.GREP_MATCHES_MAX
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", index, f"一致の行 {index}")) for index in range(limit + 1)
    ]
    over = _grep({"text": "一致"}, _searcher(auth))["structuredContent"]

    assert len(over["matches"]) == limit
    assert over["truncated"] is True
    assert "document_ids" in over["next_step"]

    _GrepStore.chunks = _GrepStore.chunks[:limit]
    exact = _grep({"text": "一致"}, _searcher(auth))["structuredContent"]
    assert len(exact["matches"]) == limit
    assert exact["truncated"] is False and exact["next_step"] is None


def test_grep_reads_on_in_batches_until_the_scan_limit(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """候補を DB で絞れない語は、読む chunk の上限で打ち切りとして返す。"""
    monkeypatch.setattr(mcp_tools, "GREP_BATCH", 2)
    monkeypatch.setattr(mcp_tools, "GREP_SCAN_CHUNKS_MAX", 4)
    _GrepStore.chunks = [_Chunk("kb-1", _row("doc-a", index, "別の行")) for index in range(5)]
    _GrepStore.chunks.append(_Chunk("kb-1", _row("doc-b", 0, "abc")))
    result = _grep({"text": "abc"}, _searcher(auth))["structuredContent"]

    assert result["matches"] == [] and result["truncated"] is True
    assert [call["after"] for call in _GrepStore.calls] == [None, ("doc-a", 1)]

    monkeypatch.setattr(mcp_tools, "GREP_SCAN_CHUNKS_MAX", 100)
    _GrepStore.calls = []
    found = _grep({"text": "abc"}, _searcher(auth))["structuredContent"]
    assert [match["document_id"] for match in found["matches"]] == ["doc-b"]
    assert found["truncated"] is False


def test_grep_searches_only_the_given_documents(auth: ProductionAuth) -> None:
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", 0, "合成の語")),
        _Chunk("kb-1", _row("doc-b", 0, "合成の語")),
        _Chunk("kb-1", _row("doc-c", 0, "合成の語")),
    ]
    result = _grep({"text": "合成の語", "document_ids": ["doc-c", "doc-a"]}, _searcher(auth))

    assert [match["document_id"] for match in result["structuredContent"]["matches"]] == [
        "doc-a",
        "doc-c",
    ]
    assert _GrepStore.calls[0]["document_ids"] == ["doc-c", "doc-a"]
    everything = _grep({"text": "合成の語"}, _searcher(auth))
    assert len(everything["structuredContent"]["matches"]) == 3
    assert _GrepStore.calls[-1]["document_ids"] is None


def test_grep_uses_the_whole_document_section_without_a_section_map(
    auth: ProductionAuth,
) -> None:
    _GrepStore.chunks = [_Chunk("kb-1", _row("doc-a", 0, "合成の語", section=None))]
    [match] = _grep({"text": "合成の語"}, _searcher(auth))["structuredContent"]["matches"]

    assert match["section_id"] == ROOT_SECTION_ID
    assert match["section_title"] == "synthetic-doc-a.txt"


def test_grep_cuts_long_lines_around_the_match(auth: ProductionAuth) -> None:
    long_line = "前" * 1000 + "合成の語" + "後" * 1000
    context = "文" * 500
    _GrepStore.chunks = [_Chunk("kb-1", _row("doc-a", 0, f"{context}\n{long_line}\n{context}"))]
    [match] = _grep({"text": "合成の語"}, _searcher(auth))["structuredContent"]["matches"]

    assert len(match["line"]) == mcp_tools.GREP_LINE_MAX_CHARS
    assert "合成の語" in match["line"]
    assert match["line_truncated"] is True
    assert [len(item) for item in (*match["before"], *match["after"])] == [
        mcp_tools.GREP_CONTEXT_LINE_MAX_CHARS
    ] * 2


def test_grep_drops_the_same_line_of_overlapping_chunks(auth: ProductionAuth) -> None:
    _GrepStore.chunks = [
        _Chunk("kb-1", _row("doc-a", 0, "前\n一致の行\n後")),
        _Chunk("kb-1", _row("doc-a", 1, "前\n一致の行\n後")),
    ]
    matches = _grep({"text": "一致"}, _searcher(auth))["structuredContent"]["matches"]

    assert len(matches) == 1


@pytest.mark.parametrize("text", ["改行\nを含む", "   ", ""], ids=["newline", "blank", "empty"])
def test_grep_rejects_text_that_cannot_match_a_line(auth: ProductionAuth, text: str) -> None:
    result = _grep({"text": text}, _searcher(auth))

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    assert _GrepStore.calls == []


class _CapturingOracle:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, object]]] = []

    async def _fetch_all(self, sql: str, binds: dict[str, object]) -> list[dict[str, object]]:
        self.queries.append((sql, binds))
        return []


async def test_grep_sql_always_applies_the_visibility_conditions() -> None:
    """``grep_chunks`` の SQL は、検索と同じ見え方の条件（範囲の bind）を必ず含む。"""
    oracle = _CapturingOracle()
    store = SectionMapStore(oracle)  # type: ignore[arg-type]
    token = set_audit_request_context(
        AuditRequestContext(allowed_knowledge_base_ids=frozenset({"kb-1"}))
    )
    try:
        await store.grep_chunks(document_ids=None, contains=None, after=None, limit=10)
        await store.grep_chunks(
            document_ids=["doc-a"], contains="合成", after=("doc-a", 3), limit=10
        )
    finally:
        reset_audit_request_context(token)

    (all_sql, all_binds), (named_sql, named_binds) = oracle.queries
    for sql, binds in oracle.queries:
        assert "access_knowledge_base_id_0" in sql
        assert binds["access_knowledge_base_id_0"] == "kb-1"
        assert "d.status = 'INDEXED'" in sql
        assert "is_owner = 1" in sql
    # 指定の無い呼び出しは旧版を除き、文書の指定は旧版も読む（文書を読むツールと同じ）。
    assert "superseded_by_document_id IS NULL" in all_sql
    assert "superseded_by_document_id IS NULL" not in named_sql
    assert "grep_document_0" in named_sql and named_binds["grep_document_0"] == "doc-a"
    assert named_binds["grep_contains"] == "合成"
    assert (named_binds["grep_after_document"], named_binds["grep_after_index"]) == ("doc-a", 3)
    assert "grep_contains" not in all_binds and "grep_after_document" not in all_binds
