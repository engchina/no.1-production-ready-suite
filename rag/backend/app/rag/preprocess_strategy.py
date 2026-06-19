"""前処理(Preprocess)アダプターの runtime レジストリ。

`chunking_strategy.py` / `parser_adapter_readiness.py` と同型で、選択された変換
プリセットと利用可能なプリセット一覧を非機密 runtime snapshot として返す。実際の
変換は in-process(`text_normalize`)または前処理マイクロサービス(`office_to_pdf` /
`pdf_to_page_images`)へ委譲する。確定スタックは不変で、外部変換物は本プロジェクトの
`SourceDerivation`(派生系譜)へ決定論的に再マップする。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import PreprocessProfile, Settings

PreprocessProfileName = PreprocessProfile
DEFAULT_PREPROCESS_PROFILE: PreprocessProfileName = "passthrough"
PREPROCESS_PROFILE_ORDER: tuple[PreprocessProfileName, ...] = (
    "passthrough",
    "text_normalize",
    "office_to_pdf",
    "pdf_to_page_images",
    "csv_to_json",
    "excel_to_json",
)


@dataclass(frozen=True)
class PreprocessProfileSpec:
    """1 プリセットの由来・適用場面・実行基盤(機械可読の非機密 metadata)。

    ``requires_service`` が真のプリセットは前処理マイクロサービス(`rag_preprocess_enabled`)
    が無いと passthrough へ安全に縮退する。``in_process`` はサービス無しでも実行できる。
    """

    name: PreprocessProfileName
    origin: str
    recommended_for: tuple[str, ...]
    in_process: bool = False
    requires_service: bool = False


PREPROCESS_PROFILE_SPECS: dict[PreprocessProfileName, PreprocessProfileSpec] = {
    "passthrough": PreprocessProfileSpec(
        name="passthrough",
        origin="baseline_no_conversion",
        recommended_for=("any",),
        in_process=True,
    ),
    "text_normalize": PreprocessProfileSpec(
        name="text_normalize",
        origin="unstructured_text_cleaning",
        recommended_for=("text", "html", "email"),
        in_process=True,
    ),
    "office_to_pdf": PreprocessProfileSpec(
        name="office_to_pdf",
        origin="libreoffice_headless",
        recommended_for=("office",),
        requires_service=True,
    ),
    "pdf_to_page_images": PreprocessProfileSpec(
        name="pdf_to_page_images",
        origin="no1_pdfparser_page_images",
        recommended_for=("pdf", "scan"),
        requires_service=True,
    ),
    "csv_to_json": PreprocessProfileSpec(
        name="csv_to_json",
        origin="no1_csv2json_records",
        recommended_for=("csv", "table"),
        requires_service=True,
    ),
    "excel_to_json": PreprocessProfileSpec(
        name="excel_to_json",
        origin="no1_excel2json_records",
        recommended_for=("excel", "xls", "xlsx", "table"),
        requires_service=True,
    ),
}


@dataclass(frozen=True)
class PreprocessProfileStatus:
    """1 プリセットの選択状態と適用場面。"""

    name: PreprocessProfileName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    in_process: bool
    requires_service: bool
    available: bool


@dataclass(frozen=True)
class PreprocessRuntimeSettings:
    """前処理アダプターの非機密 runtime snapshot。"""

    profile: PreprocessProfileName
    service_enabled: bool
    service_url: str
    canonical_artifact_prefix: str
    profiles: tuple[PreprocessProfileStatus, ...]


# 各 service 必須プロファイルが委譲する前処理マイクロサービスの URL 設定名。
PREPROCESS_SERVICE_URL_ATTRS: dict[PreprocessProfileName, str] = {
    "office_to_pdf": "rag_preprocess_office_to_pdf_service_url",
    "pdf_to_page_images": "rag_preprocess_pdf_to_page_images_service_url",
    "csv_to_json": "rag_preprocess_csv_to_json_service_url",
    "excel_to_json": "rag_preprocess_excel_to_json_service_url",
}


def preprocess_service_url(settings: Settings, profile: PreprocessProfileName) -> str | None:
    """profile に対応する前処理マイクロサービスの base URL を返す(無ければ None)。

    dev では catalog の dev_port から 127.0.0.1:<port> に解決する(/health プローブと
    取込の HTTP 委譲で同じ URL を使う)。prod は設定値そのまま。
    """
    attr = PREPROCESS_SERVICE_URL_ATTRS.get(profile)
    if attr is None:
        return None
    from app.services.catalog import resolve_service_base_url

    url = resolve_service_base_url(settings, attr)
    return url or None


def normalize_preprocess_profile(value: object) -> PreprocessProfileName:
    """未知のプリセット名は既定 passthrough へ寄せる。"""
    normalized = str(value).strip().casefold()
    for name in PREPROCESS_PROFILE_ORDER:
        if normalized == name:
            return name
    return DEFAULT_PREPROCESS_PROFILE


def resolve_preprocess_profile(settings: Settings) -> PreprocessProfileName:
    """Settings から有効な前処理プリセットを解決する。"""
    return normalize_preprocess_profile(
        getattr(settings, "rag_preprocess_profile", DEFAULT_PREPROCESS_PROFILE)
    )


def _profile_available(spec: PreprocessProfileSpec, *, service_enabled: bool) -> bool:
    """サービス無し環境で実行可能かどうか(requires_service は service_enabled が必要)。"""
    if spec.in_process:
        return True
    return service_enabled


def preprocess_runtime_settings(settings: Settings) -> PreprocessRuntimeSettings:
    """Settings から前処理アダプター readiness snapshot を作る。"""
    profile = resolve_preprocess_profile(settings)
    service_enabled = bool(getattr(settings, "rag_preprocess_enabled", False))
    statuses = tuple(
        PreprocessProfileStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == profile,
            in_process=spec.in_process,
            requires_service=spec.requires_service,
            available=_profile_available(spec, service_enabled=service_enabled),
        )
        for spec in (PREPROCESS_PROFILE_SPECS[name] for name in PREPROCESS_PROFILE_ORDER)
    )
    service_urls = ", ".join(
        url
        for url in (
            preprocess_service_url(settings, name)
            for name in PREPROCESS_SERVICE_URL_ATTRS
        )
        if url
    )
    return PreprocessRuntimeSettings(
        profile=profile,
        service_enabled=service_enabled,
        service_url=service_urls,
        canonical_artifact_prefix=str(
            getattr(settings, "rag_canonical_artifact_prefix", "artifacts/canonical")
        ),
        profiles=statuses,
    )
