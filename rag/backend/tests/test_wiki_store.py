"""SQL の範囲と、永続化前の原文照合を接続せずに確認する。"""

from typing import Any, cast

import pytest

from app.clients.wiki_store import WikiSnapshot, WikiStore, wiki_scope
from app.rag.wiki_models import WikiContext, WikiPage
from tests.test_wiki_compiler import units


def test_explicit_version_is_not_replaced_with_current_version() -> None:
    sql, binds = wiki_scope({"knowledge_base_id": "kb-a", "chunk_set_id": "requested-version"})
    assert "requested-version" in binds.values()
    assert "wiki_current_set.is_active=1" in sql
    assert "wiki_current_set.status='INDEXED'" in sql


@pytest.mark.asyncio
async def test_changed_body_is_not_returned_as_the_cached_original() -> None:
    unit = units()[0]

    class Oracle:
        async def _fetch_all(self, sql: str, binds: Any) -> Any:
            assert "wiki_build_enabled" in sql and "kb-a" in binds.values()
            return [
                {
                    "chunk_id": unit.source_chunk_ids[0],
                    "document_id": unit.document_id,
                    "chunk_set_id": unit.chunk_set_id,
                    "chunk_text": unit.text + " 変更。",
                }
            ]

    page = WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    assert not await WikiStore(cast(Any, Oracle())).read_sources(
        [page], {"knowledge_base_id": "kb-a"}
    )


@pytest.mark.asyncio
async def test_persistence_rejects_a_page_that_changed_the_authoritative_unit() -> None:
    unit = units()[0]
    page = WikiPage(
        unit=unit.model_copy(update={"text": "生成された別本文"}),
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    with pytest.raises(ValueError, match="原文と構築対象"):
        await WikiStore(cast(Any, object())).checkpoint(
            WikiSnapshot("document-a", "source-a", (unit,)), "lease-a", [page]
        )


@pytest.mark.asyncio
async def test_text_score_is_read_before_hydration_and_keeps_its_order() -> None:
    pages = [
        WikiPage(
            unit=u,
            navigation=WikiContext(
                unit_id=u.unit_id,
                context="案内",
                possible_questions=[],
                dependencies=[],
                unresolved=[],
            ),
            input_fingerprint="input-a",
        )
        for u in units()[:2]
    ]

    class Oracle:
        def __init__(self) -> None:
            self.calls = 0

        async def _fetch_all(self, sql: str, binds: Any) -> Any:
            self.calls += 1
            assert "kb-a" in binds.values() and "wiki_build_enabled" in sql
            if self.calls == 1:
                assert "SCORE(1)" in sql and "CONTAINS(p.search_text" in sql
                assert "page_json" not in sql and "WITH text_hits" not in sql
                return [{"unit_id": p.unit.unit_id, "text_score": 10} for p in reversed(pages)]
            assert "SCORE(" not in sql and "p.unit_id IN" in sql
            return [{"page_json": p.model_dump()} for p in pages]

    oracle = Oracle()
    hits = await WikiStore(cast(Any, oracle)).text_pages(
        "synthetic", {"knowledge_base_id": "kb-a"}, compiler="compiler-a", limit=2
    )
    assert oracle.calls == 2
    assert [p.unit.unit_id for p in hits] == [p.unit.unit_id for p in reversed(pages)]
