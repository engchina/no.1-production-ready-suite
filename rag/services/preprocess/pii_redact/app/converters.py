"""PII マスク(pii_redact)前処理マイクロサービスの変換実装。

取込時に原本テキストの PII(氏名・メール・電話番号・クレジットカード番号等)を検出して
マスクし、検索索引に個人情報を載せないようにする。Microsoft Presidio(ローカル OSS)+
日本語 NER(ja spaCy)で完結し、外部 SaaS は呼ばない(確定スタック非抵触)。

**原本は保全**し(`SourceDerivation` で派生系譜=溯源を残す)、マスク済みテキストを派生
canonical として後段 parse へ渡す。検出 0 件は原本と同じ内容を派生物として返す(マスクを
実行した結果。#2068)。非テキスト・空・上限超過・解析の失敗は変換せずに理由を返し、backend が
取込を止める(原本のまま索引に載せない)。

置き換えは `［氏名］` のような全角の括弧と種別の名前にする(`<…>` は Markdown / HTML の解析で
タグとして捨てられ、本文や表が壊れる。#2068)。本文は行ごとに解析し、固有表現の範囲が改行を
またいで行の区切りを消さないようにする。

セキュリティ: warning / ログには **PII の値そのものは載せず**、entity 種別と件数だけを残す。
"""

from __future__ import annotations

import functools
import threading
from collections import Counter
from collections.abc import Callable

from charset_normalizer import from_bytes
from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceModality, SourceProfile

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
# テキストとして扱う source modality(HTML はタグを残したまま本文の PII を置き換える)。
_TEXT_MODALITIES = frozenset({SourceModality.TEXT, SourceModality.HTML})
# 解析対象の上限文字数(過大入力で CPU/メモリを浪費しない)。超えたら切り捨てずに止める。
# 日本語の NER は 1 秒に数千文字ほどのため、サービスの timeout(300 秒)に収まる長さにする。
MAX_CHARS = 1_000_000
# UTF-8 で読めない本文で先に試す日本語の文字コード(charset-normalizer の名前)。
_JAPANESE_ENCODINGS = ["cp932", "euc_jis_2004", "iso2022_jp"]
# 1 回の解析に渡す最大文字数。ja のモデルの形態素解析(Sudachi)は 49149 bytes を超える入力を
# 拒むため、UTF-8 で 1 文字 4 bytes でも収まる長さにする(#2068)。
_MAX_SEGMENT_CHARS = 10_000
# 長い行を区切るときに、区切りの位置として探す文字(句読点と空白)。
_SEGMENT_BREAKS = "。．！？!?、，, \t"
# 行ごとの解析をまとめて spaCy に渡す件数。
_BATCH_SIZE = 64

# 置き換えの表示(種別ごと)。全角の括弧は Markdown / HTML でも文字のまま残る。
PLACEHOLDER_LABELS: dict[str, str] = {
    "PERSON": "氏名",
    "EMAIL_ADDRESS": "メールアドレス",
    "PHONE_NUMBER": "電話番号",
    "CREDIT_CARD": "カード番号",
    "IBAN_CODE": "口座番号",
    "IP_ADDRESS": "IPアドレス",
    "LOCATION": "地名",
    "DATE_TIME": "日時",
    "URL": "URL",
}
_FALLBACK_LABEL = "個人情報"


def placeholder(entity_type: str) -> str:
    """種別の置き換えの文字列(例: ``［氏名］``)。"""
    return f"［{PLACEHOLDER_LABELS.get(entity_type, _FALLBACK_LABEL)}］"


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
    *,
    redactor: Redactor | None = None,
) -> ConvertOutcome:
    """選択プリセットで変換する。pii_redact 以外・非テキスト・失敗は変換せず理由を返す。"""
    if preprocess_profile != "pii_redact":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _pii_redact(
        source_bytes, content_type, source_profile, redactor=redactor or _default_redactor
    )


def _is_text(content_type: str, source_profile: SourceProfile | None) -> bool:
    """content-type / source modality からテキストかどうかを判定する。"""
    if source_profile is not None and source_profile.modality in _TEXT_MODALITIES:
        return True
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized.startswith(_TEXT_CONTENT_PREFIX):
        return True
    return normalized in _TEXT_CONTENT_TYPES


def _decode(source_bytes: bytes) -> str:
    """UTF-8 → 日本語の文字コード → charset-normalizer の全体の判定の順で読む。"""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return source_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 短い本文は判定がぶれる(Shift_JIS の日本語が別の言語の文字コードになる)ため、日本語の
    # 文字コードを先に試し、合わなければ全体から判定する。
    for candidates in (_JAPANESE_ENCODINGS, None):
        match = from_bytes(source_bytes, cp_isolation=candidates).best()
        if match is not None:
            return str(match)
    return source_bytes.decode("utf-8", errors="replace")


