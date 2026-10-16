"""言語資源の画面の API(#2181)。

- ``GET /api/settings/language-pack``: 項目の定義・組み込みの値・全体の上書き・重ねた値。
- ``PUT /api/settings/language-pack``: 全体の上書きを保存する(組み込みと違う項目だけ)。
- ``POST /api/settings/language-pack/preview``: 例の文を、保存した言語資源と編集中の上書きの
  それぞれで読む。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException
from rag_parser_core.language import (
    FIELD_SPECS,
    LanguagePack,
    LanguagePackError,
    bind_pack,
    builtin_pack,
    overrides_from,
    resolve,
)

from app.config import get_settings, resolve_language_pack_file
from app.rag.cross_references import document_name_aliases, extract_references, heading_keys
from app.rag.language_pack_store import LANGUAGE_PACK_STORE
from app.schemas.common import ApiResponse
from app.schemas.settings import (
    LanguagePackData,
    LanguagePackFieldData,
    LanguagePackPreviewData,
    LanguagePackPreviewReference,
    LanguagePackPreviewRequest,
    LanguagePackPreviewResult,
    LanguagePackUpdate,
)

router = APIRouter()


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
            )
            for spec in FIELD_SPECS
        ],
        builtin=_sections(builtin),
        overrides=overrides_from(pack, builtin),
        effective=_sections(pack),
        hash=pack.hash,
    )


@router.get("/language-pack", response_model=ApiResponse[LanguagePackData])
async def get_language_pack() -> ApiResponse[LanguagePackData]:
    pack, _overrides = _saved()
    return ApiResponse(data=_data(pack))


@router.put("/language-pack", response_model=ApiResponse[LanguagePackData])
async def update_language_pack(payload: LanguagePackUpdate) -> ApiResponse[LanguagePackData]:
    pack = _resolve(payload.overrides)
    LANGUAGE_PACK_STORE.save(resolve_language_pack_file(get_settings()), pack)
    return ApiResponse(data=_data(pack))


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
