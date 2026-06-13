"""RAG 入出力のガードレール。"""

import re
from dataclasses import dataclass

from app.config import Settings, get_settings

PROMPT_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"system\s*prompt",
        r"developer\s*message",
        r"jailbreak",
        r"これまでの指示を無視",
        r"システムプロンプト",
        r"開発者メッセージ",
    ]
]
GROUNDING_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龯々ー]+", re.IGNORECASE)
GROUNDING_STOPWORDS = {
    "検索",
    "根拠",
    "質問",
    "関連",
    "内容",
    "要約",
    "です",
    "ます",
    "ください",
}
MIN_GROUNDING_OVERLAP = 3
MIN_GROUNDING_RATIO = 0.12
SENSITIVE_VALUE_MASK = "[機微情報]"
SENSITIVE_IDENTIFIER_MESSAGE = "個人番号や口座番号などの機微な識別子をマスクしました。"
SENSITIVE_LABEL_SEPARATOR = r"\s*(?:[:：#-]|は|が|を|は、)?\s*"
PERSONAL_NUMBER_VALUE = r"(?:\d[\s-]?){11}\d"
BANK_ACCOUNT_VALUE = r"(?:\d[\s-]?){6,7}\d"
PHONE_NUMBER_VALUE = r"0\d{1,4}[\s-]?\d{1,4}[\s-]?\d{3,4}"
SENSITIVE_IDENTIFIER_PATTERNS = [
    re.compile(
        rf"(?P<label>(?:マイナンバー|個人番号){SENSITIVE_LABEL_SEPARATOR})"
        rf"(?P<value>{PERSONAL_NUMBER_VALUE})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<label>(?:口座番号|口座\s*(?:No\.?|番号)|普通預金|当座預金|普通|当座)"
        rf"{SENSITIVE_LABEL_SEPARATOR})(?P<value>{BANK_ACCOUNT_VALUE})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<label>(?:電話番号|携帯電話|TEL|Tel|tel){SENSITIVE_LABEL_SEPARATOR})"
        rf"(?P<value>{PHONE_NUMBER_VALUE})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<value>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
        re.IGNORECASE,
    ),
]


@dataclass(frozen=True)
class GuardrailFinding:
    """ガードレール検出結果。"""

    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class GuardrailResult:
    """ガードレール適用後の結果。"""

    allowed: bool
    sanitized_text: str
    findings: list[GuardrailFinding]

    @property
    def warnings(self) -> list[str]:
        """API レスポンス向けの警告メッセージ。"""
        return [finding.message for finding in self.findings]


