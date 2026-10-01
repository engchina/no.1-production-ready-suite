"""文書の章節を、保存済みの抽出結果・章節の抽出規則・人の修正から解決する(#713 / #715 / #721)。

文書の詳細の章節ナビゲーションの API と、チャットの確認の章節のページの解決(#721)が使う。
"""

from datetime import datetime
from typing import cast

from app.clients.oracle import OracleClient
from app.rag.document_sections import (
    add_new_extraction,
    refresh_from_extraction,
    stored_sections_payload,
)
from app.rag.section_rules import extraction_sections, load_section_rules
from app.schemas.document import (
    DocumentProcessingConfig,
    DocumentSectionsData,
    SectionRulesMode,
)
from app.schemas.extraction import StructuredExtraction


async def sections_extraction(
    oracle: OracleClient, document_id: str, recipe_id: str | None
) -> tuple[StructuredExtraction | None, SectionRulesMode]:
    """章節の元にする抽出結果(``recipe_id``、無ければ既定のレシピの今の抽出。無ければ None)と、
    そのレシピの章節の抽出規則の方式(#715)。"""
    if recipe_id is None:
        recipe_id = str((await oracle.ensure_default_document_recipe(document_id))["recipe_id"])
    row = await oracle.get_document_recipe(document_id, recipe_id)
    config = DocumentProcessingConfig.model_validate(
        (row.get("processing_config") if row is not None else None) or {}
    )
    mode = config.section_rules_mode or load_section_rules().mode
    extraction_recipe_id = row.get("active_extraction_recipe_id") if row is not None else None
    if not extraction_recipe_id:
        return None, mode
    artifact = await oracle.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=str(extraction_recipe_id)
    )
    if artifact is None or not artifact.get("extraction_json"):
        return None, mode
    return StructuredExtraction.model_validate(artifact["extraction_json"]), mode


async def document_sections(
    oracle: OracleClient, document_id: str, recipe_id: str | None
) -> tuple[DocumentSectionsData, set[str]] | None:
    """人の修正があればそれを、無ければ抽出結果の章節を返す。文書が無ければ None。

    人の修正があるときは、人が変えていない抽出の章節のページを今の抽出に合わせ、修正の後に新しく
    抽出された章節を足す(人が消した章節は足さない。#721)。2 つ目の値は人が消した章節の id。
    """
    if not await oracle.document_exists(document_id):
        return None
    extraction, rules_mode = await sections_extraction(oracle, document_id, recipe_id)
    extracted = extraction_sections(extraction, rules_mode)
    page_count = (len(extraction.pages) or None) if extraction else None
    stored = await oracle.get_document_sections(document_id)
    if stored is None:
        return (
            DocumentSectionsData(
                document_id=document_id,
                source="extraction",
                rules_mode=rules_mode,
                sections=extracted,
                extraction_section_count=len(extracted),
                page_count=page_count,
            ),
            set(),
        )
    sections, removed = stored_sections_payload(stored["sections"])
    return (
        DocumentSectionsData(
            document_id=document_id,
            source="manual",
            rules_mode=rules_mode,
            sections=add_new_extraction(
                refresh_from_extraction(sections, extracted), extracted, removed
            ),
            extraction_section_count=len(extracted),
            page_count=page_count,
            revision=cast(int, stored["revision"]),
            updated_at=cast(datetime | None, stored.get("updated_at")),
        ),
        removed,
    )
