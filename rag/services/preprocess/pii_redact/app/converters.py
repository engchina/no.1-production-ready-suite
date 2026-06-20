"""PII マスク(pii_redact)前処理マイクロサービスの変換実装。

取込時に原本テキストの PII(氏名・メール・電話番号・クレジットカード番号等)を検出して
マスクし、検索索引に個人情報を載せないようにする。Microsoft Presidio(ローカル OSS)+
日本語 NER(GiNZA / ja spaCy)で完結し、外部 SaaS は呼ばない(確定スタック非抵触)。

**原本は保全**し(`SourceDerivation` で派生系譜=溯源を残す)、マスク済みテキストを派生
canonical として後段 parse へ渡す。検出 0 件・非テキスト・空・失敗は passthrough へ縮退する。

セキュリティ: warning / ログには **PII の値そのものは載せず**、entity 種別と件数だけを残す。
"""

from __future__ import annotations

from collections.abc import Callable

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# redactor: text -> (マスク済み text, 非機密 warning 群)。差し替え可能にして Presidio 非依存に
# テストできるようにする。
Redactor = Callable[[str], tuple[str, list[str]]]

# テキストとして扱う content-type 接頭辞 / 完全一致。
_TEXT_CONTENT_PREFIX = "text/"
_TEXT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-ndjson",
        "application/markdown",
    }
)
# 解析対象の上限文字数(過大入力で CPU/メモリを浪費しない)。
_MAX_CHARS = 2_000_000


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
    *,
    redactor: Redactor | None = None,
) -> ConvertOutcome:
    """選択プリセットで変換する。pii_redact 以外・非テキスト・失敗は passthrough へ縮退する。"""
    if preprocess_profile != "pii_redact":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _pii_redact(
        source_bytes, content_type, source_profile, redactor=redactor or _default_redactor
    )


def _is_text(content_type: str, source_profile: SourceProfile | None) -> bool:
    """content-type / source modality からテキストかどうかを判定する。"""
    if source_profile is not None and source_profile.modality == "text":
        return True
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized.startswith(_TEXT_CONTENT_PREFIX):
        return True
    return normalized in _TEXT_CONTENT_TYPES


def _decode(source_bytes: bytes) -> str:
    try:
        return source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return source_bytes.decode("utf-8", errors="replace")


def _pii_redact(
    source_bytes: bytes,
    content_type: str,
    source_profile: SourceProfile | None,
    *,
    redactor: Redactor,
) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="pii_empty")
    if not _is_text(content_type, source_profile):
        return ConvertOutcome.passthrough(reason="pii_not_text")
    text = _decode(source_bytes)
    if not text.strip():
        return ConvertOutcome.passthrough(reason="pii_empty")
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
    try:
        redacted, warnings = redactor(text)
    except Exception:  # noqa: BLE001 - 解析失敗は原本へ安全に縮退する境界
        return ConvertOutcome.passthrough(reason="pii_redact_failed")
    if redacted == text:
        # 検出 0 件: 変換せず原本を使う(派生物を増やさない)。
        return ConvertOutcome.passthrough(reason="pii_no_findings")
    return ConvertOutcome(
        converted=True,
        converter_name="pii_redact",
        converter_version="v1",
        derived_bytes=redacted.encode("utf-8"),
        derived_content_type=content_type or "text/plain; charset=utf-8",
        warnings=tuple(warnings),
    )


# 既定でマスクする entity 種別(Presidio の標準 + 言語非依存パターン)。
_DEFAULT_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "LOCATION",
    "DATE_TIME",
    "URL",
)
# 解析言語(日本語優先。GiNZA / ja spaCy モデルを使う)。
_DEFAULT_LANGUAGE = "ja"


def _default_redactor(text: str) -> tuple[str, list[str]]:
    """Presidio で PII を検出し `<ENTITY_TYPE>` でマスクする(非機密の件数 warning 付き)。"""
    from collections import Counter

    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    analyzer = _build_analyzer(AnalyzerEngine)
    results = analyzer.analyze(
        text=text, language=_DEFAULT_LANGUAGE, entities=list(_DEFAULT_ENTITIES)
    )
    if not results:
        return text, []
    anonymizer = AnonymizerEngine()
    operators = {
        "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"}),
    }
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    # warning は entity 種別と件数のみ(PII の値は載せない)。
    counts = Counter(result.entity_type for result in results)
    warnings = [f"pii_redacted:{entity}={count}" for entity, count in sorted(counts.items())]
    return anonymized.text, warnings


def _build_analyzer(analyzer_cls: type) -> object:
    """日本語対応の Presidio AnalyzerEngine を構築する(NLP エンジン設定込み)。"""
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": _DEFAULT_LANGUAGE, "model_name": "ja_core_news_lg"}],
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()
    return analyzer_cls(nlp_engine=nlp_engine, supported_languages=[_DEFAULT_LANGUAGE])
