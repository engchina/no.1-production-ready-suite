"""実 Oracle で原文の参照・有効 KB・公開前の検査と削除を確かめる。AI は合成応答。"""

import json
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pr_system_settings.auth.domain import CONFIGURED_SYSTEM_ADMIN_USER_UUID

from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute, _fetch_one
from app.clients.wiki_graph_store import WikiGraphStore
from app.clients.wiki_reference_index import DocumentNames
from app.clients.wiki_store import CURRENT_WIKI_SOURCE_SQL, WikiStore
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.wiki_models import WikiContext, WikiPage, fingerprint
from app.schemas.document import DocumentClassification
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document

pytestmark = pytest.mark.usefixtures("oracle_db")


async def test_set_enabled_never_saves_empty_build_actor() -> None:
    """担当は操作した利用者 → 保存済みの担当 → 構成管理者の順で決め、空にしない（#5746）。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-wiki-actor-{token}")

    async def actor() -> str | None:
        return dict(await store.enabled_knowledge_bases()).get(kb.id)

    # 利用者の無い文脈で、保存済みの担当も無ければ構成管理者にする。
    await store.set_enabled(kb.id, True)
    assert await actor() == CONFIGURED_SYSTEM_ADMIN_USER_UUID
    # 操作した利用者が分かれば、その利用者を担当にする。
    user = f"synthetic-user-{token}"
    context = set_audit_request_context(AuditRequestContext(user_uuid=user))
    try:
        await store.set_enabled(kb.id, True)
    finally:
        reset_audit_request_context(context)
    assert await actor() == user
    # 利用者の無い文脈では、保存済みの担当を残す（無効にしても、もう一度有効にしても）。
    await store.set_enabled(kb.id, False)
    await store.set_enabled(kb.id, True)
    assert await actor() == user


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


async def test_saved_vectors_are_valid_only_for_the_same_version_and_saved_page() -> None:
    """保存済みのベクトルは、同じ原文の版で案内を保存し直していない単位だけ返す（#5734）。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-resume-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token)
    await store.set_enabled(kb.id, True)
    source = await store.source_units(did)
    assert source is not None and len(source.units) > 1
    source = replace(source, compiler_fingerprint=fingerprint("compiler-a"))
    assert await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    pages = [
        WikiPage(
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
        for unit in source.units
    ]
    await store.checkpoint(source, lease, pages)
    assert await store.embedded_texts(source) == {}
    first = pages[0]
    await store.checkpoint(source, lease, [first], vectors=[_EMBEDDING], original_chars=5)
    # 埋め込みの入力は保存した時の文字数のもの。今の入力と違えば呼び出し側が作り直す。
    assert await store.embedded_texts(source) == {
        first.unit.unit_id: first.search_text(original_chars=5)
    }
    other_version = replace(source, source_fingerprint=fingerprint("other-version"))
    assert await store.embedded_texts(other_version) == {}
    # 案内を保存し直した単位は、前の案内から作ったベクトルを残さない。
    await store.checkpoint(source, lease, [first])
    assert await store.embedded_texts(source) == {}


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
    # 検索の経路（#4941）: 目録・案内・ベクトルの上位は、現在の原文の公開済みの案内だけを返す。
    scope = await store.resolve(filters)
    assert [row["document_id"] for row in scope.catalog] == [did]
    # 形式に関わらない版の属性（版の状態と分類の有効期間）を、どの文書にも渡す（#5446 の続き）。
    assert scope.catalog[0]["source_identities"] == [{"version_status": "current"}]
    await oracle.save_document_classification(
        did,
        DocumentClassification(effective_from=date(2026, 4, 1), effective_to=date(2027, 4, 1)),
    )
    assert (await store.resolve(filters)).catalog[0]["source_identities"] == [
        {"version_status": "current", "effective_from": "2026-04-01", "effective_to": "2027-04-01"}
    ]
    unit_ids = sorted(page.unit.unit_id for page in pages)
    assert sorted(p.unit.unit_id for p in await store.pages(scope)) == unit_ids
    top = await store.pages(scope, query_vector=_EMBEDDING, limit=1)
    assert len(top) == 1 and top[0].unit.unit_id in unit_ids
    by_unit = await store.pages(scope, unit_ids=unit_ids[:1], document_ids=[did])
    assert [p.unit.unit_id for p in by_unit] == unit_ids[:1]
    changed = replace(source, compiler_fingerprint=fingerprint("compiler-b"))
    assert not await store.enqueue(changed)
    assert await store.claim(changed, lease_seconds=300) is None
    reused = await store.resolve(filters)
    assert [row["document_id"] for row in reused.catalog] == [did]
    assert reused.sources == scope.sources
    assert len(await store.read_sources(await store.pages(reused), filters)) == len(pages)
    await store.set_enabled(first.id, False)
    # 無効にした KB からは読めないが、保存済みの案内は残り、別の有効な KB では使える。
    assert not await store.read_sources(pages, filters)
    assert not await store.document_ids(filters)
    assert not (await store.resolve(filters)).catalog
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


# 構築の条件の指紋は CHAR(64)（実際の値と同じ長さにする。短い値は空白で埋まる）。


async def test_page_retry_only_requeues_one_failed_member() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-retry-{token}")
    await store.set_enabled(kb.id, True)
    documents = []
    for suffix in ("a", "b"):
        did, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token + suffix)
        snapshot = await store.source_units(did)
        revision = await store.revision_fingerprint(did)
        assert snapshot and revision
        snapshot = replace(
            snapshot, compiler_fingerprint=fingerprint("compiler"), revision_fingerprint=revision
        )
        await store.enqueue(snapshot)
        lease = await store.claim(snapshot, lease_seconds=300)
        assert lease
        await store.finish(snapshot, lease, failure_code="timeout")
        documents.append((did, revision))
    (target, revision), (sibling, _) = documents
    before_sibling = await store.document_state(sibling)
    assert not await store.requeue_failed_document_pages("absent-kb", target, revision)
    assert await store.requeue_failed_document_pages(kb.id, target, revision)
    assert not await store.requeue_failed_document_pages(kb.id, target, revision)
    state = await store.document_state(target)
    assert state and state["status"] == "QUEUED"
    assert await store.document_state(sibling) == before_sibling


