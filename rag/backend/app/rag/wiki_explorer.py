"""公開済みの Wiki をプロファイルまたは KB の範囲で手動検索し、原文と照合する。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

from fastapi import HTTPException

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.clients.wiki_graph_index import section_id
from app.clients.wiki_graph_store import WikiGraphStore
from app.clients.wiki_store import WikiStore
from app.config import Settings, get_settings
from app.rag.request_context import current_audit_request_context
from app.rag.search_answer_profile_config import resolve_search_answer_profile_settings
from app.rag.stored_answer import stored_citation
from app.rag.wiki_models import WikiPage
from app.schemas.knowledge_base import KnowledgeBaseRef
from app.schemas.search import RetrievedChunk
from app.schemas.wiki_graph import WikiOriginal, WikiSearchHit, WikiSearchHits, WikiSourceRef

Method = Literal["hybrid", "vector", "keyword"]


@dataclass(frozen=True)
class WikiHandoff:
    profile_id: str
    refs: tuple[WikiSourceRef, ...]
    chunks: tuple[RetrievedChunk, ...]
    # True = 選んだ原文だけを根拠にする（#5981）。検索・自動の Wiki 検索・検索拡張・根拠の拡張を
    # しない。
    only: bool = False

    async def recheck(self, oracle: OracleClient, filters: Mapping[str, str]) -> None:
        current = await WikiExplorer.for_profile(oracle, self.profile_id)
        await current.selected(self.refs, filters)


def require_selection_within_limit(count: int, settings: Settings) -> None:
    """選んだ原文の数が設定の上限（`RAG_WIKI_SELECTION_MAX_UNITS`）を超えていれば 422（#5981）。"""
    if count > settings.rag_wiki_selection_max_units:
        raise HTTPException(
            422, f"一度に選べる原文は {settings.rag_wiki_selection_max_units} 件までです。"
        )


class WikiHandoffOptions(TypedDict, total=False):
    wiki_handoff: WikiHandoff