class GuardrailPolicy:
    """参照実装用の明示的なガードレールポリシー。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def validate_query(self, query: str) -> GuardrailResult:
        """検索クエリを検査し、必要なら拒否する。"""
        sanitized = re.sub(r"\s+", " ", query).strip()
        findings: list[GuardrailFinding] = []
        if self._settings.guardrail_mask_sensitive_identifiers:
            sanitized, sensitive_findings = _mask_sensitive_identifiers(sanitized)
            findings.extend(sensitive_findings)

        if len(sanitized) > self._settings.guardrail_max_query_chars:
            return GuardrailResult(
                allowed=False,
                sanitized_text=sanitized[: self._settings.guardrail_max_query_chars],
                findings=[
                    *findings,
                    GuardrailFinding(
                        code="query_too_long",
                        severity="error",
                        message="クエリが長すぎるため処理できません。",
                    ),
                ],
            )

        if self._settings.guardrail_block_prompt_injection:
            for pattern in PROMPT_INJECTION_PATTERNS:
                if pattern.search(sanitized):
                    return GuardrailResult(
                        allowed=False,
                        sanitized_text=sanitized,
                        findings=[
                            *findings,
                            GuardrailFinding(
                                code="prompt_injection",
                                severity="error",
                                message=(
                                    "システム指示の抽出や無効化を求める内容は処理できません。"
                                ),
                            ),
                        ],
                    )

        if _looks_like_sql_mutation(sanitized):
            findings.append(
                GuardrailFinding(
                    code="sql_mutation_intent",
                    severity="warning",
                    message="データ変更を伴う SQL 風の文言を検出しました。検索のみ実行します。",
                )
            )

        return GuardrailResult(allowed=True, sanitized_text=sanitized, findings=findings)

    def validate_answer(self, answer: str, context: str | None = None) -> GuardrailResult:
        """回答テキストを検査する。"""
        findings: list[GuardrailFinding] = []
        if "OCI_SECRET" in answer or "ORACLE_PASSWORD" in answer:
            return GuardrailResult(
                allowed=False,
                sanitized_text="機密情報を含む可能性があるため回答を表示できません。",
                findings=[
                    GuardrailFinding(
                        code="secret_leakage",
                        severity="error",
                        message="回答に機密情報らしき文字列が含まれました。",
                    )
                ],
            )
        sanitized = answer
        if self._settings.guardrail_mask_sensitive_identifiers:
            sanitized, sensitive_findings = _mask_sensitive_identifiers(sanitized)
            findings.extend(sensitive_findings)
        if context is not None and not _is_grounded_in_context(sanitized, context):
            findings.append(
                GuardrailFinding(
                    code="low_groundedness",
                    severity="warning",
                    message="回答と検索根拠の重なりが少ないため、引用を確認してください。",
                )
            )
        return GuardrailResult(allowed=True, sanitized_text=sanitized, findings=findings)


def _looks_like_sql_mutation(text: str) -> bool:
    """SELECT 以外の SQL 変更文らしさを検出する。"""
    return bool(re.search(r"\b(drop|delete|truncate|update|insert|merge)\b", text, re.I))


def _mask_sensitive_identifiers(text: str) -> tuple[str, list[GuardrailFinding]]:
    """個人番号・口座番号などの機微な識別子をマスクする。"""
    masked = text
    matched = False
    for pattern in SENSITIVE_IDENTIFIER_PATTERNS:
        masked, count = pattern.subn(_sensitive_replacement, masked)
        matched = matched or count > 0
    if not matched:
        return masked, []
    return masked, [
        GuardrailFinding(
            code="sensitive_identifier_redacted",
            severity="warning",
            message=SENSITIVE_IDENTIFIER_MESSAGE,
        )
    ]


def _sensitive_replacement(match: re.Match[str]) -> str:
    label = match.groupdict().get("label") or ""
    return f"{label}{SENSITIVE_VALUE_MASK}"


def _is_grounded_in_context(answer: str, context: str) -> bool:
    """回答が引用 context と最低限重なっているかを軽量に評価する。"""
    if not answer.strip() or not context.strip():
        return True

    answer_features = _grounding_features(answer)
    context_features = _grounding_features(context)
    if not answer_features or not context_features:
        return True

    overlap = answer_features & context_features
    high_signal = {feature for feature in answer_features if _is_high_signal_feature(feature)}
    if high_signal and high_signal & context_features:
        return True
    return (
        len(overlap) >= MIN_GROUNDING_OVERLAP
        or len(overlap) / len(answer_features) >= MIN_GROUNDING_RATIO
    )


def _grounding_features(text: str) -> set[str]:
    """日本語・英数字の token と日本語 n-gram を groundedness 用特徴にする。"""
    features: set[str] = set()
    for match in GROUNDING_TOKEN_PATTERN.finditer(text):
        token = match.group(0).lower()
        if len(token) < 2 or token in GROUNDING_STOPWORDS:
            continue
        features.add(token)
        if _contains_japanese(token) and len(token) >= 4:
            features.update(_char_ngrams(token, 3))
    return features


def _contains_japanese(text: str) -> bool:
    """日本語文字を含むか。"""
    return bool(re.search(r"[ぁ-んァ-ン一-龯々ー]", text))


def _char_ngrams(text: str, n: int) -> set[str]:
    """空白除去済み文字 n-gram。"""
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) < n:
        return set()
    return {normalized[index : index + n] for index in range(len(normalized) - n + 1)}


def _is_high_signal_feature(feature: str) -> bool:
    """金額・日付・ID らしい特徴は強い根拠として扱う。"""
    return any(char.isdigit() for char in feature) or bool(
        re.fullmatch(r"[a-z][a-z0-9_-]{3,}", feature, re.IGNORECASE)
    )
