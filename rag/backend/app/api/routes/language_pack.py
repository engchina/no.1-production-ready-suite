"""言語資源の画面の API(#2181)。

- ``GET /api/settings/language-pack``: 項目の定義・組み込みの値・全体の上書き・重ねた値。
- ``PUT /api/settings/language-pack``: 全体の上書きを保存する(組み込みと違う項目だけ)。
- ``POST /api/settings/language-pack/preview``: 例の文を、保存した言語資源と編集中の上書きの
  それぞれで読む。
- ``GET /api/settings/language-pack/history``: 保存の版(新しい順)。
- ``POST /api/settings/language-pack/history/{version_id}/restore``: 前の版の上書きを保存し直す
  (戻したことも新しい版として残す)。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from rag_parser_core.language import (
    FIELD_SPECS,
    LanguagePack,
    LanguagePackError,
    bind_pack,
    builtin_pack,
    overrides_from,
    resolve,
)

from app.clients.oracle import OracleClient
from app.config import get_settings, resolve_language_pack_file
from app.rag.cross_references import document_name_aliases, extract_references, heading_keys
from app.rag.language_pack_store import LANGUAGE_PACK_STORE
from app.schemas.common import ApiResponse
from app.schemas.settings import (
    LanguagePackData,
    LanguagePackFieldData,
    LanguagePackHistoryData,
    LanguagePackHistoryVersion,
    LanguagePackPreviewData,
    LanguagePackPreviewReference,
    LanguagePackPreviewRequest,
    LanguagePackPreviewResult,
    LanguagePackUpdate,
)
from app.security.dependencies import current_principal

router = APIRouter()
logger = logging.getLogger(__name__)


def _sections(pack: LanguagePack) -> dict[str, dict[str, object]]:
    data = pack.to_dict()
    data.pop("locale", None)
    return data


def _resolve(overrides: Mapping[str, Mapping[str, Any]]) -> LanguagePack:
    try:
        return resolve(builtin_pack(), overrides)
    except LanguagePackError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc


def _saved() -> tuple[LanguagePack, dict[str, dict[str, Any]]]:
    path = resolve_language_pack_file(get_settings())
    overrides = LANGUAGE_PACK_STORE.overrides(path)
    try:
        return resolve(builtin_pack(), overrides), overrides
    except LanguagePackError:
        # 手で壊した保存は使わない(組み込みで動いている)。画面は組み込みを出し、保存で直せる。
        return builtin_pack(), {}


def _data(pack: LanguagePack) -> LanguagePackData:
    builtin = builtin_pack()
    return LanguagePackData(
        fields=[
            LanguagePackFieldData(
                section=spec.section,
                key=spec.key,
                kind=spec.kind.value,
                choices=list(spec.choices),
                ingest=spec.ingest,
                max_chars=spec.max_chars,
            )
            for spec in FIELD_SPECS
        ],
        builtin=_sections(builtin),
        overrides=overrides_from(pack, builtin),
        effective=_sections(pack),
        hash=pack.hash,
    )


async def _outdated_documents(pack: LanguagePack) -> int | None:
    """今の言語資源で分割していない文書の数(DB に届かなければ None。画面は数を出さない)。"""
    try:
        return await OracleClient().count_documents_outside_language_pack(pack.ingest_hash)
    except Exception:  # noqa: BLE001 - 数は案内。言語資源の画面は DB が無くても使える。
        logger.warning("language pack outdated document count failed", exc_info=True)
        return None


@router.get("/language-pack", response_model=ApiResponse[LanguagePackData])
async def get_language_pack() -> ApiResponse[LanguagePackData]:
    pack, _overrides = _saved()
    data = _data(pack)
    data.outdated_documents = await _outdated_documents(pack)
    return ApiResponse(data=data)


def _user(request: Request) -> str | None:
    try:
        return current_principal(request).display_name
    except Exception:  # noqa: BLE001 - 認証の無い開発の構成(local)では記録しない
        return None


@router.put("/language-pack", response_model=ApiResponse[LanguagePackData])
async def update_language_pack(
    payload: LanguagePackUpdate, request: Request
) -> ApiResponse[LanguagePackData]:
    pack = _resolve(payload.overrides)
    LANGUAGE_PACK_STORE.save(
        resolve_language_pack_file(get_settings()), pack, saved_by=_user(request)
    )
    data = _data(pack)
    data.outdated_documents = await _outdated_documents(pack)
    return ApiResponse(data=data)


def _changed_fields(overrides: object) -> int:
    if not isinstance(overrides, Mapping):
        return 0
    return sum(len(fields) for fields in overrides.values() if isinstance(fields, Mapping))


@router.get("/language-pack/history", response_model=ApiResponse[LanguagePackHistoryData])
async def get_language_pack_history() -> ApiResponse[LanguagePackHistoryData]:
    versions = LANGUAGE_PACK_STORE.history(resolve_language_pack_file(get_settings()))
    return ApiResponse(
        data=LanguagePackHistoryData(
            versions=[
                LanguagePackHistoryVersion(
                    id=str(version.get("id") or ""),
                    saved_at=str(version.get("saved_at") or ""),
                    saved_by=version.get("saved_by"),
                    hash=str(version.get("hash") or ""),
                    ingest_hash=version.get("ingest_hash"),
                    restored_from=version.get("restored_from"),
                    changed_fields=_changed_fields(version.get("overrides")),
                )
                for version in versions
                if version.get("id")
            ]
        )
    )


@router.post(
    "/language-pack/history/{version_id}/restore",
    response_model=ApiResponse[LanguagePackData],
)
async def restore_language_pack(version_id: str, request: Request) -> ApiResponse[LanguagePackData]:
    path = resolve_language_pack_file(get_settings())
    version = next(
        (item for item in LANGUAGE_PACK_STORE.history(path) if item.get("id") == version_id), None
    )
    if version is None:
        raise HTTPException(status_code=404, detail="language pack version not found")
    overrides = version.get("overrides")
    # 前の版の上書きも今の項目の定義で検証する(項目が変わって読めない版は 422)。
    pack = _resolve(overrides if isinstance(overrides, Mapping) else {})
    LANGUAGE_PACK_STORE.save(path, pack, saved_by=_user(request), restored_from=version_id)
    data = _data(pack)
    data.outdated_documents = await _outdated_documents(pack)
    return ApiResponse(data=data)


def _read(pack: LanguagePack, text: str) -> LanguagePackPreviewResult:
    with bind_pack(pack):
        references = [
            LanguagePackPreviewReference(
                label=spec.label, kind=spec.kind, key=spec.key, document_title=spec.document_title
            )
            for spec in extract_references(text)
        ]
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        return LanguagePackPreviewResult(
            references=references,
            title_keys=sorted(document_name_aliases(first_line)),
            heading_keys=sorted(f"{kind}:{key}" for kind, key in heading_keys(first_line)),
        )


@router.post("/language-pack/preview", response_model=ApiResponse[LanguagePackPreviewData])
async def preview_language_pack(
    payload: LanguagePackPreviewRequest,
) -> ApiResponse[LanguagePackPreviewData]:
    saved, _overrides = _saved()
    draft = _resolve(payload.overrides)
    return ApiResponse(
        data=LanguagePackPreviewData(
            saved=_read(saved, payload.text), draft=_read(draft, payload.text)
        )
    )
