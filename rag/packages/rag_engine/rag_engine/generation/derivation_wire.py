"""生成の回答単位を、原文の事実と導出結論へ構造的に分配する。"""

from __future__ import annotations

from typing import Annotated, Any, Self

from pydantic import Field, model_validator

from rag_engine.models.llm import GroundedApplication, GroundedItem, _StrictModel


class DerivationBasis(_StrictModel):
    """結論の text とは別に保持する、導出の依存と方法。"""

    premises: list[str] = Field(default_factory=list, max_length=8, description="使用する質問の事実の連続原文。資料だけで導く場合は空。")
    item_indexes: list[Annotated[int, Field(strict=True, ge=0, le=39)]] = Field(
        min_length=1, max_length=6, description="直接記載の items の0始まりの index。導出同士の参照はしない。"
    )
    reasoning: str = Field(min_length=1, max_length=600, description="依存する事実・規則から結論を導く方法。計算は算式で表す。")


class GenerationItem(GroundedItem):
    """モデルは同じ並びで分類し、導出は変換時に applications だけへ入る。"""

    derivation: DerivationBasis | None = Field(default=None, description="derived だけが持つ依存と導出方法。それ以外は null。")

    @model_validator(mode="after")
    def check_derivation_shape(self) -> Self:
        if self.kind == "derived":
            if self.derivation is None or self.citations:
                raise ValueError("導出には依存と方法が必要です。原文の引用は依存する直接記載に付けます。")
        elif self.derivation is not None:
            raise ValueError("導出方法を持つ結論は derived に分類します。")
        return self


def split_derivations(raw: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    """wire の index を変換し、原文項目と導出の出力先を一意に決める。

    戻り値の位置一覧は、引用単位の private binding を元の項目から引き継ぐために使う。
    意味を語や数式の見かけから分類しない。導出の相互依存は許さず、原文項目への参照だけを変換する。
    """
    rows = raw.get("items", [])
    positions = [index for index, row in enumerate(rows) if row.get("kind") != "derived"]
    direct_indexes = {old: new for new, old in enumerate(positions)}
    result = dict(raw)
    result["items"] = [{key: value for key, value in rows[index].items() if key != "derivation"} for index in positions]
    applications = list(raw.get("applications", []))
    derived_indexes = {}
    for index, row in enumerate(rows):
        if row.get("kind") != "derived":
            continue
        basis = row["derivation"]
        # 範囲外・導出への依存を別の原文へ付け替えず、後段の参照検査で不正にする。
        references = [direct_indexes.get(value, len(positions) + abs(value) + 1) for value in basis["item_indexes"]]
        derived_indexes[index] = len(applications)
        applications.append(
            GroundedApplication(
                request_id=row.get("request_id", ""),
                premises=basis.get("premises", []),
                item_indexes=references,
                reasoning=basis["reasoning"],
                result=row["text"],
            ).model_dump()
        )
    result["applications"] = applications
    result["answer_parts"] = [
        {
            **part,
            "item_indexes": [direct_indexes.get(index, index) for index in part.get("item_indexes", []) if index not in derived_indexes],
            "application_indexes": [derived_indexes[index] for index in part.get("item_indexes", []) if index in derived_indexes],
        }
        for part in raw.get("answer_parts", [])
    ]
    summary_indexes = raw.get("summary_item_indexes", [])
    result["summary_item_indexes"] = list(
        dict.fromkeys(direct_indexes.get(index, index) for index in summary_indexes if index not in derived_indexes)
    )
    result["summary_application_indexes"] = list(
        dict.fromkeys(
            [
                *raw.get("summary_application_indexes", []),
                *(derived_indexes[index] for index in summary_indexes if index in derived_indexes),
            ]
        )
    )
    return result, positions
