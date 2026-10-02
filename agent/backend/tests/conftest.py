"""テスト全体の共通設定（#750）。

共通認証の service は local でも Oracle（`PLATFORM_*`）を使うため、テストは既定で InMemory の
store に差し替える（開発機の共通 `.env` の DB に触れない）。store の選択を確かめるテストは
`set_security_service(None)` で既定に戻してから確かめる。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pr_system_settings.database_status import clear_database_status_cache

from app.security.service import SecurityService, set_security_service
from app.security.store import InMemorySecurityStore
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _in_memory_security_service() -> Iterator[None]:
    store = InMemorySecurityStore()
    store._ensure_system_admin_role()
    set_security_service(SecurityService(store, get_settings()))
    yield
    set_security_service(None)


@pytest.fixture(autouse=True)
def _fresh_database_status_cache() -> Iterator[None]:
    """DB の状態 API の `ok` の cache（#793）をテストごとに捨てる（テストの順序に依らない）。"""
    clear_database_status_cache()
    yield
    clear_database_status_cache()