async def test_build_start_is_recorded_on_claim_and_kept_only_for_interrupted_builds() -> None:
    """実際に構築を始めた時刻を claim で一度だけ記録し、中断の続きだけで保つ（#5748）。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-deadline-{token}")
    await store.set_enabled(kb.id, True)
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token)
    snapshot = await store.source_units(did)
    revision = await store.revision_fingerprint(did)
    assert snapshot and revision
    snapshot = replace(
        snapshot, compiler_fingerprint=fingerprint("compiler"), revision_fingerprint=revision
    )

    async def started(table: str) -> Any:
        row = await oracle._fetch_one(
            f"SELECT build_started_at FROM {table} WHERE document_id=:id", {"id": did}
        )
        return row["build_started_at"] if row else None

    # 待機に入っただけでは数えない。
    await store.enqueue(snapshot)
    assert await started("rag_wiki_documents") is None
    assert await store.build_elapsed_seconds(did) == 0.0
    lease = await store.claim(snapshot, lease_seconds=300)
    assert lease
    first = await started("rag_wiki_documents")
    assert first is not None and 0.0 <= await store.build_elapsed_seconds(did) < 300
    # lease は所有者だけが延ばせる。
    assert await store.renew_lease(did, lease, lease_seconds=300)
    assert not await store.renew_lease(did, "other-lease", lease_seconds=300)
    # 中断からの続きは同じ開始を使う。
    await store.finish(snapshot, lease, failure_code="interrupted")
    assert await store.enqueue(snapshot)
    lease = await store.claim(snapshot, lease_seconds=300)
    assert lease and await started("rag_wiki_documents") == first
    # 時間切れの後の再試行は数え直す。
    await store.finish(snapshot, lease, failure_code="timeout")
    await store.requeue_failed({"knowledge_base_id": kb.id})
    assert await started("rag_wiki_documents") is None
    lease = await store.claim(snapshot, lease_seconds=300)
    assert lease and await started("rag_wiki_documents") is not None


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
    pages = _pages(source.units, context)
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    await store.finish(source, lease)
    return source, pages


def _pages(units: Any, context: str) -> list[WikiPage]:
    return [
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
        for u in units
    ]


async def annotate_references(oracle: OracleClient, did: str, names: list[str]) -> None:
    """取込の決定論の参照（別の文書を名前で指す）を、文書の chunk の metadata に入れる。"""
    targets = [
        {"label": name, "kind": "document", "key": "", "document_title": name} for name in names
    ]

    def operation(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            """UPDATE rag_chunks SET metadata_json=JSON_MERGEPATCH(metadata_json,:patch)
            WHERE document_id=:id""",
            {
                "id": did,
                "patch": json.dumps({"reference_targets_json": json.dumps(targets)}),
            },
        )

    await oracle._run_transaction(operation)


async def test_references_are_indexed_on_publish_and_resolved_by_name_in_scope() -> None:
    """別の文書への参照（#5784）: 公開と同じ取引で取込の参照と生成の参照を保存し、名前の比較形で
    逆に引ける。参照先は範囲の文書の名前で決まり、文書の削除で参照も消える。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-refs-{token}")
    a, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}a")
    b, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}b")
    await store.set_enabled(kb.id, True)
    name_b = f"summary-{token}b.pdf"
    await annotate_references(oracle, a, [name_b, "synthetic-missing.pdf"])
    _, pages_a = await _published(store, a, "合成の原則 syntheticrefa。")
    await _published(store, b, "合成の例外 syntheticrefb。")
    scope = await store.resolve({"knowledge_base_id": kb.id})
    first_a = pages_a[0].unit.unit_id

    found = await store.references([first_a], scope)
    assert (first_a, name_b, "", "reference") in found
    assert (first_a, "synthetic-missing.pdf", "", "reference") in found
    names = DocumentNames(scope.catalog, await store.document_headings(scope))
    # 範囲の文書は名前で決まり、範囲に無い文書の参照は決まらない（辿れない）。
    assert names.resolve(name_b, exclude=a) == [b]
    assert names.resolve("synthetic-missing.pdf", exclude=a) == []
    # 逆向き: 文書 B を名前で参照する単位を引く。
    incoming = await store.referencing(sorted(names.keys(b)), scope)
    assert {(unit, document) for unit, document, _, _ in incoming} >= {(first_a, a)}

    def count(connection: OracleConnectionProtocol) -> Any:
        return _fetch_one(
            connection,
            "SELECT COUNT(*) AS total FROM rag_wiki_references WHERE document_id=:a",
            {"a": a},
        )

    await oracle.delete_document(a)
    row = await oracle._run_transaction(count)
    assert row is not None and int(str(row["total"])) == 0


