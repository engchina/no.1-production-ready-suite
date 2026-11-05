"""未初期化の新しい環境だけ、組み込みの完全な言語資源で初期化する。"""

from rag_parser_core.language import builtin_pack

from app.clients.language_pack_release_store import LanguagePackReleaseStore
from app.clients.oracle import OracleClient
from app.config import get_settings, resolve_language_pack_file
from app.rag.language_pack_contract import (
    LanguagePackNotInitializedError,
    LanguagePackSnapshotError,
)
from app.rag.language_pack_store import history_path


async def initialized_release_store(oracle: OracleClient | None = None) -> LanguagePackReleaseStore:
    store = LanguagePackReleaseStore(oracle or OracleClient())
    try:
        await store.current()
    except LanguagePackNotInitializedError:
        path = resolve_language_pack_file(get_settings())
        for existing in (path, history_path(path)):
            try:
                existing.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise LanguagePackSnapshotError("旧言語資源の所在を確認できません。") from exc
            raise LanguagePackSnapshotError(
                "旧言語資源が残っています。データの準備が必要です。"
            ) from None
        await store.ensure_initialized(builtin_pack().to_dict())
    return store
