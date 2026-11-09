"""形式別の既定（ファイル準備・解析エンジン・分割方式。#4732）。

全体の設定の層で、解決の順は「文書のレシピ > 形式別の既定 > 全体の既定」。KB・検索・回答
プロファイルの層には関わらない（KB に取り込みの設定を持たせない）。種類は拡張子と content type の
形式の解析だけで決める。受け付ける形式は ``ACCEPTED_UPLOAD_EXTENSIONS``（#4730）。一覧に無い
形式は全体の既定に従う。

保存時の組み合わせの検証と画面の選択肢は同じ判定（``adapter_supports_source``）を使う。旧形式の
Office（.doc / .ppt / .xls）は原本のままでは解析しないため判定に含めない（「変換なし」のとき
取込の前に ``parser_source_guard`` が変換を案内して止める）。
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import PurePath
from typing import cast, get_args

from rag_parser_core.capabilities import ADAPTER_CAPABILITIES, adapter_supports_source
from rag_parser_core.structured_records import (
    JSONL_RECORD_BOUNDARY,
    STRUCTURED_RECORDS_JSONL_BACKEND,
)

from app.clients.external_parser import ENGINE_SPECS, external_parser_connection
from app.config import (
    ChunkingStrategy,
    FileType,
    FileTypeDefaultEntry,
    ParserAdapterBackend,
    PreprocessProfile,
    Settings,
    is_roadmap_parser_adapter_backend,
    roadmap_parser_adapter_backend_message,
)
from app.rag.kb_adapter_config import _EXTERNAL_PARSER_BACKEND_FLAGS
from app.rag.mineru_license import (
    MINERU_BACKEND,
    MINERU_LICENSE_REQUIRED_MESSAGE,
    mineru_license_acknowledged,
)
from app.rag.parser_source_guard import (
    STRUCTURED_RECORDS_PREPROCESS,
    parser_backend_label,
    requires_office_conversion,
)
from app.rag.preprocess_strategy import (
    PREPROCESS_PROFILE_ORDER,
    preprocess_runtime_settings,
    resolve_preprocess_profile,
)
from app.rag.source_profile import build_source_profile
from app.schemas.document import SourceProfile

# 種類と拡張子の対応（画面の行の順）。拡張子はドット付きの小文字。
FILE_TYPE_EXTENSIONS: dict[FileType, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "word": (".docx", ".doc"),
    "powerpoint": (".pptx", ".ppt"),
    "excel": (".xlsx", ".xls"),
    "csv": (".csv",),
    "text": (".txt",),
    "markdown": (".md", ".markdown"),
    "json": (".json",),
    "html": (".html", ".htm"),
    "image": (".jpg", ".jpeg", ".png"),
}
_FILE_TYPE_LABELS: dict[FileType, str] = {
    "pdf": "PDF",
    "word": "Word",
    "powerpoint": "PowerPoint",
    "excel": "Excel",
    "csv": "CSV",
    "text": "テキスト",
    "markdown": "Markdown",
    "json": "JSON",
    "html": "HTML",
    "image": "画像",
}
_FILE_TYPE_BY_EXTENSION: dict[str, FileType] = {
    extension: file_type
    for file_type, extensions in FILE_TYPE_EXTENSIONS.items()
    for extension in extensions
}
# 拡張子の無い原本は content type で決める（受け付ける MIME type と同じ。#4730）。
_FILE_TYPE_BY_CONTENT_TYPE: dict[str, FileType] = {
    "application/pdf": "pdf",
    "application/msword": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-powerpoint": "powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "powerpoint",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "text/csv": "csv",
    "application/csv": "csv",
    "text/plain": "text",
    "text/markdown": "markdown",
    "application/json": "json",
    "text/html": "html",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
}
# 種類ごとに形式を変えられるファイル準備（「変換なし」は全種類）。Office→PDF は Excel を変換しない。
_CONVERTING_PREPROCESS: dict[FileType, frozenset[PreprocessProfile]] = {
    "word": frozenset({"office_to_pdf", "word_to_jsonl"}),
    "powerpoint": frozenset({"office_to_pdf"}),
    "excel": frozenset({"excel_to_jsonl"}),
    "csv": frozenset({"csv_to_jsonl"}),
}
_PREPROCESS_LABELS: dict[PreprocessProfile, str] = {
    "passthrough": "変換なし",
    "office_to_pdf": "Office→PDF",
    "csv_to_jsonl": "CSV→JSONL",
    "excel_to_jsonl": "Excel→JSONL",
    "word_to_jsonl": "Word→JSONL",
}
CHUNKING_STRATEGY_OPTIONS: tuple[ChunkingStrategy, ...] = get_args(ChunkingStrategy)
# 保存値の無い行に使う組み込みの既定（行の順）。この環境で使えない行は全体の既定に従う。
BUILTIN_FILE_TYPE_DEFAULTS: dict[FileType, FileTypeDefaultEntry] = {
    "pdf": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="docling",
        chunking_strategy="small_to_big",
    ),
    "word": FileTypeDefaultEntry(
        preprocess_profile="office_to_pdf",
        parser_adapter_backend="docling",
        chunking_strategy="small_to_big",
    ),
    "powerpoint": FileTypeDefaultEntry(
        preprocess_profile="office_to_pdf",
        parser_adapter_backend="docling",
        chunking_strategy="page_level",
    ),
    "excel": FileTypeDefaultEntry(
        preprocess_profile="excel_to_jsonl",
        parser_adapter_backend="structured_records_jsonl",
        chunking_strategy="fixed_delimiter",
    ),
    "csv": FileTypeDefaultEntry(
        preprocess_profile="csv_to_jsonl",
        parser_adapter_backend="structured_records_jsonl",
        chunking_strategy="fixed_delimiter",
    ),
    "text": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="unstructured",
        chunking_strategy="small_to_big",
    ),
    "markdown": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="unstructured",
        chunking_strategy="structure_aware",
    ),
    "json": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="unstructured",
        chunking_strategy="small_to_big",
    ),
    "html": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="unstructured",
        chunking_strategy="structure_aware",
    ),
    "image": FileTypeDefaultEntry(
        preprocess_profile="passthrough",
        parser_adapter_backend="docling",
        chunking_strategy="page_level",
    ),
}


def classify_file_type(file_name: str | None, content_type: str | None) -> FileType | None:
    """拡張子（無ければ content type）から種類を決める。一覧に無い形式は None。"""
    extension = PurePath(file_name or "").suffix.casefold()
    if extension:
        return _FILE_TYPE_BY_EXTENSION.get(extension)
    return _FILE_TYPE_BY_CONTENT_TYPE.get((content_type or "").split(";", 1)[0].strip().casefold())


def file_type_defaults(settings: Settings) -> Mapping[str, FileTypeDefaultEntry]:
    """保存済みの形式別の既定（種類 → 行）。"""
    return getattr(settings, "_file_type_defaults", {})


def _builtin_unavailable_reason(entry: FileTypeDefaultEntry, settings: Settings) -> str | None:
    """組み込みの行がこの環境で使えない理由。使えるなら None。"""
    preprocess = entry.preprocess_profile
    if preprocess is not None:
        available = {
            status.name
            for status in preprocess_runtime_settings(settings).profiles
            if status.available
        }
        if preprocess not in available:
            return (
                f"ファイル準備「{_PREPROCESS_LABELS[preprocess]}」が無効"
                "（RAG_PREPROCESS_ENABLED）のため、組み込みの既定を使わず全体の既定に従います。"
            )
    backend = entry.parser_adapter_backend
    flag = _EXTERNAL_PARSER_BACKEND_FLAGS.get(backend) if backend is not None else None
    if backend is not None and flag is not None and not getattr(settings, flag, False):
        return (
            f"解析エンジン「{parser_backend_label(backend)}」が無効（{flag.upper()}）のため、"
            "組み込みの既定を使わず全体の既定に従います。"
        )
    return None


def builtin_file_type_default(
    settings: Settings, file_type: FileType
) -> tuple[FileTypeDefaultEntry, str | None]:
    """この環境で使う組み込みの行と、使えないときの理由。

    一部の項目だけを残すと組み合わせが壊れる（例: 変換なしの Excel と構造化レコード JSONL）ため、
    使えないときは行全体を全体の既定に従わせる。
    """
    entry = BUILTIN_FILE_TYPE_DEFAULTS[file_type]
    reason = _builtin_unavailable_reason(entry, settings)
    return (FileTypeDefaultEntry(), reason) if reason else (entry, None)


def file_type_default_overrides(settings: Settings, file_type: FileType) -> dict[str, object]:
    """形式の行が決める処理の設定（``DocumentProcessingConfig`` のフィールド）。

    保存値があればその行、無ければ組み込みの既定。全項目が未設定の行は全体の既定に従う。
    構造化レコードの行を固定分割符で分けるときは、記録の境界を分割符にする（全体の分割符が別の
    値でも 1 記録 1 chunk になる）。
    """
    entry = file_type_defaults(settings).get(file_type)
    if entry is None:
        entry = builtin_file_type_default(settings, file_type)[0]
    values: dict[str, object] = entry.model_dump(exclude_none=True)
    if (
        entry.preprocess_profile in STRUCTURED_RECORDS_PREPROCESS
        and entry.chunking_strategy == "fixed_delimiter"
    ):
        values["chunk_delimiter"] = [JSONL_RECORD_BOUNDARY]
    return values


@cache
def _sample_profile(extension: str) -> SourceProfile:
    """形式の判定に使う、その拡張子の source profile（内容は持たない）。"""
    file_name = f"sample{extension}"
    return build_source_profile(
        original_file_name=file_name,
        sanitized_file_name=file_name,
        content_type=None,
        file_size_bytes=0,
        content_sha256=None,
    )


def _preprocess_candidates(file_type: FileType) -> list[PreprocessProfile]:
    converting = _CONVERTING_PREPROCESS.get(file_type, frozenset())
    return [
        name for name in PREPROCESS_PROFILE_ORDER if name == "passthrough" or name in converting
    ]


def _inherited_preprocess(file_type: FileType, settings: Settings) -> PreprocessProfile:
    """行のファイル準備が未設定のときに解析へ渡る形を決めるファイル準備。

    全体のファイル準備がこの種類に効かなければ（例: 全体が CSV→JSONL のときの PDF）、変換しない
    形で判定する。
    """
    inherited = resolve_preprocess_profile(settings)
    return inherited if inherited in _preprocess_candidates(file_type) else "passthrough"


def _supported_parser_backends(
    file_type: FileType, preprocess: PreprocessProfile
) -> list[ParserAdapterBackend]:
    """この種類とファイル準備の組み合わせで解析できるエンジン（宣言順）。"""
    if preprocess in STRUCTURED_RECORDS_PREPROCESS:
        return [cast("ParserAdapterBackend", STRUCTURED_RECORDS_JSONL_BACKEND)]
    profiles = (
        [_sample_profile(".pdf")]
        if preprocess == "office_to_pdf"
        else [
            profile
            for profile in map(_sample_profile, FILE_TYPE_EXTENSIONS[file_type])
            if not requires_office_conversion(profile)
        ]
    )
    return [
        cast("ParserAdapterBackend", backend)
        for backend in ADAPTER_CAPABILITIES
        if all(
            adapter_supports_source(
                backend, source_profile=profile, content_type=profile.content_type
            )
            for profile in profiles
        )
    ]


def file_type_preprocess_options(
    file_type: FileType, settings: Settings
) -> tuple[list[PreprocessProfile], list[PreprocessProfile]]:
    """種類に対応するファイル準備を、(選べるもの, サービス未設定で今は使えないもの) で返す。"""
    available = {
        status.name for status in preprocess_runtime_settings(settings).profiles if status.available
    }
    candidates = _preprocess_candidates(file_type)
    return (
        [name for name in candidates if name in available],
        [name for name in candidates if name not in available],
    )


def _parser_usable(backend: str, settings: Settings) -> bool:
    if backend not in ENGINE_SPECS:
        return True
    return external_parser_connection(settings, backend).configured


def file_type_parser_options(
    file_type: FileType, preprocess: PreprocessProfile, settings: Settings
) -> tuple[list[ParserAdapterBackend], list[ParserAdapterBackend]]:
    """対応する解析エンジンを、(選べるもの, 接続が未設定で今は使えないもの) で返す。

    準備中のエンジンは選択肢に出さない（設定で使えるようにできないため、案内もしない）。
    """
    supported = [
        backend
        for backend in _supported_parser_backends(file_type, preprocess)
        if not is_roadmap_parser_adapter_backend(backend)
    ]
    return (
        [backend for backend in supported if _parser_usable(backend, settings)],
        [backend for backend in supported if not _parser_usable(backend, settings)],
    )


def file_type_parser_option_table(
    file_type: FileType, settings: Settings
) -> tuple[PreprocessProfile, dict[str, list[ParserAdapterBackend]], list[ParserAdapterBackend]]:
    """画面の行に渡す (未設定のときのファイル準備, 準備 → エンジン, 今は使えないエンジン)。"""
    options: dict[str, list[ParserAdapterBackend]] = {}
    unavailable: dict[ParserAdapterBackend, None] = {}
    for preprocess in _preprocess_candidates(file_type):
        selectable, not_usable = file_type_parser_options(file_type, preprocess, settings)
        options[preprocess] = selectable
        unavailable.update(dict.fromkeys(not_usable))
    return _inherited_preprocess(file_type, settings), options, list(unavailable)


def _labels(names: list[PreprocessProfile]) -> str:
    return "」「".join(_PREPROCESS_LABELS[name] for name in names)


def _preprocess_issue(file_type: FileType, preprocess: PreprocessProfile) -> str:
    label = _FILE_TYPE_LABELS[file_type]
    if file_type == "excel" and preprocess == "office_to_pdf":
        return (
            "Excel は「Office→PDF」で変換されません。"
            "ファイル準備で「Excel→JSONL」を選ぶか、全体の既定に従ってください。"
        )
    return (
        f"{label} のファイル準備に「{_PREPROCESS_LABELS[preprocess]}」は使えません。"
        f"「{_labels(_preprocess_candidates(file_type))}」から選ぶか、全体の既定に従ってください。"
    )


def _parser_issue(
    file_type: FileType, backend: ParserAdapterBackend, preprocess: PreprocessProfile
) -> str:
    label = _FILE_TYPE_LABELS[file_type]
    engine = parser_backend_label(backend)
    convertible = [
        name
        for name in _preprocess_candidates(file_type)
        if backend in _supported_parser_backends(file_type, name)
    ]
    if convertible:
        return (
            f"{label} は「{_PREPROCESS_LABELS[preprocess]}」のまま {engine} では解析できません。"
            f"ファイル準備で「{_labels(convertible)}」を選ぶか、別の解析エンジンを選んでください。"
        )
    return f"{label} は {engine} では解析できません。別の解析エンジンを選んでください。"


def file_type_default_issues(
    file_type: FileType, entry: FileTypeDefaultEntry, settings: Settings
) -> list[str]:
    """行の組み合わせの問題（何を直すかの文言）。無ければ空。"""
    issues: list[str] = []
    preprocess = entry.preprocess_profile
    backend = entry.parser_adapter_backend
    preprocess_valid = preprocess is None or preprocess in _preprocess_candidates(file_type)
    if preprocess is not None and not preprocess_valid:
        issues.append(_preprocess_issue(file_type, preprocess))
    if preprocess in STRUCTURED_RECORDS_PREPROCESS and backend != STRUCTURED_RECORDS_JSONL_BACKEND:
        issues.append(
            f"{_FILE_TYPE_LABELS[file_type]} のファイル準備「{_PREPROCESS_LABELS[preprocess]}」"
            "には、解析エンジン「構造化レコード JSONL」が必要です。解析エンジンを選び直して"
            "ください。"
        )
    if backend is None:
        return issues
    roadmap = is_roadmap_parser_adapter_backend(backend)
    if roadmap:
        issues.append(roadmap_parser_adapter_backend_message(backend))
    if backend == MINERU_BACKEND and not mineru_license_acknowledged(settings):
        issues.append(MINERU_LICENSE_REQUIRED_MESSAGE)
    structured_error = (
        preprocess in STRUCTURED_RECORDS_PREPROCESS and backend != STRUCTURED_RECORDS_JSONL_BACKEND
    )
    if roadmap or not preprocess_valid or structured_error:
        return issues
    effective_preprocess = preprocess or _inherited_preprocess(file_type, settings)
    if backend not in _supported_parser_backends(file_type, effective_preprocess):
        issues.append(_parser_issue(file_type, backend, effective_preprocess))
    return issues
