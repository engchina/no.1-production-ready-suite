"""回答言語の意味判断を再実行せず、明示指定の原文だけを検証する。"""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model
from rag_parser_core.language import current_pack

from rag_engine.models.llm import DefaultResponseLanguage, ExplicitResponseLanguage, ResponseLanguage

# 指示の文の差し込み口（WeKnora の {{language}} と同じ形）。保存した指示でも使える。
LANGUAGE_PLACEHOLDER = "{{language}}"
# 言語資源の locale の表示名。無い locale は locale の文字のまま渡す。
_LOCALE_LANGUAGE_NAMES = {"ja": "日本語"}


def default_response_language() -> str:
    """回答の既定の言語。回答の見出し・定型文と同じ言語資源の locale から決める。"""
    locale = current_pack().locale
    return _LOCALE_LANGUAGE_NAMES.get(locale, locale)


def render_language_placeholder(prompt: str) -> str:
    """指示の文の差し込み口を、回答の既定の言語で埋める。"""
    return prompt.replace(LANGUAGE_PLACEHOLDER, default_response_language())


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
    description = (
        "モデルが書く文の欄（result・reasoning・requirement・text・external_data_items・reference_materials）はすべてこの回答言語で書く。"
        "引用原文と逐語で示す語句は原文の表記と言語のままにする。"
    )
    return create_model(
        base.__name__, __base__=base, response_language=(contract, Field(default_factory=contract, description=description))
    )