async def test_resolved_scope_reads_only_current_published_pages_in_scope() -> None:
    """範囲を 1 回で決めた後の軽い読取（#5499）: 今の保存版・公開済み・範囲の中の案内だけを返し、
    古い保存版・未公開・別の KB の文書の案内を返さない。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-scope-{token}")
    other = await oracle.create_knowledge_base(name=f"synthetic-scope-other-{token}")
    a, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}a")
    unpublished, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}u")
    outside, _, _ = await _indexed_document(oracle, knowledge_base_id=other.id, token=f"{token}o")
    await store.set_enabled(kb.id, True)
    await store.set_enabled(other.id, True)
    source_a, pages_a = await _published(store, a, "合成の案内 syntheticscope。")
    await _published(store, outside, "合成の案内 syntheticscope。")
    # 未公開: 案内と埋め込みは保存したが、公開（finish）していない文書。
    revision = await store.revision_fingerprint(unpublished)
    draft = await store.source_units(unpublished)
    assert revision is not None and draft is not None
    draft = replace(
        draft, compiler_fingerprint=source_a.compiler_fingerprint, revision_fingerprint=revision
    )
    await store.enqueue(draft)
    lease = await store.claim(draft, lease_seconds=300)
    assert lease
    draft_pages = _pages(draft.units, "合成の案内 syntheticscope。")
    await store.checkpoint(draft, lease, draft_pages, vectors=[_EMBEDDING] * len(draft_pages))

    filters = {"knowledge_base_id": kb.id}
    units_a = sorted(page.unit.unit_id for page in pages_a)

    async def read(scope: Any) -> tuple[list[str], list[str], list[str], list[str]]:
        ranked = await store.pages(scope, query_vector=_EMBEDDING, limit=50)
        return (
            sorted(p.unit.unit_id for p in await store.pages(scope)),
            sorted(p.unit.unit_id for p in ranked),
            # ベクトルの上位が読む案内の保存版（語境で見分ける）と文書。
            sorted({p.navigation.context for p in ranked}),
            sorted({p.unit.document_id for p in ranked}),
        )

    old_context, new_context = (
        ["合成の案内 syntheticscope。"],
        ["合成の新しい案内 syntheticrenewed。"],
    )

    scope = await store.resolve(filters)
    assert [row["document_id"] for row in scope.catalog] == [a]
    assert {did for did, _, _ in scope.sources} == {a}
    assert {value for _, _, value in scope.sources} == {source_a.source_fingerprint}
    assert await read(scope) == (units_a, units_a, old_context, [a])
    # 別の KB・未公開の文書は、ID で指定しても読まない。
    assert not await store.pages(scope, document_ids=[unpublished, outside])

    # 古い保存版: 同じ単位で新しい保存版の案内を作って公開すると、決め直した範囲は新しい保存版の
    # 案内だけを読み、古い保存版の案内（表に残る）を読まない。
    renewed = replace(source_a, source_fingerprint=fingerprint(f"renewed-{token}"))
    assert await store.enqueue(renewed)
    lease = await store.claim(renewed, lease_seconds=300)
    assert lease
    renewed_pages = _pages(renewed.units, "合成の新しい案内 syntheticrenewed。")
    await store.checkpoint(renewed, lease, renewed_pages, vectors=[_EMBEDDING] * len(pages_a))
    # 公開の前に決めた範囲は、作り直し中の新しい保存版の案内を読まない。
    assert await read(scope) == (units_a, units_a, old_context, [a])
    await store.finish(renewed, lease)
    current = await store.resolve(filters)
    assert {value for _, _, value in current.sources} == {renewed.source_fingerprint}
    assert await read(current) == (units_a, units_a, new_context, [a])


# 公開の条件の以前の形（JSON_TABLE の入れ子。#5533 で書き直した）。今の形と同じ判定かを見る。
_JSON_TABLE_REFERENCE_SQL = """NOT EXISTS (
    SELECT 1 FROM rag_chunk_sets current_set
    JOIN rag_document_recipes current_recipe ON current_recipe.recipe_id=current_set.recipe_id
    WHERE current_set.document_id=c.document_id AND current_set.is_active=1
      AND current_set.status='INDEXED' AND NOT EXISTS (
        SELECT 1 FROM JSON_TABLE(w.chunk_sets_json,'$[*]'
            COLUMNS (set_id VARCHAR2(64) PATH '$')) saved_set
        WHERE saved_set.set_id=current_set.chunk_set_id)
) AND NOT EXISTS (
    SELECT 1 FROM JSON_TABLE(w.chunk_sets_json,'$[*]'
        COLUMNS (set_id VARCHAR2(64) PATH '$')) saved_set
    WHERE NOT EXISTS (SELECT 1 FROM rag_chunk_sets current_set
        JOIN rag_document_recipes current_recipe
          ON current_recipe.recipe_id=current_set.recipe_id
        WHERE current_set.document_id=c.document_id AND current_set.is_active=1
          AND current_set.status='INDEXED' AND current_set.chunk_set_id=saved_set.set_id)
)"""


async def test_publication_requires_saved_chunk_sets_to_equal_current_sets() -> None:
    """公開の条件（#5533）: 構築の保存版の chunk_set の集合が、今の有効で索引済みの集合と同じとき
    だけ、目録・案内・構築状態・図に出す。以前の形（JSON_TABLE）と同じ判定になる。"""
    oracle, token = OracleClient(), uuid4().hex[:12]
    store, graph = WikiStore(oracle), WikiGraphStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-sets-{token}")
    did, _, first_set = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token)
    await store.set_enabled(kb.id, True)
    source, pages = await _published(store, did, "合成の案内 syntheticsets。")
    filters = {"knowledge_base_id": kb.id}
    units = sorted(page.unit.unit_id for page in pages)
    # 原文の chunk の無い 2 つ目のレシピの chunk_set（有効・索引済みにすると今の保存版に入る）。
    recipe = await oracle.create_document_recipe(did)
    second_set = f"cs_sets_{uuid4().hex[:16]}"
    await oracle.upsert_chunk_set(
        chunk_set_id=second_set,
        document_id=did,
        recipe_id=str(recipe["recipe_id"]),
        extraction_recipe_id=f"ex_{token}_2",
    )
    await oracle.mark_chunk_set_indexed(chunk_set_id=second_set, chunk_count=0, vector_count=0)

    async def update(sql: str, **binds: object) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(connection, sql, binds)

        await oracle._run_transaction(operation)

    async def judged() -> tuple[bool, bool]:
        """今の形と以前の形の判定。"""

        def operation(connection: OracleConnectionProtocol) -> Any:
            return _fetch_one(
                connection,
                f"""SELECT CASE WHEN {CURRENT_WIKI_SOURCE_SQL} THEN 1 ELSE 0 END AS now_value,
                CASE WHEN {_JSON_TABLE_REFERENCE_SQL} THEN 1 ELSE 0 END AS reference_value
                FROM rag_wiki_documents w JOIN rag_documents c ON c.document_id=w.document_id
                WHERE w.document_id=:id""",
                {"id": did},
            )

        row = await oracle._run_transaction(operation)
        assert row is not None
        return int(str(row["now_value"])) == 1, int(str(row["reference_value"])) == 1

    async def expect(published: bool) -> None:
        assert await judged() == (published, published)
        scope = await store.resolve(filters)
        assert [row["document_id"] for row in scope.catalog] == ([did] if published else [])
        assert {value for value, _, _ in scope.sources} == ({did} if published else set())
        assert sorted(p.unit.unit_id for p in await store.pages(scope)) == (
            units if published else []
        )
        statuses = await store.document_statuses(filters)
        assert [row["status"] for row in statuses] == ["READY" if published else "STALE"]
        overview = await graph.overview(kb.id)
        assert [doc["document_id"] for doc in overview["documents"]] == ([did] if published else [])

    async def save_sets(sets: list[str] | None) -> None:
        if sets is None:
            await update(
                "UPDATE rag_wiki_documents SET chunk_sets_json=NULL WHERE document_id=:id", id=did
            )
            return
        await update(
            "UPDATE rag_wiki_documents SET chunk_sets_json=JSON(:sets) WHERE document_id=:id",
            sets=json.dumps(sets),
            id=did,
        )

    await expect(True)
    # 今の保存版に chunk_set が増えた（保存版 ⊂ 今）。
    await update("UPDATE rag_chunk_sets SET is_active=1 WHERE chunk_set_id=:id", id=second_set)
    await expect(False)
    # 保存版にも同じ 2 つ（複数の chunk_set の一致）。
    await save_sets(sorted([first_set, second_set]))
    await expect(True)
    # 今の保存版から外れた（保存版 ⊃ 今）: 索引済みでない・有効でない chunk_set は数えない。
    await update(
        "UPDATE rag_chunk_sets SET status='INGESTING' WHERE chunk_set_id=:id", id=second_set
    )
    await expect(False)
    await update(
        "UPDATE rag_chunk_sets SET status='INDEXED',is_active=0 WHERE chunk_set_id=:id",
        id=second_set,
    )
    await expect(False)
    # 数は同じでも別の chunk_set。
    await save_sets([second_set])
    await expect(False)
    # 保存版の集合が無い。
    await save_sets(None)
    await expect(False)
    await save_sets([first_set])
    await expect(True)
    await oracle.delete_document(did)
