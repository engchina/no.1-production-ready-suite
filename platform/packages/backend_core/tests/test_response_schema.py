"""応答 schema の項目の説明を、指示の文の Step にして送る（#5230・#5238）。"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from pr_backend_core.prompt_steps import continue_prompt_steps
from pr_backend_core.response_schema import (
    FIELD_GUIDE_LEAD,
    schema_field_guide,
    with_schema_field_guide,
)


class _Child(BaseModel):
    """型の説明（実装のメモ）。"""

    kind: Literal["k1", "k2"] = Field(description="k1=合成の種類 1、k2=合成の種類 2。")
    description: str = Field(description="子の項目の説明。")


class _Probe(BaseModel):
    """根の型の説明（実装のメモ）。"""

    note: str = Field(description="この欄には必ず「ZEBRA-7」とだけ書く。")
    color: Literal["c1", "c2", "c3"] = Field(description="c1=赤、c2=緑、c3=青。")
    plain: str
    first: list[_Child] = Field(description="最初の子の並び。")
    second: _Child | None = None


def _descriptions(node: object) -> list[str]:
    """schema の node に付いた ``description``（項目名の ``description`` は除く）。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            elif key in {"properties", "$defs"}:
                for child in value.values():
                    found.extend(_descriptions(child))
            else:
                found.extend(_descriptions(value))
    elif isinstance(node, list):
        for child in node:
            found.extend(_descriptions(child))
    return found


def test_guide_is_generated_from_schema_in_field_order_without_repeats() -> None:
    schema = _Probe.model_json_schema()
    guide = schema_field_guide(schema)
    assert guide == [
        (("`note`",), "この欄には必ず「ZEBRA-7」とだけ書く。"),
        (("`color`",), "c1=赤、c2=緑、c3=青。"),
        (("`first`",), "最初の子の並び。"),
        # 同じ定義を 2 つの項目が使っても、説明は 1 行にまとめて位置を並べる。
        (("`first[].kind`", "`second.kind`"), "k1=合成の種類 1、k2=合成の種類 2。"),
        (("`first[].description`", "`second.description`"), "子の項目の説明。"),
    ]
    # 型の説明（docstring）は項目の説明ではない。
    assert all("型の説明" not in description for _, description in guide)
    # 同じ入力から同じ結果（順序が安定）。
    assert schema_field_guide(json.loads(json.dumps(schema))) == guide


def test_descriptions_move_from_schema_to_instruction_step_once() -> None:
    schema = _Probe.model_json_schema()
    instructions = (
        "## Step 1: 読む\n合成の指示。\n\n## Step 2: 結果を返す\n応答 schema どおりに返す。\n"
    )
    guided, stripped = with_schema_field_guide(instructions, schema)

    assert guided.startswith(
        instructions.rstrip() + "\n\n## Step 3: 応答の各項目を書く\n" + FIELD_GUIDE_LEAD + "\n"
    )
    assert "- `note`: この欄には必ず「ZEBRA-7」とだけ書く。" in guided
    assert "- `first[].kind`、`second.kind`: k1=合成の種類 1、k2=合成の種類 2。" in guided
    for _, description in schema_field_guide(schema):
        assert guided.count(description) == 1
    # schema には説明を残さず（型の説明も外す）、項目の名前・型・制約は変えない。
    assert _descriptions(stripped) == []
    assert "description" in stripped["$defs"]["_Child"]["properties"]
    assert stripped["properties"]["color"]["enum"] == ["c1", "c2", "c3"]
    assert stripped["required"] == schema["required"]
    # 元の schema は書き換えない。
    assert schema["properties"]["note"]["description"]


def test_schema_without_field_descriptions_keeps_instructions() -> None:
    class _Bare(BaseModel):
        """型の説明だけ。"""

        value: str

    guided, stripped = with_schema_field_guide("合成の指示。", _Bare.model_json_schema())
    assert guided == "合成の指示。"
    assert _descriptions(stripped) == []


def test_empty_instructions_get_only_the_step() -> None:
    guided, _ = with_schema_field_guide("", _Probe.model_json_schema())
    assert guided.startswith("## Step 1: 応答の各項目を書く\n")


def test_recursive_schema_is_walked_once() -> None:
    class _Node(BaseModel):
        label: str = Field(description="節の名前。")
        children: list["_Node"] = Field(default_factory=list, description="子の節。")

    guide = schema_field_guide(_Node.model_json_schema())
    assert guide == [(("`label`",), "節の名前。"), (("`children`",), "子の節。")]


def test_discriminated_choices_name_the_value_each_description_belongs_to() -> None:
    class _Off(BaseModel):
        kind: Literal["off"] = Field(description="指定が無い。")

    class _On(BaseModel):
        kind: Literal["on"] = Field(description="指定がある。")
        quote: str = Field(description="指定の語句。")

    class _Holder(BaseModel):
        choice: _Off | _On = Field(discriminator="kind", description="指定の有無。")

    assert schema_field_guide(_Holder.model_json_schema()) == [
        (("`choice`",), "指定の有無。"),
        (('`choice.kind` = "off"',), "指定が無い。"),
        (('`choice.kind` = "on"',), "指定がある。"),
        (("`choice.quote`",), "指定の語句。"),
    ]


def test_continued_steps_follow_the_last_step_of_the_preceding_text() -> None:
    preceding = "## Step 1: 読む\n合成の指示。\n\n## Step 2: 返す\n合成の指示。"
    assert continue_prompt_steps(preceding, "## Step 9: 足す\n## Step 10: 終える") == (
        "## Step 3: 足す\n## Step 4: 終える"
    )
    assert continue_prompt_steps("Step の無い指示。", "## Step 5: 足す") == "## Step 1: 足す"
