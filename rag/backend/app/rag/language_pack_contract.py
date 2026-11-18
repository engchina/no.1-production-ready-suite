"""言語資源の保存契約の失敗と、保存済みの内容の読み取り。保存先やAPI層へ依存しない。"""

from collections.abc import Mapping
from typing import Any

from rag_parser_core.language import LanguagePack, LanguagePackError, build_pack


class LanguagePackNotInitializedError(LookupError):
    """状態行も公開履歴もない、新しい環境だけを表す。"""


class LanguagePackSnapshotError(ValueError):
    """保存済みの言語資源が現在の契約に合わない(値が検証に通らない・保存の形が違う)。"""


def stored_pack(effective: Mapping[str, Any]) -> LanguagePack:
    """保存済みの公開版・下書きを読む(#5476)。

    欠けた項目(項目を足す前に保存したもの)は組み込みの値で補い、hash は保存していた項目だけから
    計算する。保存していた値が検証に通らないとき・保存の形が正規の形でないときだけ例外にする。
    """
    try:
        pack = build_pack(effective, fill_missing=True)
    except LanguagePackError as exc:
        raise LanguagePackSnapshotError(_error(set(exc.errors))) from exc
    except (TypeError, AttributeError) as exc:
        # 節の値が dict でない。
        raise LanguagePackSnapshotError(_error(_malformed_sections(effective))) from exc
    # 検証は通るが、保存の形が正規の形とそろわない項目(前後の空白など)も受け入れない。
    normalized = pack.to_dict()
    differing = _malformed_sections(effective) | {
        f"{section}.{key}"
        for section, fields in effective.items()
        if section != "locale" and isinstance(fields, Mapping)
        for key, value in fields.items()
        if normalized.get(section, {}).get(key) != value
    }
    if effective.get("locale") != pack.locale:
        differing.add("locale")
    if differing:
        raise LanguagePackSnapshotError(_error(differing))
    return pack


def _malformed_sections(effective: Mapping[str, Any]) -> set[str]:
    return {
        section
        for section, fields in effective.items()
        if section != "locale" and not isinstance(fields, Mapping)
    }


def _error(names: set[str]) -> str:
    # 項目の名前だけを出し、保存した値は出さない。
    return (
        f"保存済み言語資源の項目が現在の契約に合いません（{', '.join(sorted(names)) or '形式'}）。"
    )
