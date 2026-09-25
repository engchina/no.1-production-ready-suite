"""型付き候補の根拠・不足情報検査。SQL や外部操作は実行しない。"""

from __future__ import annotations

from .ontology_definitions import (
    BusinessDefinition,
    DefinitionFinding,
    DefinitionSource,
    PropertyDefinition,
)


def inspect_definition_quality(
    definitions: list[BusinessDefinition], sources: list[DefinitionSource]
) -> list[DefinitionFinding]:
    findings: list[DefinitionFinding] = []
    for item in definitions:

        def finding(
            code: str,
            field: str,
            message: str,
            *,
            error: bool = False,
            definition_id: str = item.id,
        ) -> None:
            findings.append(
                DefinitionFinding(
                    code=code,
                    definition_id=definition_id,
                    field=field,
                    severity="error" if error else "warning",
                    message_ja=message,
                )
            )

        for evidence in item.evidence:
            evidence.verified = False
            matches = [
                source
                for source in sources
                if source.source_id == evidence.source_id and source.locator == evidence.locator
            ]
            if (
                len(matches) == 1
                and evidence.excerpt_ja.strip()
                and evidence.excerpt_ja in matches[0].text
            ):
                source = matches[0]
                if not evidence.source_sha256 or evidence.source_sha256 == source.sha256:
                    evidence.verified = True
                    evidence.source_sha256 = source.sha256
                    evidence.source_kind = source.kind
            if not evidence.verified:
                finding(
                    "EVIDENCE_UNRESOLVED", "evidence", "証拠の資料・位置・原文を照合できません。"
                )
        if not any(e.verified for e in item.evidence):
            finding(
                "INFERENCE_WITHOUT_EVIDENCE",
                "evidence",
                "根拠を確認できない推論です。業務担当者の確認が必要です。",
            )
        for missing in item.missing_information_ja:
            finding("INFORMATION_REQUIRED", "missing_information_ja", missing)
        if item.kind == "object_type":
            if not item.primary_key or not item.grain_ja:
                finding(
                    "OBJECT_IDENTITY_REQUIRED",
                    "primary_key",
                    "識別子と業務上の粒度を確認してください。",
                    error=True,
                )
        elif isinstance(item, PropertyDefinition):
            if not item.data_type:
                finding(
                    "PROPERTY_TYPE_REQUIRED",
                    "data_type",
                    "プロパティの型が未確定です。",
                    error=True,
                )
        elif item.kind == "link_type" and item.cardinality == "unknown":
            finding("CARDINALITY_REQUIRED", "cardinality", "関係の基数が未確定です。")
        elif item.kind == "metric":
            required = (
                "expression_sql",
                "aggregation",
                "grain",
                "null_policy_ja",
                "unit",
                "time_policy_ja",
            )
            for field in required:
                if not getattr(item, field):
                    finding(
                        "METRIC_SEMANTICS_REQUIRED",
                        field,
                        f"指標の {field} が未確定です。",
                        error=True,
                    )
            if item.additivity == "unknown":
                finding(
                    "METRIC_ADDITIVITY_REQUIRED",
                    "additivity",
                    "指標の加算可能な軸を確認してください。",
                )
        elif item.kind == "function":
            if not item.return_type or not (item.expression_sql or item.implementation_key):
                finding(
                    "CONFIGURATION_REQUIRED",
                    "implementation_key",
                    "戻り値・計算方法または登録済み実装の設定が必要です。",
                )
        elif item.kind == "action_type" and (
            not item.permission_requirement_ja
            or not item.failure_policy_ja
            or not (item.assignments or item.implementation_key)
        ):
            finding(
                "CONFIGURATION_REQUIRED",
                "implementation_key",
                "対象属性・権限・失敗処理の設定が必要です。",
            )
    return findings
