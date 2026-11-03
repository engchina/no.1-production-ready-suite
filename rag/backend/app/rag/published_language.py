"""公開版を初めて使う入口で、組み込みの言語資源を公開版にする。"""

from rag_parser_core.language import builtin_pack

from app.clients.language_pack_release_store import LanguagePackReleaseStore
from app.clients.oracle import OracleClient


async def initialized_release_store(oracle: OracleClient | None = None) -> LanguagePackReleaseStore:
    store = LanguagePackReleaseStore(oracle or OracleClient())
    try:
        await store.current()
    except LookupError:
        await store.ensure_initialized(builtin_pack().to_dict())
    return store
