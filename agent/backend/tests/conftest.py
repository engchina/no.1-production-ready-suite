"""テスト全体の共通設定（#750）。

共通認証の service は local でも Oracle（`PLATFORM_*`）を使うため、テストは既定で InMemory の
store に差し替える（開発機の共通 `.env` の DB に触れない）。store の選択を確かめるテストは
`set_security_service(None)` で既定に戻してから確かめる。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

# 保存先の既定は `auto`（#839）。開発機の共通 `.env` に DB の設定があると、`app` の import 時に
# Run の repository が Oracle に接続するため、テストは memory に固定する（`auto` を確かめるテストは
# settings を差し替えて `storage_backend.reset()` する）。`app` を import する前に設定する。
os.environ["AGENT_RUNTIME_REPOSITORY_BACKEND"] = "memory"

import pytest  # noqa: E402
from pr_system_settings.database_status import clear_database_status_cache  # noqa: E402

from app.features.agent import run_facts_store, storage_backend, tools  # noqa: E402
from app.security.service import SecurityService, set_security_service  # noqa: E402
from app.security.store import InMemorySecurityStore  # noqa: E402
from app.settings import get_settings  # noqa: E402


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


@pytest.fixture(autouse=True)
def _no_run_facts_store() -> Iterator[None]:
    """Run の事実（#794）は既定で保存しない（構成を変えるテストが Oracle を選ばないように）。

    保存先を確かめるテストは `run_facts_store.configure` / `reset` で差し替える。
    """
    run_facts_store.configure(None)
    yield
    run_facts_store.configure(None)


@pytest.fixture(autouse=True)
def _fresh_storage_backend_decision() -> Iterator[None]:
    """保存先の `auto` の判定（#839）をテストごとにやり直す。"""
    storage_backend.reset()
    yield
    storage_backend.reset()


@pytest.fixture(autouse=True)
def _no_mcp_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP の再試行の待ち（#854）はテストでは待たない。"""
    monkeypatch.setattr(tools, "_retry_sleep", lambda _seconds: None)
