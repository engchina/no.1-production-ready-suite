"""既存の品質評価へ渡す比較要求と環境差分を、通信せずに準備する。"""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from app.schemas.evaluation import EvaluationRagOverrides, EvaluationRunRequest

# 実行時の設定経路を持つ項目だけ。文書選択の無効化はbackendから指定できない。
_REQUEST_STAGES = {
    "reference": "reference_expansion_enabled",
    "entity": "entity_expansion_enabled",
    "section": "section_expansion_enabled",
}


class PlanVariant(TypedDict):
    id: str
    request: dict[str, Any]
    environment_overlay: dict[str, str]


class ComparisonPlan(TypedDict):
    schema_version: int
    request_sha256: str
    executed: bool
    requires_effective_settings_snapshot: bool
    unavailable_controls: list[str]
    variants: list[PlanVariant]


def prepare_plan(request: EvaluationRunRequest) -> ComparisonPlan:
    """ケース・範囲・モデル以外の既存入力を保ち、一度に一項目だけ変える。"""
    if request.compare_search_answer_profile_id:
        raise ValueError("別プロファイルとの同時比較を外してから準備してください。")
    baseline = request.model_dump(mode="json", exclude_none=True)
    variants: list[PlanVariant] = [
        {"id": "baseline", "request": baseline, "environment_overlay": {}}
    ]
    for name, key in _REQUEST_STAGES.items():
        overrides = (
            request.rag_overrides.model_dump(exclude_none=True) if request.rag_overrides else {}
        )
        overrides[key] = False
        changed = request.model_copy(update={"rag_overrides": EvaluationRagOverrides(**overrides)})
        variants.append(
            {
                "id": f"without-{name}",
                "request": changed.model_dump(mode="json", exclude_none=True),
                "environment_overlay": {},
            }
        )
    variants.append(
        {
            "id": "with-anchor-document-cap",
            "request": request.model_copy(
                update={
                    "rag_overrides": EvaluationRagOverrides(
                        **{
                            **(
                                request.rag_overrides.model_dump(exclude_none=True)
                                if request.rag_overrides
                                else {}
                            ),
                            "max_anchors_per_document": 5,
                        }
                    )
                }
            ).model_dump(mode="json", exclude_none=True),
            "environment_overlay": {},
        }
    )
    digest = hashlib.sha256(
        json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "request_sha256": digest,
        "executed": False,
        "requires_effective_settings_snapshot": True,
        "unavailable_controls": [],
        "variants": variants,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="既存 EvaluationRunRequest JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    request = EvaluationRunRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    plan = prepare_plan(request)
    # 既存結果や入力を上書きせず、新しい比較のディレクトリだけを作る。
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for variant in plan["variants"]:
        name = variant["id"]
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(variant["request"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / f"{name}.env").write_text(
            "".join(f"{key}={value}\n" for key, value in variant["environment_overlay"].items()),
            encoding="utf-8",
        )
    (args.output_dir / "manifest.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
