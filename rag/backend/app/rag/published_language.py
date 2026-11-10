"""未初期化の新しい環境だけ、組み込みの完全な言語資源で初期化する。"""

from rag_parser_core.language import builtin_pack

from app.clients.language_pack_release_store import LanguagePackReleaseStore
from app.clients.oracle import OracleClient
from app.rag.language_pack_contract import LanguagePackNotInitializedError


async def initialized_release_store(oracle: OracleClient | None = None) -> LanguagePackReleaseStore:
    store = LanguagePackReleaseStore(oracle or OracleClient())
    try:
        await store.current()
    except LanguagePackNotInitializedError:
        await store.ensure_initialized(builtin_pack().to_dict())
    return store
