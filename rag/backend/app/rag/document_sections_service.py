"""文書の章節を、保存済みの抽出結果・章節の抽出規則・人の修正から解決する(#713 / #715 / #721)。

文書の詳細の章節ナビゲーションの API と、チャットの確認の章節のページの解決(#721)と、章節と chunk の
対応の作り直し(#1438。``app.rag.section_map_service``)が使う。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.clients.oracle import OracleClient
from app.rag.document_sections import (
    add_new_extraction,
    refresh_from_extraction,
    stored_sections_payload,
)
from app.rag.section_revisions import (
    SectionExtractionBase,
    base_change_flags,
    extraction_base_of,
)
from app.rag.section_rules import (
    NAVIGATION_RULES_FINGERPRINT_ARTIFACT,
    extraction_sections,
    load_section_rules,
    section_rules_fingerprint,
)
from app.schemas.document import (
    DocumentProcessingConfig,
    DocumentSectionsData,
    SectionRulesMode,
)
from app.schemas.extraction import StructuredExtraction


@dataclass(frozen=True)
class SectionsSource:
    """章節の元にした抽出結果(無ければ None)・章節の抽出規則の方式・解析の結果の ID。"""

    extraction: StructuredExtraction | None
    rules_mode: SectionRulesMode
    extraction_recipe_id: str | None


async def sections_source(
    oracle: OracleClient, document_id: str, recipe_id: str | None
) -> SectionsSource:
    """章節の元にする抽出結果(``recipe_id``、無ければ既定のレシピの今の抽出)と方式(#715)。

    ``recipe_id`` の無い呼び出し（検索・回答・章節の対応）は、検索に出している版の抽出を読む。
    再取込の途中でレシピの今の抽出が新しい版へ動いても、旧版の chunk と揃える（#4115）。
    """
    serving = recipe_id is None
    if recipe_id is None:
        row = next(
            (
                row
                for row in await oracle.list_document_recipes(document_id)
                if row.get("slot_no") == 1
            ),
            None,
        )
        recipe_id = str(row["recipe_id"]) if row is not None else None
    else:
        row = await oracle.get_document_recipe(document_id, recipe_id)
    config = DocumentProcessingConfig.model_validate(
        (row.get("processing_config") if row is not None else None) or {}
    )
    mode = config.section_rules_mode or load_section_rules().mode
    extraction_recipe_id = row.get("active_extraction_recipe_id") if row is not None else None
    latest_chunk_set = getattr(oracle, "get_latest_recipe_chunk_set", None)
    if serving and recipe_id is not None and callable(latest_chunk_set):
        served = await latest_chunk_set(recipe_id, status="INDEXED", active=True)
        if served is not None and served.get("extraction_recipe_id"):
            extraction_recipe_id = served["extraction_recipe_id"]
    if not extraction_recipe_id:
        return SectionsSource(None, mode, None)
    artifact = await oracle.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=str(extraction_recipe_id)
    )
    if artifact is None or not artifact.get("extraction_json"):
        return SectionsSource(None, mode, str(extraction_recipe_id))
    return SectionsSource(
        StructuredExtraction.model_validate(artifact["extraction_json"]),
        mode,
        str(extraction_recipe_id),
    )


def section_extraction_base(source: SectionsSource) -> SectionExtractionBase:
    """今の抽出を、章節の修正の基として記録する形にする(#4171)。"""
    return extraction_base_of(
        source.extraction_recipe_id,
        extraction_sections(source.extraction, source.rules_mode, load_section_rules()),
        has_extraction=source.extraction is not None,
    )


def _navigation_summaries(extraction: StructuredExtraction | None) -> dict[str, str]:
    """抽出結果の全章節の要約(抽出の章節 id → 要約)。要約の無い章節は含めない(#4094)。"""
    if extraction is None:
        return {}
    return {node.section_id: node.summary for node in extraction.navigation if node.summary}


async def document_sections(
    oracle: OracleClient, document_id: str, recipe_id: str | None
) -> tuple[DocumentSectionsData, set[str]] | None:
    """人の修正があればそれを、無ければ抽出結果の章節を返す。文書が無ければ None。

    人の修正があるときは、人が変えていない抽出の章節のページを今の抽出に合わせ、修正の後に新しく
    抽出された章節を足す(人が消した章節は足さない。#721)。2 つ目の値は人が消した章節の id。
    """
    resolved = await document_sections_with_source(oracle, document_id, recipe_id)
    if resolved is None:
        return None
    data, removed, _source = resolved
    return data, removed


async def document_sections_with_source(
    oracle: OracleClient, document_id: str, recipe_id: str | None
) -> tuple[DocumentSectionsData, set[str], SectionsSource] | None:
    """``document_sections`` に、章節の元にした抽出結果を添えて返す(章節と chunk の対応。#1438)。"""
    if not await oracle.document_exists(document_id):
        return None
    source = await sections_source(oracle, document_id, recipe_id)
    extraction, rules_mode = source.extraction, source.rules_mode
    store = load_section_rules()
    extracted = extraction_sections(extraction, rules_mode, store)
    saved_rules = (
        extraction.parser_artifacts.get(NAVIGATION_RULES_FINGERPRINT_ARTIFACT)
        if extraction
        else None
    )
    summary_rules_match = (
        saved_rules == section_rules_fingerprint(rules_mode, store) if saved_rules else None
    )
    summaries = _navigation_summaries(extraction)
    page_count = (len(extraction.pages) or None) if extraction else None
    stored = await oracle.get_document_sections(document_id)
    if stored is None:
        return (
            DocumentSectionsData(
                document_id=document_id,
                source="extraction",
                rules_mode=rules_mode,
                summary_rules_match=summary_rules_match,
                summaries=summaries,
                sections=extracted,
                extraction_section_count=len(extracted),
                page_count=page_count,
            ),
            set(),
            source,
        )
    sections, removed = stored_sections_payload(stored["sections"])
    base_changed, base_outline_changed = base_change_flags(
        recorded_extraction_recipe_id=stored.get("base_extraction_recipe_id"),
        recorded_outline_sha256=stored.get("base_outline_sha256"),
        current=extraction_base_of(
            source.extraction_recipe_id, extracted, has_extraction=extraction is not None
        ),
    )
    return (
        DocumentSectionsData(
            document_id=document_id,
            source="manual",
            rules_mode=rules_mode,
            summary_rules_match=summary_rules_match,
            summaries=summaries,
            sections=add_new_extraction(
                refresh_from_extraction(sections, extracted), extracted, removed
            ),
            extraction_section_count=len(extracted),
            page_count=page_count,
            revision=cast(int, stored["revision"]),
            updated_at=cast(datetime | None, stored.get("updated_at")),
            base_changed=base_changed,
            base_outline_changed=base_outline_changed,
        ),
        removed,
        source,
    )


async def current_section_pages(
    oracle: OracleClient, document_ids: set[str]
) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """文書の今の章節のページ。確認を保存した後に章節のページが変わっても追う(#721)。"""
    pages: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for document_id in sorted(document_ids):
        resolved = await document_sections(oracle, document_id, None)
        if resolved is None:
            continue
        for section in resolved[0].sections:
            pages[(document_id, section.id)] = (section.page_start, section.page_end)
    return pages
