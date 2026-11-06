"""回答言語の意味判断を再実行せず、明示指定の原文だけを検証する。"""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from rag_engine.models.llm import DefaultResponseLanguage, ExplicitResponseLanguage, ResponseLanguage


def response_language_contract(question: str, value: ResponseLanguage) -> dict[str, Any]:
    if value.invalid:
        return {"status": "unconfirmed", "language": None, "instruction": None}
    selection = value.selection
    if selection.kind == "default":
        return {"status": "confirmed", "language": "ja", "instruction": None}
    copied = selection.quote
    if not copied.strip() or copied not in question:
        return {"status": "unconfirmed", "language": None, "instruction": copied}
    return {"status": "confirmed", "language": copied, "instruction": copied}


def generation_language_schema(base: type[BaseModel], question: str) -> type[BaseModel]:
    """引用候補を原質問の全文と空白区切りの連続部分に固定し、言語名を生成させない。"""
    fragments = tuple(dict.fromkeys([question, *question.split()]))
    explicit = create_model(
        "BoundExplicitResponseLanguage",
        __base__=ExplicitResponseLanguage,
        quote=(cast(Any, Literal)[fragments], Field(..., description="原質問の回答言語指定を含む連続原文を選ぶ。")),
    )
    contract = create_model(
        "BoundResponseLanguage",
        __base__=ResponseLanguage,
        selection=(DefaultResponseLanguage | explicit, Field(default_factory=DefaultResponseLanguage, discriminator="kind")),
    )
    return create_model(base.__name__, __base__=base, response_language=(contract, Field(default_factory=contract)))
