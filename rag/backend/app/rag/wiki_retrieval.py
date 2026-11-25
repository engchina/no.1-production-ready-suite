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
        self, scope: WikiScope, pages: dict[str, WikiPage], selected: Sequence[str]
    ) -> tuple[list[str], list[dict[str, int]], int, set[tuple[str, str, str]]]:
        """選んだ単位から、同じ文書の依存を辿る（#5492）。別の文書への参照は辿らない（#5841）。

        ``RAG_WIKI_LINK_DEPTH`` 段まで、段ごとに関係の種類の順（例外・前提を先）で、まだ読んでいない
        単位を ``RAG_WIKI_LINK_MAX_UNITS`` まで足す。一度見た単位は辿り直さない（循環を止める）。
        読めない単位（未公開・古い保存版・範囲の外）は数に入れない。辿るのは DB だけで、
        モデルは呼ばない。
        返す値は（足した単位 ID・段ごとの数・読めなかった単位の数・辿りで見た関係）。関係は
        （起点, 終点, 関係の種類）で、終点が起点に対してその役割を持つ（#5500）。``pages`` に
        読んだ案内を足す。
        """
        opts, store = self.settings, self.store
        budget = opts.rag_wiki_link_max_units
        priority = {relation: index for index, relation in enumerate(WIKI_RELATION_PRIORITY)}
        seen, frontier = set(selected), list(selected)
        added: list[str] = []
        hops: list[dict[str, int]] = []
        missing = 0
        edges: set[tuple[str, str, str]] = set()
        for depth in range(1, opts.rag_wiki_link_depth + 1):
            if not frontier or len(added) >= budget:
                break
            found: dict[str, int] = {}
            for uid in frontier:
                for dependency in pages[uid].navigation.dependencies:
                    edges.add((uid, dependency.unit_id, dependency.relation))
                    rank = priority.get(dependency.relation, len(priority))
                    target = dependency.unit_id
                    if target not in seen and (target not in found or rank < found[target]):
                        found[target] = rank
            ordered = sorted(found, key=lambda uid: found[uid])
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
            frontier = chosen
            hops.append({"depth": depth, "dependency_units": len(chosen)})
        return added, hops, missing, edges

    async def search(
        self,
        question: str,
        *,
        profile_id: str | None,
        filters: Mapping[str, str],
        timeout: float | None,
        stage_seconds: dict[str, float] | None = None,
        top_k: int | None = None,
    ) -> WikiSearchResult:
        """``stage_seconds`` には段（``_stage``）ごとの秒を書く（失敗・時間切れでも、それまでの
        段）。``top_k`` は起点数（通常検索と同じ定義。省略時は ``RAG_SEARCH_TOP_K_DEFAULT``）。"""
        scoped = await self.scope(profile_id, filters)
        if scoped is None:
            return WikiSearchResult(trace={"status": "profile_disabled"})
        limit = self.settings.rag_wiki_query_timeout_seconds
        seconds = {} if stage_seconds is None else stage_seconds
        roots = max(1, int(top_k or self.settings.rag_search_top_k_default))
        async with asyncio.timeout(limit if timeout is None else min(limit, timeout)):
            return await self._search(question, profile_id, scoped, seconds, roots)

    async def _roots(
        self, question: str, candidates: Sequence[WikiPage], top_k: int
    ) -> tuple[list[WikiPage], dict[str, float], dict[str, Any]]:
        """候補を原文で rerank し、通常検索と同じ関連度の下限と起点数で起点を決める（#5831）。

        下限は ``RAG_RERANK_MIN_RELEVANCE_SCORE``。1 件も届かないときだけ
        ``RAG_RERANK_MIN_RELEVANCE_FALLBACK_RATIO`` を掛けて 1 回下げる。下限の後に文書ごとの
        起点数の上限（``RAG_MAX_ANCHORS_PER_DOCUMENT``。プロファイルの上書きを含む）を掛け、
        起点数で切る（#5839）。rerank が無効・失敗の
        ときは、通常検索と同じくベクトルの順のまま起点数で切る（Wiki の経路は止めない）。
        返す値は（起点・起点の単位 ID ごとの rerank の点数・診断）。
        """
        opts = self.settings
        if not candidates or not opts.rag_rerank_enabled:
            return list(candidates[:top_k]), {}, {"rerank": "disabled"}
        try:
            ranked = await self.genai.rerank(
                question, [page.unit.text for page in candidates], len(candidates)
            )
        except Exception as exc:  # noqa: BLE001 - rerank の失敗で Wiki の経路を止めない。
            return (
                list(candidates[:top_k]),
                {},
                {"rerank": "failed", "rerank_failure_code": type(exc).__name__},
            )
        floor = opts.rag_rerank_min_relevance_score
        ratio = opts.rag_rerank_min_relevance_fallback_ratio
        if floor > 0 and 0 < ratio < 1 and not any(score >= floor for _, score in ranked):
            floor *= ratio
        kept = sorted(
            ((index, score) for index, score in ranked if score >= floor),
            key=lambda item: (-item[1], item[0]),
        )
        relevant = len(kept)
        # 通常検索の起点と同じく、下限の後・起点数の切りの前に文書ごとの上限を掛ける
        # （#5839。0 は上限なし）。
        per_document = opts.rag_max_anchors_per_document
        if per_document > 0:
            counts: dict[str, int] = {}
            capped = []
            for index, score in kept:
                document_id = candidates[index].unit.document_id
                if counts.get(document_id, 0) < per_document:
                    counts[document_id] = counts.get(document_id, 0) + 1
                    capped.append((index, score))
            kept = capped
        return (
            [candidates[index] for index, _ in kept[:top_k]],
            {candidates[index].unit.unit_id: score for index, score in kept[:top_k]},
            {
                "rerank": "completed",
                "rerank_min_relevance_score": floor,
                "reranked_units": len(ranked),
                "relevant_units": relevant,
            },
        )

    async def _search(
        self,
        question: str,
        profile_id: str | None,
        scope: dict[str, str],
        seconds: dict[str, float],
        top_k: int,
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
        # 件数と類似度の下限は通常検索のベクトル検索と同じ設定を使う（#5831）。
        with _stage("Wiki: 案内の候補の検索", seconds):
            candidates = await store.pages(
                wiki,
                query_vector=vector,
                limit=opts.rag_retrieval_query_limit,
                min_similarity=opts.rag_min_similarity,
            )
        # 原文の rerank と関連度の下限を通った単位を、点数の順に起点数まで辿りの起点にする
        # （選択のモデルは呼ばない。#5731・#5831）。
        with _stage("Wiki: 起点の再ランキング", seconds):
            roots, scores, rerank_trace = await self._roots(question, candidates, top_k)
        vector_ranks = {page.unit.unit_id: rank for rank, page in enumerate(candidates, 1)}
        pages = {page.unit.unit_id: page for page in candidates}
        selected = list(dict.fromkeys(page.unit.unit_id for page in roots))
        # 案内の依存（同じ文書）を、予算の内で段ごとに辿る（#5492）。別の文書への参照は辿らない
        # （#5841）。
        with _stage("Wiki: 依存の辿り", seconds):
            followed, hops, missing, edges = await self.follow(wiki, pages, selected)
        with _stage("Wiki: 原文の読み込み", seconds):
            required = [pages[uid] for uid in [*selected, *followed]]
            originals = await self.current_sources(profile_id, scope, required)
        identities = {row["document_id"]: row["source_identities"] for row in catalog}
        names = {row["document_id"]: row["title"] for row in catalog}
        relations = _relations(required, edges, names)
        units = {sid: page.unit.unit_id for page in required for sid in page.unit.source_chunk_ids}
        # 起点（rerank の順）、辿った単位の順に並べる。起点には Wiki のベクトル検索の順位と
        # rerank の点数を、辿った単位には由来（依存）を付ける（合わせ込みの順と画面のバッジ。
        # #5831）。
        order = {page.unit.unit_id: index for index, page in enumerate(required)}
        dependencies = set(followed)

        def origin(unit_id: str) -> dict[str, Any]:
            if unit_id in dependencies:
                return {"wiki_origin": "dependency"}
            return {
                "wiki_origin": "root",
                "wiki_rank": vector_ranks[unit_id],
                **({"wiki_rerank_score": scores[unit_id]} if unit_id in scores else {}),
            }

        chunks = tuple(
            chunk.model_copy(
                update={
                    "metadata": {
                        **chunk.metadata,
                        "wiki_source": True,
                        **origin(units[chunk.chunk_id]),
                        "source_identities": identities.get(chunk.document_id, []),
                        **(
                            {"wiki_relations": found}
                            if (found := relations.get(units[chunk.chunk_id]))
                            else {}
                        ),
                    }
                }
            )
            for chunk in sorted(originals, key=lambda chunk: order[units[chunk.chunk_id]])
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
                **rerank_trace,
                "selected_documents": len({page.unit.document_id for page in roots}),
                "selected_units": len(selected),
                "dependency_units": len(followed),
                "missing_dependency_units": missing,
                "link_hops": hops,
                "source_count": len(chunks),
                # 回答の根拠に渡した原文の chunk ID（通常検索との重複を外す前）と、案内の単位の
                # 由来（選んだ単位・依存）ごとの chunk ID。記録から重複率を出す（#5518）。
                "source_chunk_ids": [chunk.chunk_id for chunk in chunks][
                    :_SOURCE_DIAGNOSTICS_LIMIT
                ],
                "source_chunk_ids_by_origin": {
                    "selected": source_ids(selected),
                    "dependency": source_ids(followed),
                },
            },
        )
