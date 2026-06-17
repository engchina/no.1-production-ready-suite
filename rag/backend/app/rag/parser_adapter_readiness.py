"""Optional parser adapter runtime readiness.

Docling / Marker / Unstructured は任意依存として扱うため、flag と実際の
package installation 状態を分けて非機密に表示する。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass
from typing import Literal

from app.config import ParserAdapterBackend, Settings

ParserAdapterName = Literal["docling", "marker", "unstructured"]
ParserAdapterStatus = Literal["active", "available", "disabled", "ignored", "missing"]

ADAPTER_PACKAGES: dict[ParserAdapterName, str] = {
    "docling": "docling",
    "marker": "marker",
    "unstructured": "unstructured",
}
ADAPTER_ORDER: tuple[ParserAdapterName, ...] = ("docling", "marker", "unstructured")
ADAPTER_BACKENDS: tuple[ParserAdapterBackend, ...] = (
    "local",
    "auto",
    "docling",
    "marker",
    "unstructured",
)


@dataclass(frozen=True)
class ParserAdapterRuntimeStatus:
    """1 adapter の flag / package / selection 状態。"""

    backend: ParserAdapterName
    package_name: str
    enabled: bool
    selected: bool
    installed: bool
    status: ParserAdapterStatus
    version: str | None = None
    warning_code: str | None = None


@dataclass(frozen=True)
class ParserAdapterRuntimeSettings:
    """parser adapter feature flags の非機密 runtime snapshot。"""

    adapter_backend: ParserAdapterBackend
    effective_order: tuple[ParserAdapterName, ...]
    adapters: tuple[ParserAdapterRuntimeStatus, ...]


def parser_adapter_runtime_settings(settings: Settings) -> ParserAdapterRuntimeSettings:
    """Settings から adapter readiness snapshot を作る。"""
    adapter_backend = _normalize_adapter_backend(settings.rag_parser_adapter_backend)
    effective_order = _effective_adapter_order(settings, adapter_backend)
    adapters = tuple(
        _adapter_status(settings, backend=backend, adapter_backend=adapter_backend)
        for backend in ADAPTER_ORDER
    )
    return ParserAdapterRuntimeSettings(
        adapter_backend=adapter_backend,
        effective_order=effective_order,
        adapters=adapters,
    )


def _effective_adapter_order(
    settings: Settings,
    adapter_backend: ParserAdapterBackend,
) -> tuple[ParserAdapterName, ...]:
    if adapter_backend in ADAPTER_PACKAGES:
        return (adapter_backend,) if _adapter_flag(settings, adapter_backend) else ()
    if adapter_backend != "auto":
        return ()
    return tuple(backend for backend in ADAPTER_ORDER if _adapter_flag(settings, backend))


def _adapter_status(
    settings: Settings,
    *,
    backend: ParserAdapterName,
    adapter_backend: ParserAdapterBackend,
) -> ParserAdapterRuntimeStatus:
    package_name = ADAPTER_PACKAGES[backend]
    enabled = _adapter_flag(settings, backend)
    explicitly_selected = adapter_backend == backend
    selected = explicitly_selected or backend in _effective_adapter_order(settings, adapter_backend)
    installed, version = _package_info(package_name)
    status: ParserAdapterStatus
    warning_code: str | None = None

    if selected and not enabled:
        status = "disabled"
        warning_code = "adapter_feature_flag_disabled"
    elif selected and installed:
        status = "active"
    elif selected:
        status = "missing"
        warning_code = "adapter_package_missing"
    elif enabled:
        status = "ignored"
        warning_code = "adapter_flag_ignored_by_backend"
    elif installed:
        status = "available"
    else:
        status = "disabled"

    return ParserAdapterRuntimeStatus(
        backend=backend,
        package_name=package_name,
        enabled=enabled,
        selected=selected,
        installed=installed,
        status=status,
        version=version,
        warning_code=warning_code,
    )


def _adapter_flag(settings: Settings, backend: ParserAdapterName) -> bool:
    return bool(getattr(settings, f"rag_parser_{backend}_enabled", False))


def _normalize_adapter_backend(value: object) -> ParserAdapterBackend:
    normalized = str(value).casefold()
    if normalized in ADAPTER_BACKENDS:
        return normalized
    return "local"


def _package_info(package_name: str) -> tuple[bool, str | None]:
    if importlib.util.find_spec(package_name) is None:
        return False, None
    try:
        version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return True, version
