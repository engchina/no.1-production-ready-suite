"""図・表・chart の VLM/LLM 要約（Knowhere の multimodal 要約 由来）。

Knowhere は「画像・表を VLM で要約/特徴抽出し source chunk へ link、推論時に multimodal
asset を検索/引用」する。本モジュールは確定スタック（OCI Enterprise AI VLM/LLM）に合わせて
再実装する:
- `summarize_assets`: `ExtractionAsset` の要約を注入された要約器で生成し、asset.summary へ
  保存しつつ、検索可能な合成 `DocumentElement(content_kind=figure)` を追加して既存の
  chunking 経路へ流す（chunking 側を変更しない）。要約器は注入可能でテストは決定論。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.schemas.extraction import DocumentElement, ExtractionAsset, StructuredExtraction

# 要約対象にする asset 種別（純テキスト添付などは対象外）。
SUMMARIZABLE_ASSET_KINDS = frozenset({"figure", "image", "chart", "diagram", "graph", "plot"})
DEFAULT_ASSET_SUMMARY_MAX_ASSETS = 24

AssetSummarizer = Callable[[ExtractionAsset], Awaitable[str | None]]


def _asset_summary_element(asset: ExtractionAsset, summary: str, order: int) -> DocumentElement:
    """asset 要約を検索可能な figure element として表現する（citation link 付き）。"""
    metadata: dict[str, object] = {
        "asset_id": asset.asset_id,
        "asset_kind": asset.kind,
        "asset_summary": True,
    }
    if asset.object_path:
        metadata["asset_object_path"] = asset.object_path
    return DocumentElement(
        kind="figure",
        text=summary,
        order=order,
        element_id=f"asset-summary-{asset.asset_id}",
        content_kind="figure",
        page_number=asset.page_number,
        bbox=list(asset.bbox) if asset.bbox else None,
        metadata=metadata,
    )


async def summarize_assets(
    extraction: StructuredExtraction,
    summarize: AssetSummarizer,
    *,
    max_assets: int = DEFAULT_ASSET_SUMMARY_MAX_ASSETS,
) -> StructuredExtraction:
    """asset を要約し、asset.summary と検索可能な合成 figure element を付与する。

    要約は上限 `max_assets` 件まで。要約器が None/失敗を返した asset は据え置く
    （best-effort）。既存 element の reading order の後ろへ合成 element を足す。
    """
    if not extraction.assets:
        return extraction

    next_order = max((element.order for element in extraction.elements), default=0) + 1
    updated_assets: list[ExtractionAsset] = []
    new_elements: list[DocumentElement] = []
    remaining = max_assets
    for asset in extraction.assets:
        if remaining <= 0 or asset.kind.casefold() not in SUMMARIZABLE_ASSET_KINDS:
            updated_assets.append(asset)
            continue
        try:
            summary = await summarize(asset)
        except Exception:
            updated_assets.append(asset)
            continue
        summary = (summary or "").strip()
        if not summary:
            updated_assets.append(asset)
            continue
        remaining -= 1
        updated_assets.append(asset.model_copy(update={"summary": summary}))
        new_elements.append(_asset_summary_element(asset, summary, next_order))
        next_order += 1

    if not new_elements:
        return extraction
    return extraction.model_copy(
        update={
            "assets": updated_assets,
            "elements": [*extraction.elements, *new_elements],
        }
    )
