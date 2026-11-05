"""決定論スタブの既知の根拠選択を、最終検証の単位wireとして作る。"""

from typing import Any


def wire_text(item: dict[str, Any]) -> str:
    """スタブの入力観察用。モデルへはunitsだけが送信される。"""
    return "\n".join(row["text"] for row in item["units"]) if "units" in item else item["text"]


def unit_audit_output(schema, payload, values):
    by_id = {item["evidence_id"]: item for item in payload["evidence_items"]}
    # テストの選択元指定は完全なsource IDも使える。モデル応答にはunit IDだけを作る。
    for item in payload["evidence_items"]:
        by_id.setdefault(item["id"], item)
    claims = []
    for original in values["claim_checks"]:
        fields = {
            key: value for key, value in original.items() if key in schema.model_fields["claim_checks"].annotation.__args__[0].model_fields
        }
        # fixtureが明示した実片段/完全sourceだけを用いる。表示名や省略IDを推定しない。
        tokens = [token.strip() for token in original.get("evidence_id", "").split(",") if token.strip()]
        fields["evidence_unit_ids"] = [
            unit["unit_id"] for token in tokens for unit in by_id.get(token, {}).get("units", [{"unit_id": "invalid-unit:" + token}])
        ]
        claims.append(fields)
    return schema.model_validate({"claim_checks": claims})
