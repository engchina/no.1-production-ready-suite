"""実 Oracle で原文の参照・有効 KB・公開前の検査と削除を確かめる。AI は合成応答。"""

from dataclasses import replace
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute, _fetch_one
from app.clients.wiki_store import WikiStore
from app.rag.wiki_models import WikiContext, WikiPage, fingerprint
from app.schemas.document import DocumentClassification
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document

pytestmark = pytest.mark.usefixtures("oracle_db")


async def test_virtual_parent_persists_all_original_bindings_and_rejects_changed_member() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-group-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token)

    def group(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            """UPDATE rag_chunks SET metadata_json=JSON_MERGEPATCH(metadata_json,
                JSON('{"parent_chunk_id":"virtual-parent"}')) WHERE document_id=:id""",
            {"id": did},
        )

    await oracle._run_transaction(group)
    await store.set_enabled(kb.id, True)
    source = await store.source_units(did)
    assert source is not None and len(source.units) == 1
    unit = source.units[0]
    assert len(unit.source_chunk_ids) == len(unit.source_chunk_hashes) == 3
    source = replace(source, compiler_fingerprint=fingerprint("compiler-a"))
    assert await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    page = WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="合成の案内。",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    await store.checkpoint(source, lease, [page], vectors=[_EMBEDDING])
    await store.finish(source, lease)
    restored = await store.saved_pages(source)
    assert restored[0].unit == unit
    filters = {"knowledge_base_id": kb.id}
    assert len(await store.read_sources(restored, filters)) == 3

    def change(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            "UPDATE rag_chunks SET chunk_text=:text WHERE chunk_id=:id",
            {"text": "変更された合成の原文。", "id": unit.source_chunk_ids[-1]},
        )

    await oracle._run_transaction(change)
    assert not await store.read_sources(restored, filters)


