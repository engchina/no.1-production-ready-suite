"""検索・回答プロファイルの言語資源の上書きを、全体の言語資源に重ねる。

プロファイルだけで設定する項目(根拠がないときの固定返信。#5463)は、全体の値を使わず組み込みの値に重ねる。
"""

from collections.abc import Iterator
from contextlib import contextmanager

from rag_parser_core.language import LanguagePack, bind_pack, builtin_pack, default_pack, resolve

from app.config import Settings
from app.rag.language_pack_releases import active_release, pack_from_release
from app.rag.search_answer_profile_config import PROFILE_ONLY_LANGUAGE_PACK_FIELDS


def resolved_profile_language(settings: Settings) -> LanguagePack:
    """全体の既定へ、この要求で指定されたプロファイルの上書きだけを重ねる。"""
    release = active_release()
    base = pack_from_release(release) if release is not None else default_pack()
    builtin = builtin_pack(base.locale)
    profile_only: dict[str, dict[str, object]] = {}
    for section, key in PROFILE_ONLY_LANGUAGE_PACK_FIELDS:
        profile_only.setdefault(section, {})[key] = builtin.value(section, key)
    return resolve(base, profile_only, settings.rag_language_pack_overrides)


@contextmanager
def profile_language_scope(settings: Settings) -> Iterator[LanguagePack]:
    """await と thread へ伝播し、終了・失敗時に必ず元の要求へ戻す。"""
    pack = resolved_profile_language(settings)
    with bind_pack(pack):
        yield pack