@dataclass
class WikiExplorer:
    oracle: OracleClient
    knowledge_bases: list[KnowledgeBaseRef]
    settings: Settings

    @classmethod
    async def for_profile(
        cls, oracle: OracleClient, profile_id: str, kb: str | None = None
    ) -> "WikiExplorer":
        access = current_audit_request_context()
        if (
            access.allowed_search_answer_profile_ids is not None
            and profile_id not in access.allowed_search_answer_profile_ids
        ):
            raise HTTPException(404, "検索・回答プロファイルが見つかりません。")
        profile = await oracle.get_search_answer_profile(profile_id)
        if not profile or profile.status != "ACTIVE":
            raise HTTPException(404, "検索・回答プロファイルが見つかりません。")
        allowed = access.allowed_knowledge_base_ids
        scope = [
            KnowledgeBaseRef(id=item.id, name=item.name)
            for item in profile.knowledge_bases
            if item.status == "ACTIVE"
            and (allowed is None or item.id in allowed)
            and (kb is None or kb == item.id)
        ]
        if kb and not scope:
            raise HTTPException(404, "対象が見つかりません。")
        settings, _ = resolve_search_answer_profile_settings(
            get_settings(), profile.config, apply_answer_model=False
        )
        return cls(oracle, scope, settings)

    @property
    def ids(self) -> str:
        if not self.knowledge_bases:
            raise HTTPException(409, "この範囲で検索できるナレッジベースがありません。")
        return ",".join(item.id for item in self.knowledge_bases)

    @property
    def graph(self) -> WikiGraphStore:
        return WikiGraphStore(self.oracle)

    async def search(
        self, question: str, method: Method, offset: int, limit: int
    ) -> WikiSearchHits:
        ids = self.ids
        hits: dict[str, WikiSearchHit] = {}
        depth = self.settings.rag_retrieval_query_limit
        total = 0
        if method != "vector":
            keywords = await self.graph.search(
                ids,
                question,
                offset=offset if method == "keyword" else 0,
                limit=limit if method == "keyword" else depth,
            )
            total = keywords["total"]
            for rank, item in enumerate(
                keywords["items"], 1 + (offset if method == "keyword" else 0)
            ):
                hits[item["id"]] = WikiSearchHit(**item, keyword_rank=rank)
        if method != "keyword":
            store = WikiStore(self.oracle)
            scope = await store.resolve({"knowledge_base_id": ids}, require_build_enabled=False)
            if scope.sources:
                vector = (
                    await OciGenAiClient(self.settings).embed([question], input_type="SEARCH_QUERY")
                )[0]
                pages = await store.pages(
                    scope,
                    query_vector=vector,
                    min_similarity=self.settings.rag_min_similarity,
                    limit=depth,
                )
                names = {row["document_id"]: row["title"] for row in scope.catalog}
                for rank, page in enumerate(pages, 1):
                    unit = page.unit
                    hit = hits.setdefault(
                        unit.unit_id,
                        WikiSearchHit(
                            id=unit.unit_id,
                            label=unit.title,
                            document_id=unit.document_id,
                            document_name=names[unit.document_id],
                        ),
                    )
                    hit.vector_rank = rank
            total = len(hits)
        weights = self.settings.rag_rrf_method_ratio
        k = self.settings.rag_rrf_k

        def score(item: WikiSearchHit) -> float:
            return sum(
                weight / (k + rank)
                for weight, rank in zip(weights, (item.vector_rank, item.keyword_rank), strict=True)
                if rank is not None
            )

        ordered = list(hits.values())
        if method != "keyword":
            ordered = sorted(
                ordered, key=lambda hit: (-score(hit), hit.vector_rank or depth + 1, hit.id)
            )[offset : offset + limit]
        memberships = await self.oracle.knowledge_base_ids_by_retrieved_document(
            list(dict.fromkeys(hit.document_id for hit in ordered)),
            [kb.id for kb in self.knowledge_bases],
        )
        for hit in ordered:
            hit.knowledge_base_ids = sorted(memberships.get(hit.document_id, set()))
        return WikiSearchHits(items=ordered, total=total, offset=offset)

    async def original(self, unit_id: str) -> WikiOriginal:
        row = (await self.graph.pages_by_unit(self.ids, [unit_id])).get(unit_id)
        if row is None:
            raise HTTPException(404, "原文が見つかりません。")
        page: WikiPage = row["page"]
        chunks = await self._originals([page], {})
        return WikiOriginal(
            id=unit_id,
            parent_id=section_id(page.unit.document_id, page.unit.section_path)
            if page.unit.section_path
            else None,
            label=page.unit.title,
            document_id=page.unit.document_id,
            document_name=row["document_name"],
            path=page.unit.section_path,
            source=WikiSourceRef(unit_id=unit_id, source_hash=page.unit.source_hash),
            chunks=[stored_citation(chunk) for chunk in chunks],
            context=page.navigation.context,
            possible_questions=page.navigation.possible_questions,
            unresolved=page.navigation.unresolved,
        )

    async def selected(
        self, refs: Sequence[WikiSourceRef], filters: Mapping[str, str] | None = None
    ) -> list[RetrievedChunk]:
        rows = await self.graph.pages_by_unit(self.ids, [ref.unit_id for ref in refs])
        pages = []
        for ref in refs:
            row = rows.get(ref.unit_id)
            if row is None or row["page"].unit.source_hash != ref.source_hash:
                raise HTTPException(
                    409,
                    "選択した原文が更新されたか、閲覧できなくなりました。"
                    "Wiki 検索で選び直してください。",
                )
            pages.append(row["page"])
        return await self._originals(pages, filters or {})

    async def _originals(
        self, pages: Sequence[WikiPage], filters: Mapping[str, str]
    ) -> list[RetrievedChunk]:
        ids = self.ids
        if requested := filters.get("knowledge_base_id"):
            permitted = set(ids.split(",")) & set(requested.split(","))
            if not permitted:
                raise HTTPException(409, "選択した原文を現在の検索範囲で確認できません。")
            ids = ",".join(sorted(permitted))
        chunks = await WikiStore(self.oracle).read_sources(
            pages, {**filters, "knowledge_base_id": ids}, require_build_enabled=False
        )
        if {c.chunk_id for c in chunks} != {sid for p in pages for sid in p.unit.source_chunk_ids}:
            raise HTTPException(
                409, "選択した原文を確認できません。Wiki 検索で選び直してください。"
            )
        return chunks
