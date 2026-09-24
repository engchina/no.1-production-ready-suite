"""解析に使ったファイルから bbox の領域を切り出す(解析結果プレビューと DocRAG 回答画像で共用)。"""

from __future__ import annotations

from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient

MAX_CROP_PIXELS = 4000 * 4000


class DocumentSourceNotFoundError(LookupError):
    """文書、またはその原本ファイルが見つからない。"""


async def load_parsed_source(oracle: OracleClient, document_id: str) -> bytes:
    """解析に使ったファイル(ファイル準備後の artifact があればそれ、無ければ原本)を返す。

    参照パスが不正な場合は ValueError(ObjectStorageClient の契約)。
    """
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise DocumentSourceNotFoundError("ドキュメントが見つかりません。")
    artifact = detail.preprocess_artifact
    path = (
        artifact.object_storage_path
        if artifact is not None and artifact.object_storage_path
        else detail.object_storage_path
    )
    try:
        return await ObjectStorageClient().get(path)
    except FileNotFoundError as exc:
        raise DocumentSourceNotFoundError("原本ファイルが見つかりません。") from exc


def crop_png(
    data: bytes,
    page_number: int,
    bbox: tuple[float, float, float, float],
    page_size: tuple[float, float],
    dpi: int = 150,
) -> bytes:
    """PDF / 画像の 1 ページから bbox(ページ画像 px 座標)を切り出して PNG にする(pymupdf)。"""
    import fitz  # type: ignore[import-untyped]

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
    try:
        document = fitz.open(stream=data)
    except Exception as exc:
        raise ValueError("このファイルは切り出しに対応していません。") from exc
    with document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError("ページ番号がファイルのページ数を超えています。")
        page = document[page_number - 1]
        scale_x = page.rect.width / width
        scale_y = page.rect.height / height
        clip = fitz.Rect(x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y) & page.rect
        zoom = dpi / 72
        if clip.is_empty or clip.width * clip.height * zoom * zoom > MAX_CROP_PIXELS:
            raise ValueError("切り出し範囲が不正です。")
        pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(zoom, zoom))
        return bytes(pixmap.tobytes("png"))
