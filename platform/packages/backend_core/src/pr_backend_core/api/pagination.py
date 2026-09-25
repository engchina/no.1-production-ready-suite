"""ページングユーティリティ（サービス横断で共通）。"""

from ..schemas import Page


def paginate[T](items: list[T], *, total: int, limit: int, offset: int) -> Page[T]:
    """items（当該ページ分）と総件数から Page を組み立てる。

    `has_next` は offset + 当該ページ件数が total 未満かで判定する。
    """
    return Page[T](
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_next=(offset + len(items)) < total,
    )
