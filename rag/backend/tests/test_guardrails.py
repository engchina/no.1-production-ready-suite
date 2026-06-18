"""RAG guardrail policy のテスト。"""

from app.config import Settings
from app.rag.guardrails import GuardrailPolicy, evaluate_groundedness


def test_validate_answer_blocks_secret_leakage() -> None:
    """secret らしき文字列を含む回答は表示しない。"""
    result = GuardrailPolicy().validate_answer("OCI_SECRET=raw-value")

    assert result.allowed is False
    assert result.sanitized_text == "機密情報を含む可能性があるため回答を表示できません。"
    assert [finding.code for finding in result.findings] == ["secret_leakage"]


def test_validate_query_masks_sensitive_identifiers_before_embedding() -> None:
    """検索 query の個人番号・口座番号・メールアドレスは embedding 前にマスクする。"""
    result = GuardrailPolicy().validate_query(
        "個人番号 1234-5678-9012 と 口座番号 1234567 と user@example.com を確認"
    )

    assert result.allowed is True
    assert result.findings[0].code == "sensitive_identifier_redacted"
    assert "1234-5678-9012" not in result.sanitized_text
    assert "1234567" not in result.sanitized_text
    assert "user@example.com" not in result.sanitized_text
    assert result.sanitized_text.count("[機微情報]") == 3


def test_sensitive_identifier_masking_can_be_disabled() -> None:
    """外部 DLP と併用する場合は app 内マスクを無効化できる。"""
    result = GuardrailPolicy(Settings(guardrail_mask_sensitive_identifiers=False)).validate_query(
        "口座番号 1234567"
    )

    assert result.allowed is True
    assert result.findings == []
    assert "1234567" in result.sanitized_text


def test_validate_query_warns_for_japanese_sql_mutation_intent() -> None:
    """日本語のデータ変更意図も Select AI runsql 用 warning として検出する。"""
    result = GuardrailPolicy().validate_query("rag_documents の古い行を削除してください")

    assert result.allowed is True
    assert [finding.code for finding in result.findings] == ["sql_mutation_intent"]


def test_validate_query_does_not_warn_for_japanese_mutation_word_as_question() -> None:
    """削除件数の確認のような参照クエリは mutation intent として扱わない。"""
    result = GuardrailPolicy().validate_query("削除件数をステータス別に教えてください")

    assert result.allowed is True
    assert result.findings == []


def test_validate_answer_masks_sensitive_identifiers() -> None:
    """回答中の機微な識別子はブロックではなくマスクして warning にする。"""
    result = GuardrailPolicy().validate_answer(
        "振込先の口座番号は 1234567 です。電話番号 03-1234-5678 へ連絡してください。"
    )

    assert result.allowed is True
    assert [finding.code for finding in result.findings] == ["sensitive_identifier_redacted"]
    assert "1234567" not in result.sanitized_text
    assert "03-1234-5678" not in result.sanitized_text
    assert result.sanitized_text.count("[機微情報]") == 2


def test_validate_answer_warns_when_grounding_overlap_is_low() -> None:
    """回答と citation context の重なりが少ない場合は warning を返す。"""
    result = GuardrailPolicy().validate_answer(
        "明日の天気は晴れです。",
        context="[policy.txt#doc-1:0]\n承認条件: 120000 円。クラウド利用料。",
    )

    assert result.allowed is True
    assert [finding.code for finding in result.findings] == ["low_groundedness"]
    assert result.warnings == ["回答と検索根拠の重なりが少ないため、引用を確認してください。"]


def test_regulated_policy_escalates_low_groundedness_to_error() -> None:
    """regulated ポリシーは低根拠を warning ではなく error 扱いにする(監査強調)。"""
    result = GuardrailPolicy(Settings(rag_guardrail_policy="regulated")).validate_answer(
        "明日の天気は晴れです。",
        context="[policy.txt#doc-1:0]\n承認条件: 120000 円。クラウド利用料。",
    )

    findings = [finding for finding in result.findings if finding.code == "low_groundedness"]
    assert findings and findings[0].severity == "error"


def test_validate_answer_accepts_grounded_numeric_answer() -> None:
    """金額や ID が citation context と一致する回答は warning なしで通す。"""
    result = GuardrailPolicy().validate_answer(
        "承認条件は 120000 円です。",
        context="[policy.txt#doc-1:0]\n承認条件: 120000 円。クラウド利用料。",
    )

    assert result.allowed is True
    assert result.findings == []


def test_evaluate_groundedness_returns_score_and_overlap_counts() -> None:
    """評価 runner でも使える groundedness 診断値を返す。"""
    result = evaluate_groundedness(
        "承認条件は 120000 円です。",
        "[policy.txt#doc-1:0]\n承認条件: 120000 円。クラウド利用料。",
    )

    assert result.grounded is True
    assert result.score == 1.0
    assert result.overlap_count >= 1
    assert result.answer_feature_count >= 1
    assert result.high_signal_overlap is True


def test_evaluate_groundedness_fails_unrelated_answer_with_context() -> None:
    """引用と無関係な回答は groundedness gate で検出できる。"""
    result = evaluate_groundedness(
        "明日の天気は晴れです。",
        "[policy.txt#doc-1:0]\n経費申請は部門長の承認が必要です。",
    )

    assert result.grounded is False
    assert result.score == 0.0
    assert result.overlap_count == 0
