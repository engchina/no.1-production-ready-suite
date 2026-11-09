"""検索・回答プロファイルの言語資源の上書きを、全体の言語資源に重ねる。"""

from collections.abc import Iterator
from contextlib import contextmanager

from rag_parser_core.language import LanguagePack, bind_pack, default_pack, resolve

from app.config import Settings
from app.rag.language_pack_releases import active_release, pack_from_release


def resolved_profile_language(settings: Settings) -> LanguagePack:
    """全体の既定へ、この要求で指定されたプロファイルの上書きだけを重ねる。"""
    release = active_release()
    base = pack_from_release(release) if release is not None else default_pack()
    return resolve(base, settings.rag_language_pack_overrides)


@contextmanager
def profile_language_scope(settings: Settings) -> Iterator[LanguagePack]:
    """await と thread へ伝播し、終了・失敗時に必ず元の要求へ戻す。"""
    pack = resolved_profile_language(settings)
    with bind_pack(pack):
        yield pack