def _utf8_content_type(content_type: str) -> str:
    """派生物は UTF-8 で返すため、content-type の charset を utf-8 にする。"""
    media_type = content_type.split(";", 1)[0].strip() or "text/plain"
    if media_type.startswith(_TEXT_CONTENT_PREFIX):
        return f"{media_type}; charset=utf-8"
    return media_type


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
    if len(text) > MAX_CHARS:
        # 末尾を黙って捨てない(#2068)。backend が上限を案内して取込を止める。
        return ConvertOutcome.passthrough(reason="pii_too_large")
    try:
        redacted, warnings = redactor(text)
    except Exception:  # noqa: BLE001 - 解析失敗は変換せずに止める境界(原本は索引に載せない)
        return ConvertOutcome.passthrough(reason="pii_redact_failed")
    if redacted == text:
        # 検出 0 件: マスクを実行した結果として、原本と同じ内容を派生物にする(#2068)。
        warnings = ["pii_no_findings"]
    return ConvertOutcome(
        converted=True,
        converter_name="pii_redact",
        converter_version="v2",
        derived_bytes=redacted.encode("utf-8"),
        derived_content_type=_utf8_content_type(content_type),
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
# 解析言語(日本語優先。ja spaCy モデルを使う)。
_DEFAULT_LANGUAGE = "ja"
# クレジットカード番号の文脈の語(Luhn の検査に加えて score を上げる)。
_CREDIT_CARD_CONTEXT = ["クレジット", "カード", "カード番号", "credit", "card"]


def split_segments(text: str) -> list[str]:
    """本文を行(行末の改行を含む)に分け、長すぎる行は上限の長さで区切る。

    連結すると元の本文に戻る。行ごとに解析して固有表現の範囲が改行をまたがないようにする。
    """
    segments: list[str] = []
    for line in text.splitlines(keepends=True):
        while len(line) > _MAX_SEGMENT_CHARS:
            cut = _segment_cut(line)
            segments.append(line[:cut])
            line = line[cut:]
        segments.append(line)
    return segments


def _segment_cut(line: str) -> int:
    """長い行を区切る位置。上限の後半にある句読点・空白の直後、無ければ上限の位置。"""
    window = line[:_MAX_SEGMENT_CHARS]
    position = max(window.rfind(char) for char in _SEGMENT_BREAKS)
    if position >= _MAX_SEGMENT_CHARS // 2:
        return position + 1
    return _MAX_SEGMENT_CHARS


def _line_body(segment: str) -> tuple[str, str]:
    """行の本文と行末(改行)に分ける。"""
    body = segment.rstrip("\r\n")
    return body, segment[len(body) :]


def _has_word_char(value: str) -> bool:
    return any(char.isalnum() for char in value)


def _default_redactor(text: str) -> tuple[str, list[str]]:
    """Presidio で PII を検出し、種別の置き換え(``［氏名］`` 等)でマスクする。

    warning は非機密の件数だけ(``pii_redacted:PERSON=2``)。
    """
    from presidio_anonymizer.entities import OperatorConfig

    segments = split_segments(text)
    bodies = [_line_body(segment) for segment in segments]
    # 空行は解析しない(spaCy に渡さない)。
    targets = [index for index, (body, _) in enumerate(bodies) if body.strip()]
    # サービスは要求ごとに thread で変換する。共有の解析器(spaCy / Sudachi)は thread-safe でない
    # ため、同時の要求は 1 つずつ解析する(同時に使うと解析が失敗する。#2068)。
    with _ENGINE_LOCK:
        batch_analyzer, anonymizer = _engines()
        results_by_target = list(
            batch_analyzer.analyze_iterator(
                [bodies[index][0] for index in targets],
                language=_DEFAULT_LANGUAGE,
                batch_size=_BATCH_SIZE,
                entities=list(_DEFAULT_ENTITIES),
            )
        )
    operators = {
        entity: OperatorConfig("replace", {"new_value": placeholder(entity)})
        for entity in _DEFAULT_ENTITIES
    }
    operators["DEFAULT"] = OperatorConfig("replace", {"new_value": placeholder("")})
    counts: Counter[str] = Counter()
    redacted_parts = list(segments)
    for index, results in zip(targets, results_by_target, strict=True):
        body, line_end = bodies[index]
        # 文字・数字を含まない範囲(Markdown の `##`・表の `|` など)は固有表現の誤りとして捨てる。
        results = [item for item in results if _has_word_char(body[item.start : item.end])]
        if not results:
            continue
        anonymized = anonymizer.anonymize(text=body, analyzer_results=results, operators=operators)
        # 件数は重なりを解いた後の置き換えの数で数える。
        counts.update(item.entity_type for item in anonymized.items)
        redacted_parts[index] = anonymized.text + line_end
    warnings = [f"pii_redacted:{entity}={count}" for entity, count in sorted(counts.items())]
    return "".join(redacted_parts), warnings


_ENGINE_LOCK = threading.Lock()


@functools.cache
def _engines() -> tuple[object, object]:
    """解析器と匿名化器を process の中で 1 回だけ作る(日本語モデルの読み込みは重い)。"""
    from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

    analyzer = _build_analyzer(AnalyzerEngine)
    return BatchAnalyzerEngine(analyzer_engine=analyzer), AnonymizerEngine()


def _build_analyzer(analyzer_cls: type) -> object:
    """日本語対応の Presidio AnalyzerEngine を構築する(NLP エンジン設定込み)。"""
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import CreditCardRecognizer

    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": _DEFAULT_LANGUAGE, "model_name": "ja_core_news_lg"}],
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()
    analyzer = analyzer_cls(nlp_engine=nlp_engine, supported_languages=[_DEFAULT_LANGUAGE])
    # Presidio の CreditCardRecognizer は既定で英語だけに登録される。ja でも Luhn の検査つきで
    # 動かす(#2068)。
    analyzer.registry.add_recognizer(
        CreditCardRecognizer(supported_language=_DEFAULT_LANGUAGE, context=_CREDIT_CARD_CONTEXT)
    )
    return analyzer
