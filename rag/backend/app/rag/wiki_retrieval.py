"""通常検索の候補を受け取らず、原質問から Wiki を辿って現在の原文を返す独立した検索の経路（#4941）。

案内（語境・質問の言い換え・依存）は原文を探すためだけに使い、回答の根拠にはしない。返すのは
保存した原文の参照を、構成子・hash・所属・権限で照合し直した現在の原文だけ。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pr_backend_core.bounded_calls import chain_classifiers
from pr_backend_core.observability.tracing import SpanDisplay, observed_span
from pydantic import ValidationError

from app.clients.ai_calls import rag_error_kind
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient, _filter_id_values
from app.clients.wiki_reference_index import DocumentNames, section_pages
from app.clients.wiki_store import WikiScope, WikiStore
from app.config import Settings
from app.rag.span_display import ANSWER_STEP_KIND
from app.rag.wiki_models import WIKI_RELATION_PRIORITY, WikiPage
from app.schemas.search import RetrievedChunk

# 回答の診断に出す原文の chunk ID の数の上限（由来ごとの一覧も。#5518）。数は ``source_count``。
_SOURCE_DIAGNOSTICS_LIMIT = 200

# 失敗の種類（空の応答・schema に合わない出力は ``invalid_output``）。取込の呼び出しと同じ分類。
_classify = chain_classifiers(rag_error_kind)


def failure_reason(exc: BaseException) -> str:
    """Wiki の経路の失敗の、記録に残せる短い理由（#5528）。

    失敗の種類（``invalid_output``・``timeout`` など）に、schema に合わない出力なら最初の誤りの
    種類と位置（項目名と番号）を足す。例外の本文・入力の値・応答の本文は入れない。
    """
    kind = _classify(exc).kind.value
    if isinstance(exc, ValidationError) and exc.error_count():
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"{kind}:{first['type']}@{location}"
    return kind


class WikiSourceChanged(RuntimeError):
    """案内を読んだ後に設定・所属・原文が変わった。"""


@contextmanager
def _stage(name: str, seconds: dict[str, float]) -> Iterator[None]:
    """Wiki の経路の段を回答の工程と同じ span で記録する（時間のかかる段を見る。#5437）。

    span の中の同じ範囲の秒を ``seconds`` の段の名前に足す（span を保存しない評価でも回答の診断で
    時間を見る。#5518）。失敗・時間切れで抜けた段も、それまでの秒を残す。
    """
    with observed_span(
        "rag.engine.stage",
        attributes={"pr.stage": name},
        display=SpanDisplay(kind=ANSWER_STEP_KIND, params={"name": name}),
    ):
        started = time.perf_counter()
        try:
            yield
        finally:
            seconds[name] = round(seconds.get(name, 0.0) + time.perf_counter() - started, 3)


@dataclass(frozen=True)
class WikiSearchResult:
    chunks: tuple[RetrievedChunk, ...] = ()
    pages: tuple[WikiPage, ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)


def _relations(
    pages: Sequence[WikiPage], edges: set[tuple[str, str, str]], names: Mapping[str, str]
) -> dict[str, list[dict[str, Any]]]:
    """辿りで見た関係のうち、両端が回答の原文に入るものを、単位ごとの相手の場所つきで返す（#5500）。

    ``role_of`` は関係の役割を持つ側（``this`` はこの単位、``other`` は相手）。相手は文書名と
    章節で示す（根拠の ID は根拠を描くときに決まるので使わない）。関係の種類の順（例外・前提を先）
    に並べる。
    """
    by_id = {page.unit.unit_id: page for page in pages}
    priority = {relation: index for index, relation in enumerate(WIKI_RELATION_PRIORITY)}
    found: dict[str, list[tuple[int, str, str, str]]] = {}
    for source, target, relation in edges:
        if source == target or source not in by_id or target not in by_id:
            continue
        rank = priority.get(relation, len(priority))
        found.setdefault(source, []).append((rank, relation, "other", target))
        found.setdefault(target, []).append((rank, relation, "this", source))
    result: dict[str, list[dict[str, Any]]] = {}
    for uid, rows in found.items():
        entries: list[dict[str, Any]] = []
        for _, relation, role_of, other in sorted(set(rows)):
            unit = by_id[other].unit
            entry = {
                "relation": relation,
                "role_of": role_of,
                "document_name": names.get(unit.document_id) or unit.document_id,
                "section_path": list(unit.section_path),
            }
            if entry not in entries:
                entries.append(entry)
        result[uid] = entries
    return result


# 別の文書への参照から辿る単位の数（参照先の文書ごと）と、その中から章節を探す候補の数（#5784）。
_REFERENCE_UNITS = 3
_REFERENCE_CANDIDATES = 20


class WikiRetriever:
    def __init__(
        self,
        settings: Settings,
        oracle: OracleClient,
        genai: OciGenAiClient,
        *,
        store: WikiStore | None = None,
    ) -> None:
        self.settings, self.oracle, self.genai = settings, oracle, genai
        self.store = store or WikiStore(oracle)

    async def scope(
        self, profile_id: str | None, filters: Mapping[str, str]
    ) -> dict[str, str] | None:
        """プロファイルが有効で Wiki の使用を指定していれば、その有効な KB に絞った検索条件。"""
        if not profile_id:
            return None
        profile = await self.oracle.get_search_answer_profile(profile_id)
        if (
            profile is None
            or profile.status != "ACTIVE"
            or profile.config.query.wiki_search_enabled is not True
        ):
            return None
        available = {kb.id for kb in profile.knowledge_bases if kb.status == "ACTIVE"}
        requested = set(_filter_id_values(filters.get("knowledge_base_id")))
        selected = available & requested if requested else available
        return {**filters, "knowledge_base_id": ",".join(sorted(selected))} if selected else None

    async def current_sources(
        self, profile_id: str | None, filters: Mapping[str, str], pages: Sequence[WikiPage]
    ) -> list[RetrievedChunk]:
        """設定・所属・権限・原文の保存版をいま確かめ直し、すべての単位の原文が揃うときだけ返す。"""
        scoped = await self.scope(profile_id, filters)
        if scoped is None:
            raise WikiSourceChanged("Wiki の使用の設定または対象の範囲が変わりました。")
        chunks = await self.store.read_sources(pages, scoped)
        if {chunk.chunk_id for chunk in chunks} != {
            sid for page in pages for sid in page.unit.source_chunk_ids
        }:
            raise WikiSourceChanged("Wiki の原文の保存版・所属・権限が変わりました。")
        return chunks

    async def follow(
        self,
        scope: WikiScope,
        pages: dict[str, WikiPage],
        selected: Sequence[str],
        vector: Sequence[float] | None = None,
    ) -> tuple[list[str], list[dict[str, int]], int, set[tuple[str, str, str]], dict[str, str]]:
        """選んだ単位から、同じ文書の依存と、別の文書への参照（両方の向き）を辿る（#5492・#5784）。

        別の文書への参照は、文書の内容に書かれた参照（``rag_wiki_references``）を、今の範囲の文書の
        名前と照合して決める（範囲に無い文書への参照は辿らない）。出る向きは単位が名前を挙げた
        文書の単位、入る向きはその単位の文書を名前で参照するほかの文書の単位。参照先の単位は、
        章節の表記に当たる単位、無ければ質問に近い単位（``vector``）を ``_REFERENCE_UNITS`` 件まで。

        ``RAG_WIKI_LINK_DEPTH`` 段まで、段ごとに関係の種類の順（例外・前提を先）で、まだ読んでいない
        単位を ``RAG_WIKI_LINK_MAX_UNITS`` まで足す。一度見た単位は辿り直さない（循環を止める）。
        読めない単位（未公開・古い保存版・範囲の外）は数に入れない。辿るのは DB だけで、
        モデルは呼ばない。
        返す値は（足した単位 ID・段ごとの数・読めなかった単位の数・辿りで見た関係・
        足した単位の由来）。関係は（起点, 終点, 関係の種類）で、終点が起点に対してその役割を持つ
        （#5500）。由来は足した単位 ID ごとの ``dependency``（案内の依存）か ``link``（別の文書
        への参照）で、段ごとの数と同じ値（#5518）。``pages`` に読んだ案内を足す。
        """
        opts, store = self.settings, self.store
        budget = opts.rag_wiki_link_max_units
        priority = {relation: index for index, relation in enumerate(WIKI_RELATION_PRIORITY)}
        seen, frontier = set(selected), list(selected)
        added: list[str] = []
        origins: dict[str, str] = {}
        hops: list[dict[str, int]] = []
        missing = 0
        edges: set[tuple[str, str, str]] = set()
        names: DocumentNames | None = None
        for depth in range(1, opts.rag_wiki_link_depth + 1):
            if not frontier or len(added) >= budget:
                break
            offers: list[tuple[str, str, str]] = [
                (dependency.unit_id, dependency.relation, "dependency")
                for uid in frontier
                for dependency in pages[uid].navigation.dependencies
            ]
            edges.update(
                (uid, dependency.unit_id, dependency.relation)
                for uid in frontier
                for dependency in pages[uid].navigation.dependencies
            )
            names = names or DocumentNames(scope.catalog, await store.document_headings(scope))
            for source, target, relation in await self._references(
                scope, pages, frontier, names, vector
            ):
                edges.add((source, target, relation))
                offers.append((source if target in frontier else target, relation, "link"))
            found: dict[str, tuple[int, str]] = {}
            for uid, relation, kind in offers:
                rank = priority.get(relation, len(priority))
                if uid not in seen and (uid not in found or rank < found[uid][0]):
                    found[uid] = (rank, kind)
            ordered = sorted(found, key=lambda uid: found[uid][0])
            unknown = [uid for uid in ordered if uid not in pages]
            if unknown:
                pages.update(
                    (page.unit.unit_id, page) for page in await store.pages(scope, unit_ids=unknown)
                )
            readable = [uid for uid in ordered if uid in pages]
            missing += len(ordered) - len(readable)
            chosen = readable[: budget - len(added)]
            seen.update(ordered)
            added.extend(chosen)
            origins.update((uid, found[uid][1]) for uid in chosen)
            frontier = chosen
            hops.append(
                {
                    "depth": depth,
                    "dependency_units": sum(found[uid][1] == "dependency" for uid in chosen),
                    "link_units": sum(found[uid][1] == "link" for uid in chosen),
                }
            )
        return added, hops, missing, edges, origins

    async def _references(
        self,
        scope: WikiScope,
        pages: dict[str, WikiPage],
        frontier: Sequence[str],
        names: DocumentNames,
        vector: Sequence[float] | None,
    ) -> list[tuple[str, str, str]]:
        """起点の単位の、別の文書への参照の関係（起点, 終点, 関係）。両方の向き。"""
        store = self.store
        found: list[tuple[str, str, str]] = []
        units: dict[tuple[str, str], list[str]] = {}
        for source, name, section, relation in await store.references(frontier, scope):
            own = pages[source].unit.document_id
            for document_id in names.resolve(name, exclude=own):
                key = (document_id, section)
                if key not in units:
                    units[key] = await self._reference_units(
                        scope, pages, document_id, section, vector
                    )
                found.extend((source, target, relation) for target in units[key])
        # 入る向き: 起点の文書を名前で参照する、範囲の中のほかの文書の単位（#5784）。
        first: dict[str, str] = {}
        for uid in frontier:
            first.setdefault(pages[uid].unit.document_id, uid)
        # 種類の呼び名で書いた参照も拾い、当たる文書は出る向きと同じ照合で決める（#5795）。
        keys = sorted({key for document_id in first for key in names.keys(document_id)})
        aliases = sorted({alias for document_id in first for alias in names.aliases(document_id)})
        for source, document_id, name_key, relation in await store.referencing(
            keys, scope, contained_in=aliases
        ):
            found.extend(
                (source, first[target], relation)
                for target in names.resolve_key(name_key, exclude=document_id)
                if target in first
            )
        return found

    async def _reference_units(
        self,
        scope: WikiScope,
        pages: dict[str, WikiPage],
        document_id: str,
        section: str,
        vector: Sequence[float] | None,
    ) -> list[str]:
        """参照先の文書の、辿る単位（章節の表記に当たる単位、無ければ質問に近い単位）。"""
        # ponytail: 質問に近い上位 _REFERENCE_CANDIDATES 件の中で章節を探す。上位に無い章節は
        # 文書の先頭側の単位で代える。章節の索引で引く形は要るときに足す。
        candidates = await self.store.pages(
            scope,
            document_ids=[document_id],
            query_vector=vector,
            limit=_REFERENCE_CANDIDATES if vector is not None else None,
        )
        pages.update((page.unit.unit_id, page) for page in candidates)
        return [page.unit.unit_id for page in section_pages(candidates, section)[:_REFERENCE_UNITS]]

    async def search(
        self,
        question: str,
        *,
        profile_id: str | None,
        filters: Mapping[str, str],
        timeout: float | None,
        stage_seconds: dict[str, float] | None = None,
    ) -> WikiSearchResult:
        """``stage_seconds`` には段（``_stage``）ごとの秒を書く（失敗・時間切れでも、それまでの
        段）。"""
        scoped = await self.scope(profile_id, filters)
        if scoped is None:
            return WikiSearchResult(trace={"status": "profile_disabled"})
        limit = self.settings.rag_wiki_query_timeout_seconds
        seconds = {} if stage_seconds is None else stage_seconds
        async with asyncio.timeout(limit if timeout is None else min(limit, timeout)):
            return await self._search(question, profile_id, scoped, seconds)

    async def _search(
        self,
        question: str,
        profile_id: str | None,
        scope: dict[str, str],
        seconds: dict[str, float],
    ) -> WikiSearchResult:
        opts, store = self.settings, self.store
        # 範囲（有効な KB・権限・公開・今の保存版）を評価するのはここの 1 回だけ。後の読取は
        # 決めた案内の集合の中だけを読み、回答に使う原文は current_sources で照合し直す（#5499）。
        with _stage("Wiki: 目録の読み込み", seconds):
            wiki = await store.resolve(scope)
        catalog = list(wiki.catalog)
        if not catalog:
            return WikiSearchResult(trace={"status": "not_ready"})
        with _stage("Wiki: 質問のベクトル化", seconds):
            vector = (await self.genai.embed([question], input_type="SEARCH_QUERY"))[0]
        # 候補は案内のベクトル検索の順位だけ（全文検索は通常検索と重なるので使わない。#5825）。
        with _stage("Wiki: 案内の候補の検索", seconds):
            candidates = await store.pages(
                wiki, query_vector=vector, limit=opts.rag_wiki_candidates
            )
        # 公開された索引の上位の原文単位を辿りの起点にする（選択のモデルは呼ばない。#5731）。
        roots = candidates[: opts.rag_wiki_selected_units]
        pages = {page.unit.unit_id: page for page in candidates}
        selected = list(dict.fromkeys(page.unit.unit_id for page in roots))
        # 案内の依存（同じ文書）と文書をまたぐ関連（出る向き）を、予算の内で段ごとに辿る（#5492）。
        with _stage("Wiki: 依存と関連の辿り", seconds):
            followed, hops, missing, edges, origins = await self.follow(
                wiki, pages, selected, vector
            )
        with _stage("Wiki: 原文の読み込み", seconds):
            required = [pages[uid] for uid in [*selected, *followed]]
            originals = await self.current_sources(profile_id, scope, required)
        identities = {row["document_id"]: row["source_identities"] for row in catalog}
        names = {row["document_id"]: row["title"] for row in catalog}
        relations = _relations(required, edges, names)
        units = {sid: page.unit.unit_id for page in required for sid in page.unit.source_chunk_ids}
        chunks = tuple(
            chunk.model_copy(
                update={
                    "metadata": {
                        **chunk.metadata,
                        "wiki_source": True,
                        "source_identities": identities.get(chunk.document_id, []),
                        **(
                            {"wiki_relations": found}
                            if (found := relations.get(units[chunk.chunk_id]))
                            else {}
                        ),
                    }
                }
            )
            for chunk in originals
        )

        def source_ids(unit_ids: Sequence[str]) -> list[str]:
            ids = (sid for uid in unit_ids for sid in pages[uid].unit.source_chunk_ids)
            return list(dict.fromkeys(ids))[:_SOURCE_DIAGNOSTICS_LIMIT]

        return WikiSearchResult(
            chunks,
            tuple(required),
            {
                "status": "ready",
                "catalog_documents": len(catalog),
                "candidate_units": len(candidates),
                "selected_documents": len({page.unit.document_id for page in roots}),
                "selected_units": len(selected),
                "dependency_units": len(followed),
                "missing_dependency_units": missing,
                "link_hops": hops,
                "source_count": len(chunks),
                # 回答の根拠に渡した原文の chunk ID（通常検索との重複を外す前）と、案内の単位の
                # 由来（選んだ単位・依存・関連）ごとの chunk ID。記録から重複率を出す（#5518）。
                "source_chunk_ids": [chunk.chunk_id for chunk in chunks][
                    :_SOURCE_DIAGNOSTICS_LIMIT
                ],
                "source_chunk_ids_by_origin": {
                    "selected": source_ids(selected),
                    "dependency": source_ids(
                        [uid for uid in followed if origins[uid] == "dependency"]
                    ),
                    "link": source_ids([uid for uid in followed if origins[uid] == "link"]),
                },
            },
        )
