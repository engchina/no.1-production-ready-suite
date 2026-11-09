"""回答言語の意味判断を再実行せず、明示指定の原文だけを検証する。"""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from rag_engine.models.llm import DefaultResponseLanguage, ExplicitResponseLanguage, ResponseLanguage




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
    description = "result・reasoning・requirement・text はこの回答言語で書く。引用原文と逐語で示す語句は原文の表記と言語のままにする。"
    return create_model(
        base.__name__, __base__=base, response_language=(contract, Field(default_factory=contract, description=description))
    )
