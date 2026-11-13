"""取得済みの行列数を上限に、表投影の位置指定を解釈する。"""

from __future__ import annotations

import re

_ITEM = re.compile(r"[1-9][0-9]*(?:-[1-9][0-9]*)?\Z")


class TableProjectionError(ValueError):
    pass


def selected_positions(spec: str, count: int) -> list[int]:
    """1始まりの指定を、表示順を保った0始まりの位置へ変換する。"""
    spec = spec.strip()
    if spec == "*":
        return list(range(count))
    if not spec or spec.count(",") + 1 > count:
        raise TableProjectionError("表の行・列は取得結果の範囲内で重複なく指定してください。")
    selected: list[int] = []
    seen: set[int] = set()
    limit = str(count)

    def bounded_number(text: str) -> int:
        # 巨大な整数への変換やrangeの展開より先に、元結果の上限と比較する。
        if len(text) > len(limit) or len(text) == len(limit) and text > limit:
            raise TableProjectionError("表の行・列が取得結果の範囲を超えています。")
        return int(text)

    for item in spec.split(","):
        bounds = [bound.strip() for bound in item.strip().split("-")]
        if _ITEM.fullmatch("-".join(bounds)) is None:
            raise TableProjectionError(
                "行・列は*、正の番号、昇順の範囲、カンマ区切りで指定してください。"
            )
        start = bounded_number(bounds[0])
        end = bounded_number(bounds[-1])
        if start > end or end - start + 1 > count - len(selected):
            raise TableProjectionError(
                "表の行・列は取得結果の範囲内で昇順の範囲を指定してください。"
            )
        for position in range(start - 1, end):
            if position in seen:
                raise TableProjectionError("同じ元行・元列を表に重ねて指定しないでください。")
            seen.add(position)
            selected.append(position)
    return selected
