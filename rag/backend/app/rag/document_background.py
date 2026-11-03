"""最終選択後の文書背景を、正確な版と検索範囲で取得する。"""

from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.knowledge.document_metadata import extract_first_page_context

from app.rag.context_page_scope import contained, positive
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import PAGE_RANGES_FILTER_KEY


def first_page_for_index(extraction: StructuredExtraction) -> dict[str, Any]:
    """既存解析の物理ページが分かるときだけ全文を保存し、追加解析はしない。"""
    layout = extraction.parser_artifacts.get("layout_records")
    raw_records = layout.get("records") if isinstance(layout, Mapping) else None
    if isinstance(raw_records, list):
        records = [value for value in raw_records if isinstance(value, Mapping)]
        raw_pages = layout.get("pages") if isinstance(layout, Mapping) else None
        pages = (
            [
                number
                for value in raw_pages
                if isinstance(value, Mapping)
                and (number := positive(value.get("page"))) is not None
            ]
            if isinstance(raw_pages, list)
            else []
        )
        return extract_first_page_context(
            records, engine="native", analyzed_pages=pages, paginated=bool(pages)
        )
    physical = [
        page.page_number
        for page in extraction.pages
        if page.width is not None and page.height is not None
    ]
    return extract_first_page_context(
        [
            {
                "id": element.element_id or str(index),
                "seq_no": element.order,
                "page": element.page_number,
                "text": element.text,
                "category": "Picture" if element.kind in {"image", "picture"} else "Text",
            }
            for index, element in enumerate(extraction.elements)
        ],
        engine="native",
        analyzed_pages=physical,
        paginated=bool(physical),
    )


def _fully_inside(
    document_id: str, metadata: Mapping[str, Any], filters: Mapping[str, str], scope: str
) -> bool:
    if not contained(document_id, metadata, scope):
        return False
    start = positive(metadata.get("page_start")) or positive(metadata.get("page_number"))
    end = positive(metadata.get("page_end")) or start
    if (minimum := filters.get("page_number_min")) and (start is None or start < int(minimum)):
        return False
    maximum = filters.get("page_number_max")
    return not maximum or end is not None and end <= int(maximum)


def first_page_global_scope_reason(filters: Mapping[str, str]) -> str:
    if (minimum := filters.get("page_number_min")) and int(minimum) > 1:
        return "outside_page_scope"
    if (maximum := filters.get("page_number_max")) and int(maximum) < 1:
        return "outside_page_scope"
    if any(
        filters.get(key)
        for key in (
            "content_kind",
            "content_kinds",
            "section_title",
            "section_path",
            "source_acl",
            "sheet_name",
        )
    ):
        return "content_scope_not_verifiable"
    return ""


async def selected_document_backgrounds(
    oracle: Any, state: Any, filters: dict[str, str], spans: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """選んだ文書・版だけを読み、同じ範囲の取得済み本文は回答内で再利用する。"""
    requests: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for span in spans:
        scope = list(span.get("document_scope") or ())
        if len(scope) != 6 or not scope[-1]:
            continue
        document_id, chunk_set_id = str(scope[-1]), str(scope[4])
        if not any(
            chunk.document_id == document_id
            and str(chunk.metadata.get("chunk_set_id") or "") == chunk_set_id
            for chunk in state.chunks.values()
        ):
            continue
        page_scope = str(span.get("page_scope") or filters.get(PAGE_RANGES_FILTER_KEY) or "")
        requests.setdefault((document_id, chunk_set_id, page_scope), span)
    results = []
    for (document_id, chunk_set_id, page_scope), span in requests.items():
        scoped = {**filters, **({PAGE_RANGES_FILTER_KEY: page_scope} if page_scope else {})}
        cache_key = (document_id, chunk_set_id, tuple(sorted(scoped.items())))
        cached = state.document_backgrounds.get(cache_key)
        if cached is not None:
            results.append({**cached, "reused": True})
            continue
        result: dict[str, Any] = {
            "document_scope": list(span["document_scope"]),
            "page_scope": page_scope,
            "first_chunk_status": "not_available_in_scope",
            "first_page_status": "not_available",
            "first_chunk": None,
            "first_page": None,
            "reused": False,
        }
        first = await oracle.retrieval_document_first_chunk(
            scoped, document_id=document_id, chunk_set_id=chunk_set_id
        )
        if first is not None:
            if (
                first.document_id != document_id
                or str(first.metadata.get("chunk_set_id") or "") != chunk_set_id
            ):
                result["first_chunk_status"] = "version_mismatch"
            elif _fully_inside(document_id, first.metadata, scoped, page_scope):
                state.chunks[first.chunk_id] = first
                result.update(first_chunk=first, first_chunk_status="available")
        if not _fully_inside(document_id, {"page_start": 1, "page_end": 1}, scoped, page_scope):
            result["first_page_status"] = "outside_page_scope"
        elif reason := first_page_global_scope_reason(scoped):
            result["first_page_status"] = reason
        elif chunk_set_id:
            contexts = await oracle.chunk_set_first_page_contexts([chunk_set_id], filters=scoped)
            page = contexts.get(chunk_set_id)
            if page:
                result["first_page"] = dict(page)
                if page.get("reconstructed_from"):
                    result["first_page_reconstructed_from"] = page["reconstructed_from"]
                result["first_page_status"] = (
                    "legacy_incomplete"
                    if page.get("truncated") is True
                    else "completeness_not_recorded"
                    if page.get("status") == "available" and page.get("truncated") is not False
                    else str(page.get("status") or "not_available")
                )
        state.document_backgrounds[cache_key] = result
        results.append(result)
    return results