async def test_shared_document_cache_requires_enabled_current_membership_and_cascades() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    first = await oracle.create_knowledge_base(name=f"synthetic-wiki-a-{token}")
    second = await oracle.create_knowledge_base(name=f"synthetic-wiki-b-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=first.id, token=token)
    await oracle.assign_documents_to_knowledge_base(second.id, [did])
    filters = {"knowledge_base_id": first.id}
    other = {"knowledge_base_id": second.id}
    assert not await store.document_ids(filters)
    statuses = await store.document_statuses(filters)
    assert [(row["document_id"], row["status"]) for row in statuses] == [(did, "NOT_BUILT")]
    assert statuses[0]["file_name"] and statuses[0]["failure_code"] is None
    # 名前の順の 1 ページ分と、ページに関わらない KB 全体の件数（#5466）。
    later, _, _ = await _indexed_document(oracle, knowledge_base_id=first.id, token=f"{token}z")
    page = await store.document_statuses(filters, limit=1, offset=1)
    assert [row["document_id"] for row in page] == [later]
    assert await store.status_counts(filters) == {"NOT_BUILT": 2}
    # 以降の確認は 1 件の文書で行う。
    await oracle.remove_document_from_knowledge_base(first.id, later)
    await store.set_enabled(first.id, True)
    await store.set_enabled(second.id, True)
    assert {kb for kb, _ in await store.enabled_knowledge_bases()} >= {first.id, second.id}
    assert await store.document_ids(filters) == [did]
    revision = await store.revision_fingerprint(did)
    source = await store.source_units(did)
    assert revision is not None and source is not None
    source = replace(
        source, compiler_fingerprint=fingerprint("compiler-a"), revision_fingerprint=revision
    )
    assert await store.enqueue(source)
    # 同じ入力の再投入は QUEUED / RUNNING / READY を作り直さない。
    assert not await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease and await store.claim(source, lease_seconds=300) is None
    pages = [
        WikiPage(
            unit=u,
            navigation=WikiContext(
                unit_id=u.unit_id,
                context="架空の案内 syntheticnavigation。",
                possible_questions=[],
                dependencies=[],
                unresolved=[],
            ),
            input_fingerprint="input-a",
        )
        for u in source.units
    ]
    await store.checkpoint(source, lease, pages)
    # 埋め込みが揃うまで公開しない。
    with pytest.raises(ValueError, match="未完了"):
        await store.finish(source, lease)
    # 進んだ時間切れは自動で続くので待機に数え、再試行の要る失敗にしない（#5430）。
    await store.finish(source, lease, failure_code="interrupted")
    assert [row["status"] for row in await store.document_statuses(filters)] == ["QUEUED"]
    assert (await store.document_statuses(filters))[0]["failure_code"] is None
    assert await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    await store.finish(source, lease)
    assert [row["status"] for row in await store.document_statuses(filters)] == ["READY"]
    assert len(await store.saved_pages(source)) == len(pages)
    assert len(await store.read_sources(pages, filters)) == len(pages)
    # 検索の経路（#4941）: 目録・案内・ベクトルの上位・全文検索の順位は、公開済み・現在の構築条件の
    # 案内だけを返す。全文検索の順位は文書ごとに 1 回の SQL で取る（#4986）。
    compiler = source.compiler_fingerprint
    catalog = await store.catalog(filters, compiler)
    assert [row["document_id"] for row in catalog] == [did]
    # 形式に関わらない版の属性（版の状態と分類の有効期間）を、どの文書にも渡す（#5446 の続き）。
    assert catalog[0]["source_identities"] == [{"version_status": "current"}]
    await oracle.save_document_classification(
        did,
        DocumentClassification(effective_from=date(2026, 4, 1), effective_to=date(2027, 4, 1)),
    )
    assert (await store.catalog(filters, compiler))[0]["source_identities"] == [
        {"version_status": "current", "effective_from": "2026-04-01", "effective_to": "2027-04-01"}
    ]
    unit_ids = sorted(page.unit.unit_id for page in pages)
    assert sorted(p.unit.unit_id for p in await store.pages(filters, compiler)) == unit_ids
    top = await store.pages(filters, compiler, query_vector=_EMBEDDING, limit=1)
    assert len(top) == 1 and top[0].unit.unit_id in unit_ids
    by_unit = await store.pages(filters, compiler, unit_ids=unit_ids[:1], document_ids=[did])
    assert [p.unit.unit_id for p in by_unit] == unit_ids[:1]
    ranking = await store.text_rankings("syntheticnavigation", filters, compiler, limit=10)
    assert sorted(uid for _, uid in ranking) == unit_ids
    per_document = await store.text_rankings(
        "syntheticnavigation", filters, compiler, limit=1, document_ids=[did]
    )
    assert len(per_document) == 1 and per_document[0][0] == did
    assert not await store.catalog(filters, fingerprint("compiler-b"))
    assert not await store.pages(filters, fingerprint("compiler-b"))
    await store.set_enabled(first.id, False)
    # 無効にした KB からは読めないが、保存済みの案内は残り、別の有効な KB では使える。
    assert not await store.read_sources(pages, filters)
    assert not await store.document_ids(filters)
    assert not await store.catalog(filters, compiler)
    assert len(await store.read_sources(pages, other)) == len(pages)
    assert [row["status"] for row in await store.document_statuses(filters)] == ["READY"]

    # 再試行の要る失敗は理由つきで返し、再試行で待機へ戻す（#5466）。
    def fail(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            """UPDATE rag_wiki_documents SET status='FAILED',failure_code='timeout'
            WHERE document_id=:id""",
            {"id": did},
        )

    await oracle._run_transaction(fail)
    failed = await store.document_statuses(filters)
    assert [(row["status"], row["failure_code"]) for row in failed] == [("FAILED", "timeout")]
    assert failed[0]["updated_at"] is not None
    await store.requeue_failed(filters)
    assert [row["status"] for row in await store.document_statuses(filters)] == ["QUEUED"]
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    await store.finish(source, lease)
    await oracle.remove_document_from_knowledge_base(second.id, did)
    assert not await store.read_sources(pages, other)
    await store.set_enabled(first.id, True)
    assert len(await store.read_sources(pages, filters)) == len(pages)
    await oracle.delete_document(did)
    assert not await store.saved_pages(source)
    assert await store.document_state(did) is None


async def _published(store: WikiStore, did: str, context: str) -> tuple[Any, list[WikiPage]]:
    """合成の案内を作って公開した文書（保存版と案内）。"""
    revision = await store.revision_fingerprint(did)
    source = await store.source_units(did)
    assert revision is not None and source is not None
    source = replace(
        source, compiler_fingerprint=fingerprint("compiler-a"), revision_fingerprint=revision
    )
    await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    pages = [
        WikiPage(
            unit=u,
            navigation=WikiContext(
                unit_id=u.unit_id,
                context=context,
                possible_questions=[],
                dependencies=[],
                unresolved=[],
            ),
            input_fingerprint="input-a",
        )
        for u in source.units
    ]
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    await store.finish(source, lease)
    return source, pages


async def test_cross_document_links_follow_current_versions_scope_and_cascade() -> None:
    """文書をまたぐ関連（#5492）: 候補の除外・lease・checkpoint・両向きの読取・所属・削除。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-links-{token}")
    only_a = await oracle.create_knowledge_base(name=f"synthetic-links-only-a-{token}")
    a, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}a")
    b, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}b")
    await oracle.assign_documents_to_knowledge_base(only_a.id, [a])
    await store.set_enabled(kb.id, True)
    await store.set_enabled(only_a.id, True)
    source_a, pages_a = await _published(store, a, "合成の原則 syntheticprinciple。")
    _, pages_b = await _published(store, b, "合成の例外 syntheticexception。")
    compiler = source_a.compiler_fingerprint
    filters, narrow = {"knowledge_base_id": kb.id}, {"knowledge_base_id": only_a.id}
    assert await store.link_knowledge_bases(a) == sorted([kb.id, only_a.id])
    assert await store.link_knowledge_bases(b) == [kb.id]
    # 候補はほかの文書の案内だけ（ベクトル・全文の両方）。
    near = await store.pages(
        filters, compiler, query_vector=_EMBEDDING, limit=50, exclude_document_ids=[a]
    )
    assert near and {p.unit.document_id for p in near} == {b}
    ranking = await store.text_rankings(
        "syntheticexception",
        filters,
        compiler,
        limit=50,
        exclude_document_ids=[a],
    )
    assert ranking and {did for did, _ in ranking} == {b}

    first_a, first_b = pages_a[0].unit.unit_id, pages_b[0].unit.unit_id
    fingerprint_a = source_a.source_fingerprint
    claimed = await store.claim_links(a, fingerprint_a, "links-1", lease_seconds=300)
    assert claimed is not None and claimed[1] == []
    lease = claimed[0]
    assert await store.claim_links(a, fingerprint_a, "links-1", lease_seconds=300) is None
    with pytest.raises(ValueError, match="組"):
        await store.save_links(
            a,
            lease,
            sources=[first_a],
            candidates=[],
            links=[(first_a, first_b, "exception", "")],
            done=[],
        )
    await store.save_links(
        a,
        lease,
        sources=[first_a],
        candidates=[first_b],
        links=[(first_a, first_b, "exception", "合成の理由")],
        done=[f"forward:{first_a}"],
    )
    # 出る向きと入る向きの両方で読める。
    expected = [(first_a, first_b, "exception")]
    assert await store.links([first_a], filters, compiler) == expected
    assert await store.links([first_b], filters, compiler) == expected
    # 終点の文書が今の KB に無ければ辿らない（案内は KB をまたいで再利用されるので読むときに照合）。
    assert await store.links([first_a], narrow, compiler) == []
    # 構築条件が今と違う案内の関連は辿らない。
    assert await store.links([first_a], filters, fingerprint("compiler-b")) == []
    # 中断の後は判定済みの鍵を返して続きから作る。
    await store.finish_links(a, lease, failure_code="interrupted")
    resumed = await store.claim_links(a, fingerprint_a, "links-1", lease_seconds=300)
    assert resumed is not None and resumed[1] == [f"forward:{first_a}"]
    await store.finish_links(a, resumed[0], failure_code="build_failed")
    state = await store.link_state(a)
    assert state and state["status"] == "FAILED" and state["failure_code"] == "build_failed"
    # 再試行は関連の構築の失敗も待機へ戻す。
    await store.requeue_failed(filters)
    state = await store.link_state(a)
    assert state and state["status"] == "QUEUED"
    again = await store.claim_links(a, fingerprint_a, "links-1", lease_seconds=300)
    assert again is not None
    await store.finish_links(a, again[0])
    assert await store.claim_links(a, fingerprint_a, "links-1", lease_seconds=300) is None
    # 原文の保存版が変われば、古い保存版の関連を消して作り直す。
    renewed = await store.claim_links(a, fingerprint("source-new"), "links-1", lease_seconds=300)
    assert renewed is not None and renewed[1] == []
    assert await store.links([first_a], filters, compiler) == []
    await store.finish_links(a, renewed[0])
    # 逆向き（B → A）の関連も同じ表に置き、文書の削除は外部キーで関連の行と状態を消す。
    reverse = await store.claim_links(b, fingerprint("source-b"), "links-1", lease_seconds=300)
    assert reverse is not None
    await store.save_links(
        b,
        reverse[0],
        sources=[first_b],
        candidates=[first_a],
        links=[(first_b, first_a, "prerequisite", "合成の理由")],
        done=[f"reverse:{first_b}"],
    )
    assert await store.links([first_a], filters, compiler) == [(first_b, first_a, "prerequisite")]

    def count(connection: OracleConnectionProtocol) -> Any:
        return _fetch_one(
            connection,
            """SELECT (SELECT COUNT(*) FROM rag_wiki_links
                WHERE source_document_id IN (:a,:b) OR target_document_id IN (:a,:b)) AS links,
            (SELECT COUNT(*) FROM rag_wiki_link_documents WHERE document_id IN (:a,:b)) AS states
            FROM dual""",
            {"a": a, "b": b},
        )

    await oracle.delete_document(a)
    row = await oracle._run_transaction(count)
    assert row is not None and int(str(row["links"])) == 0 and int(str(row["states"])) == 1
    await oracle.delete_document(b)
    row = await oracle._run_transaction(count)
    assert row is not None and int(str(row["states"])) == 0
