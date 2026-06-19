"""RAG 入出力のガードレール。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.config import Settings, get_settings
from app.rag.guardrail_adapter import resolve_guardrail_adapter

if TYPE_CHECKING:
    from app.clients.oci_guardrails import GuardrailInspection


class _OciGuardrailsLike(Protocol):
    """GuardrailPolicy が使う OCI Guardrails クライアントの最小契約(テスト fake も満たす)。"""

    def inspect_text(self, text: str) -> GuardrailInspection | None: ...

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
SQL_MUTATION_INTENT_PATTERN = re.compile(
    r"\b(drop|delete|truncate|update|insert|merge)\b|"
    r"(削除|消去|更新|挿入|追加|上書き)\s*(?:して|する|してください|を実行)",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class GroundednessEvaluation:
    """回答が citation context に支えられているかの軽量評価。"""

    grounded: bool
    score: float
    overlap_count: int
    answer_feature_count: int
    high_signal_overlap: bool


class GuardrailPolicy:
    """参照実装用の明示的なガードレールポリシー。

    backend が ``oci_guardrails`` のときは、in-process(local)検査に加えて OCI Generative AI
    Guardrails(ApplyGuardrails)で content moderation / PII / prompt injection を検出して
    増強する。OCI 側が未設定・失敗のときは local の結果へ安全に縮退する。
    """

    def __init__(
        self, settings: Settings | None = None, oci_client: _OciGuardrailsLike | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._params = resolve_guardrail_adapter(self._settings)
        self._oci_client: _OciGuardrailsLike | None = (
            oci_client if oci_client is not None else _maybe_oci_client(self._settings)
        )

    def validate_query(self, query: str) -> GuardrailResult:
        """検索クエリを検査し、必要なら拒否する(local → OCI 増強)。"""
        result = self._local_validate_query(query)
        return self._augment_with_oci(result, block_on_flag=True)

    def _local_validate_query(self, query: str) -> GuardrailResult:
        """in-process(local)決定論ガードレール。"""
        sanitized = re.sub(r"\s+", " ", query).strip()
        findings: list[GuardrailFinding] = []
        if self._params.mask_sensitive_identifiers:
            sanitized, sensitive_findings = _mask_sensitive_identifiers(sanitized)
            findings.extend(sensitive_findings)

        if len(sanitized) > self._params.max_query_chars:
            return GuardrailResult(
                allowed=False,
                sanitized_text=sanitized[: self._params.max_query_chars],
                findings=[
                    *findings,
                    GuardrailFinding(
                        code="query_too_long",
                        severity="error",
                        message="クエリが長すぎるため処理できません。",
                    ),
                ],
            )

        if self._params.block_prompt_injection:
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
        """回答テキストを検査する(local → OCI 増強)。"""
        result = self._local_validate_answer(answer, context)
        # 回答側は moderation / PII を検出しても回答自体は止めず warning に留める
        # (引用確認は low_groundedness と同様に運用判断へ委ねる)。
        return self._augment_with_oci(result, block_on_flag=False)

    def _local_validate_answer(self, answer: str, context: str | None = None) -> GuardrailResult:
        """in-process(local)決定論ガードレール(回答側)。"""
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
        if self._params.mask_sensitive_identifiers:
            sanitized, sensitive_findings = _mask_sensitive_identifiers(sanitized)
            findings.extend(sensitive_findings)
        if context is not None and not evaluate_groundedness(
            sanitized,
            context,
            min_overlap=self._params.grounding_min_overlap,
            min_ratio=self._params.grounding_min_ratio,
        ).grounded:
            findings.append(
                GuardrailFinding(
                    code="low_groundedness",
                    severity="error" if self._params.audit_emphasis else "warning",
                    message="回答と検索根拠の重なりが少ないため、引用を確認してください。",
                )
            )
        return GuardrailResult(allowed=True, sanitized_text=sanitized, findings=findings)

    def _augment_with_oci(self, result: GuardrailResult, *, block_on_flag: bool) -> GuardrailResult:
        """OCI Guardrails の検出で result を増強する。未設定/失敗時は result をそのまま返す。"""
        if self._oci_client is None or not result.sanitized_text.strip():
            return result
        inspection = self._oci_client.inspect_text(result.sanitized_text)
        if inspection is None or not inspection.flagged:
            return result
        extra: list[GuardrailFinding] = []
        blocked = not result.allowed
        if inspection.prompt_injection:
            extra.append(
                GuardrailFinding(
                    code="oci_prompt_injection",
                    severity="error",
                    message="OCI Guardrails が prompt injection を検出しました。",
                )
            )
            blocked = blocked or block_on_flag
        if inspection.moderation_categories:
            extra.append(
                GuardrailFinding(
                    code="oci_content_moderation",
                    severity="error" if block_on_flag else "warning",
                    message="OCI Guardrails が不適切な内容を検出しました。",
                )
            )
            blocked = blocked or block_on_flag
        if inspection.pii_labels:
            # PII の値は載せず、検出 label 種別のみを残す(privacy)。
            labels = ",".join(sorted(set(inspection.pii_labels)))
            extra.append(
                GuardrailFinding(
                    code="oci_pii_detected",
                    severity="warning",
                    message=f"OCI Guardrails が個人情報の可能性を検出しました({labels})。",
                )
            )
        if not extra:
            return result
        return GuardrailResult(
            allowed=not blocked,
            sanitized_text=result.sanitized_text,
            findings=[*result.findings, *extra],
        )


def _maybe_oci_client(settings: Settings) -> _OciGuardrailsLike | None:
    """backend が oci_guardrails のときだけ OCI Guardrails クライアントを生成する。"""
    backend = str(getattr(settings, "rag_guardrail_backend", "local") or "local")
    if backend != "oci_guardrails":
        return None
    from app.clients.oci_guardrails import OciGuardrailsClient

    return OciGuardrailsClient(settings)


def _looks_like_sql_mutation(text: str) -> bool:
    """SELECT 以外の SQL 変更文らしさを検出する。"""
    return bool(SQL_MUTATION_INTENT_PATTERN.search(text))


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


def evaluate_groundedness(
    answer: str,
    context: str,
    *,
    min_overlap: int = MIN_GROUNDING_OVERLAP,
    min_ratio: float = MIN_GROUNDING_RATIO,
) -> GroundednessEvaluation:
    """回答が引用 context と最低限重なっているかを軽量に評価する。

    `min_overlap` / `min_ratio` は Guardrail アダプターの policy が解決する閾値。
    既定は現行定数で、standard policy と一致する。
    """
    if not answer.strip() or not context.strip():
        return GroundednessEvaluation(
            grounded=True,
            score=1.0,
            overlap_count=0,
            answer_feature_count=0,
            high_signal_overlap=False,
        )

    answer_features = _grounding_features(answer)
    context_features = _grounding_features(context)
    if not answer_features or not context_features:
        return GroundednessEvaluation(
            grounded=True,
            score=1.0,
            overlap_count=0,
            answer_feature_count=len(answer_features),
            high_signal_overlap=False,
        )

    overlap = answer_features & context_features
    high_signal = {feature for feature in answer_features if _is_high_signal_feature(feature)}
    if high_signal and high_signal & context_features:
        return GroundednessEvaluation(
            grounded=True,
            score=1.0,
            overlap_count=len(overlap),
            answer_feature_count=len(answer_features),
            high_signal_overlap=True,
        )
    score = round(min(1.0, len(overlap) / len(answer_features)), 4)
    return GroundednessEvaluation(
        grounded=len(overlap) >= min_overlap or score >= min_ratio,
        score=score,
        overlap_count=len(overlap),
        answer_feature_count=len(answer_features),
        high_signal_overlap=False,
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
