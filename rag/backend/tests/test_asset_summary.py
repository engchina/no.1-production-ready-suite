"""図・表・chart の VLM/LLM 要約（Knowhere 由来）の単体テスト。"""

from __future__ import annotations

import pytest

from app.rag.asset_summary import summarize_assets
from app.schemas.extraction import (
    DocumentElement,
    ExtractionAsset,
    StructuredExtraction,
)


def _extraction(assets: list[ExtractionAsset]) -> StructuredExtraction:
    return StructuredExtraction(
        raw_text="本文",
        elements=[DocumentElement(kind="text", text="本文", order=0, element_id="e0")],
        assets=assets,
    )


@pytest.mark.anyio
async def test_summarize_assets_fills_summary_and_appends_searchable_element() -> None:
    extraction = _extraction(
        [ExtractionAsset(asset_id="fig-1", kind="figure", page_number=2, alt_text="売上推移")]
    )

    async def _summarize(asset: ExtractionAsset) -> str | None:
        return f"{asset.alt_text}の要約"

    result = await summarize_assets(extraction, _summarize)

    # asset.summary が埋まる。
    assert result.assets[0].summary == "売上推移の要約"
    # 検索可能な figure element が追加され、asset へ link する。
    summary_elements = [
        element for element in result.elements if element.metadata.get("asset_summary")
    ]
    assert len(summary_elements) == 1
    element = summary_elements[0]
    assert element.content_kind == "figure"
    assert element.text == "売上推移の要約"
    assert element.metadata.get("asset_id") == "fig-1"
    assert element.page_number == 2
    assert element.element_id == "asset-summary-fig-1"


@pytest.mark.anyio
async def test_summarize_assets_skips_non_summarizable_kinds() -> None:
    extraction = _extraction(
        [ExtractionAsset(asset_id="att-1", kind="attachment", alt_text="添付")]
    )

    async def _summarize(asset: ExtractionAsset) -> str | None:
        return "要約"

    result = await summarize_assets(extraction, _summarize)
    assert result.assets[0].summary is None
    assert not [e for e in result.elements if e.metadata.get("asset_summary")]


@pytest.mark.anyio
async def test_summarize_assets_skips_empty_summary() -> None:
    extraction = _extraction([ExtractionAsset(asset_id="fig-1", kind="figure", alt_text="x")])

    async def _summarize(asset: ExtractionAsset) -> str | None:
        return "   "

    result = await summarize_assets(extraction, _summarize)
    assert result.assets[0].summary is None
    assert result.elements == extraction.elements


@pytest.mark.anyio
async def test_summarize_assets_respects_max_assets() -> None:
    extraction = _extraction(
        [ExtractionAsset(asset_id=f"fig-{i}", kind="chart", alt_text=f"c{i}") for i in range(5)]
    )

    async def _summarize(asset: ExtractionAsset) -> str | None:
        return "要約"

    result = await summarize_assets(extraction, _summarize, max_assets=2)
    assert sum(1 for a in result.assets if a.summary) == 2
    assert len([e for e in result.elements if e.metadata.get("asset_summary")]) == 2


@pytest.mark.anyio
async def test_summarize_assets_best_effort_on_summarizer_error() -> None:
    extraction = _extraction([ExtractionAsset(asset_id="fig-1", kind="figure", alt_text="x")])

    async def _summarize(asset: ExtractionAsset) -> str | None:
        raise RuntimeError("vlm down")

    result = await summarize_assets(extraction, _summarize)
    assert result.assets[0].summary is None


@pytest.mark.anyio
async def test_summarized_asset_round_trips_through_document_payload() -> None:
    extraction = _extraction([ExtractionAsset(asset_id="fig-1", kind="figure", alt_text="x")])

    async def _summarize(asset: ExtractionAsset) -> str | None:
        return "図の要約"

    result = await summarize_assets(extraction, _summarize)
    restored = StructuredExtraction.model_validate(result.to_document_payload())
    assert restored.assets[0].summary == "図の要約"
    assert any(e.metadata.get("asset_summary") for e in restored.elements)
