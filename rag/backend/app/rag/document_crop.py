"""解析に使ったファイルから bbox の領域を切り出す(解析結果プレビューと 回答画像で共用)。

文書プレビューのページ画像(bbox の強調を重ねるための PDF のページ画像)もここで作る(#349)。
MCP の ``rag_read_source`` の図の画像(大きさの上限つき。#1282)もここで切り出す。
PDF / 画像を開く・描くのは ``app.rag.pdf_backend``(PDFium と Pillow。#3343)。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from rag_parser_core.preprocess import SourceDerivation

from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient
from app.rag.ingestion import preprocess_artifact_from_derivation
from app.rag.pdf_backend import PageSource, SourceOpenError, intersect, open_source, png_bytes
from app.schemas.document import DocumentPreprocessArtifact

MAX_CROP_PIXELS = 4000 * 4000
# プレビューのページ画像の上限(大判の図面でも 1 枚 16M px に収める)。
MAX_PAGE_PIXELS = 4000 * 4000
MAX_PREVIEW_PAGES = 10000


class DocumentSourceNotFoundError(LookupError):
    """文書、またはその原本ファイルが見つからない。"""


class VersionSourceUnknownError(DocumentSourceNotFoundError):
    """版（抽出）が解析したファイルの記録が無い。別のファイルで代用しない。"""

    def __init__(self) -> None:
        super().__init__("この版が解析したファイルの記録がありません。")


def _artifact_from_derivation(detail: Any, raw: object) -> DocumentPreprocessArtifact:
    if not isinstance(raw, Mapping) or not raw:
        raise VersionSourceUnknownError
    try:
        derivation = SourceDerivation.model_validate(raw)
    except ValidationError as exc:
        raise VersionSourceUnknownError from exc
    return preprocess_artifact_from_derivation(
        derivation,
        original_file_name=detail.file_name,
        original_object_storage_path=detail.object_storage_path,
        fallback_content_type=detail.content_type or "application/octet-stream",
    )


def prepared_artifact_from_extraction(
    detail: Any, extraction: Mapping[str, Any]
) -> DocumentPreprocessArtifact:
    """抽出が解析したファイル。系譜が無ければ ``VersionSourceUnknownError``。

    bbox と要素の位置はこのファイルの座標で、レシピの行の artifact は再処理の開始で新しいファイルを
    指す。版の根拠はレシピの行ではなくここから読む（#4166）。
    """
    artifacts = extraction.get("parser_artifacts")
    raw = artifacts.get("source_derivation") if isinstance(artifacts, Mapping) else None
    return _artifact_from_derivation(detail, raw)


async def chunk_set_prepared_artifact(
    oracle: OracleClient, detail: Any, document_id: str, chunk_set_id: str
) -> DocumentPreprocessArtifact:
    """chunk_set の抽出が解析したファイル。抽出か系譜が無ければ ``VersionSourceUnknownError``。"""
    raw = await oracle.chunk_set_source_derivation(
        document_id=document_id, chunk_set_id=chunk_set_id
    )
    return _artifact_from_derivation(detail, raw)


async def load_parsed_source(
    oracle: OracleClient,
    document_id: str,
    recipe_id: str | None = None,
    *,
    chunk_set_id: str | None = None,
) -> bytes:
    """解析に使ったファイル(ファイル準備後の artifact があればそれ、無ければ原本)を返す。

    ``chunk_set_id`` を渡すと、その版が解析したファイルを読む（#4166）。記録が無い版は
    ``VersionSourceUnknownError`` で、ほかのファイルで代用しない。``chunk_set_id`` が無いときは
    ``recipe_id`` の処理レシピの artifact を使う(レシピごとに解析したファイルが違うため、bbox は
    そのファイルの座標。#1282)。レシピが無ければ文書の artifact を使う。参照パスが不正な場合は
    ValueError(ObjectStorageClient の契約)。
    """
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise DocumentSourceNotFoundError("ドキュメントが見つかりません。")
    artifact = detail.preprocess_artifact
    if chunk_set_id:
        artifact = await chunk_set_prepared_artifact(oracle, detail, document_id, chunk_set_id)
    elif recipe_id:
        recipe = await oracle.get_document_recipe(document_id, recipe_id)
        if recipe is not None:
            raw = recipe.get("preprocess_artifact")
            artifact = DocumentPreprocessArtifact.model_validate(raw) if raw else None
    path = (
        artifact.object_storage_path
        if artifact is not None and artifact.object_storage_path
        else detail.object_storage_path
    )
    try:
        return await ObjectStorageClient().get(path)
    except FileNotFoundError as exc:
        raise DocumentSourceNotFoundError("原本ファイルが見つかりません。") from exc


def _open(data: bytes, message: str) -> PageSource:
    try:
        return open_source(data)
    except SourceOpenError as exc:
        raise ValueError(message) from exc


def _valid_bbox(
    bbox: tuple[float, float, float, float], page_size: tuple[float, float]
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    width, height = page_size
    if (
        width <= 0
        or height <= 0
        or x1 <= x0
        or y1 <= y0
        or x0 < 0
        or y0 < 0
        or x1 > width * 1.001
        or y1 > height * 1.001
    ):
        raise ValueError("切り出し範囲が不正です。")
    return bbox


def _page_clip(
    source: PageSource,
    page_number: int,
    bbox: tuple[float, float, float, float],
    page_size: tuple[float, float],
) -> tuple[float, float, float, float] | None:
    """bbox(解析時のページ画像 px)を、そのページの pt の範囲(ページの中に収めた)にする。"""
    if page_number < 1 or page_number > source.page_count:
        raise ValueError("ページ番号がファイルのページ数を超えています。")
    page_width, page_height = source.page_size(page_number - 1)
    x0, y0, x1, y1 = bbox
    scale_x = page_width / page_size[0]
    scale_y = page_height / page_size[1]
    return intersect(
        (x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y), (0, 0, page_width, page_height)
    )


def crop_png(
    data: bytes,
    page_number: int,
    bbox: tuple[float, float, float, float],
    page_size: tuple[float, float],
    dpi: int = 150,
) -> bytes:
    """PDF / 画像の 1 ページから bbox(ページ画像 px 座標)を切り出して PNG にする。"""
    _valid_bbox(bbox, page_size)
    with _open(data, "このファイルは切り出しに対応していません。") as source:
        clip = _page_clip(source, page_number, bbox, page_size)
        zoom = dpi / 72
        if (
            clip is None
            or (clip[2] - clip[0]) * (clip[3] - clip[1]) * zoom * zoom > MAX_CROP_PIXELS
        ):
            raise ValueError("切り出し範囲が不正です。")
        image = source.render(page_number - 1, zoom, clip)
    return png_bytes(image)


class CropTooLargeError(ValueError):
    """上限まで縮めても、切り出した画像が上限のバイト数を超える。"""


@dataclass(frozen=True, slots=True)
class BoundedCrop:
    png: bytes
    width: int
    height: int


def crop_png_bounded(
    data: bytes,
    page_number: int,
    bbox: tuple[float, float, float, float],
    page_size: tuple[float, float],
    *,
    max_edge: int,
    max_bytes: int,
    max_dpi: int = 200,
) -> BoundedCrop:
    """bbox の領域を、長い辺が ``max_edge`` px 以下・``max_bytes`` 以下の PNG にする(#1282)。

    小さな図は ``max_dpi`` まで拡大し、大きな図は長い辺に合わせて縮める。PNG が上限を超えたら
    倍率を下げて 2 回まで作り直し、それでも超えれば ``CropTooLargeError``。範囲が不正・
    対応しない形式は ValueError(``crop_png`` と同じ)。
    """
    _valid_bbox(bbox, page_size)
    with _open(data, "このファイルは切り出しに対応していません。") as source:
        clip = _page_clip(source, page_number, bbox, page_size)
        if clip is None:
            raise ValueError("切り出し範囲が不正です。")
        zoom = min(max_dpi / 72, max_edge / max(clip[2] - clip[0], clip[3] - clip[1]))
        for _ in range(3):
            image = source.render(page_number - 1, zoom, clip)
            png = png_bytes(image)
            if len(png) <= max_bytes:
                return BoundedCrop(png=png, width=image.width, height=image.height)
            # PNG のバイト数はおおよそ画素数に比例する。少し余裕を見て倍率を下げる。
            zoom *= (max_bytes / len(png)) ** 0.5 * 0.9
        raise CropTooLargeError("図の画像が大きすぎます。")


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    """PDF / 画像の各ページの表示寸法(pt。ページの /Rotate を反映した向き)を返す。"""
    with _open(data, "このファイルはページ画像のプレビューに対応していません。") as source:
        if source.page_count < 1:
            raise ValueError("このファイルにはページがありません。")
        return [
            source.page_size(index) for index in range(min(source.page_count, MAX_PREVIEW_PAGES))
        ]


def render_page_png(data: bytes, page_number: int, dpi: int = 144) -> bytes:
    """PDF / 画像の 1 ページ全体を PNG にする。

    ページの /Rotate を反映した向きで描く。解析の bbox(ページ画像 px 座標)も同じ向きなので、
    画面ではページに対する割合で重ねれば解像度に関係なく位置が合う。
    """
    with _open(data, "このファイルはページ画像のプレビューに対応していません。") as source:
        if page_number < 1 or page_number > source.page_count:
            raise ValueError("ページ番号がファイルのページ数を超えています。")
        width, height = source.page_size(page_number - 1)
        area = width * height
        if area <= 0:
            raise ValueError("ページの寸法が不正です。")
        # 大判のページは上限の画素数に収まるまで倍率を下げる。
        zoom = min(dpi / 72, (MAX_PAGE_PIXELS / area) ** 0.5)
        image = source.render(page_number - 1, zoom)
    return png_bytes(image)
