"""Markdown のファイル（.md）を作る Control Plane のツール `markdown_create`（#5867）。

- 渡し方: スキルが `control-plane:markdown_create` を宣言した業務 Agent の Run にだけ渡る（業務
  Agent・Run に暗黙に足さない。不変条件 1）。実行は `tool_registry.invoke`（ツール権限・監査）を
  通る。Run の中に成果物を作るだけで外部へ書かないので、既定では承認が要らない（`xlsx_create` と
  同じく副作用なしの宣言。管理者はツール権限で承認・拒否に変えられる）。
- 引数: ファイル名と本文（Markdown の文）だけ。本文はそのまま UTF-8 で保存する。記法の検査・
  整形・描画はしない（記法はスキル `markdown-create` が持つ。どの形式で作るかはモデルが選ぶ）。
- 検査は UTF-8 で表せるか（対になっていないサロゲートを含む文は引数の誤り）と、大きさの上限
  （ほかのファイルと同じ `AGENT_ARTIFACT_FILE_MAX_BYTES`。保存の `artifact_file_store` が見る）
  だけ。1 つの Run で作れるファイルの数は `MAX_FILES_PER_RUN`（形式を問わない）。
- 結果・イベント・ログにファイルの中身を載せない（参照だけを返す）。

ツールの層（`tools.py`）からは import しない（Run の module を読むため）。`tool_registry` への登録は
組み込み Runtime（`builtin_runtime`）がする。
"""

from __future__ import annotations

from typing import Any

from app.features.agent.artifact_file_store import MARKDOWN_MIME_TYPE
from app.features.agent.file_tool_support import (
    FILE_OUTPUT_SCHEMA,
    MAX_FILES_PER_RUN,
    check_file_count,
    invalid_content,
    running_run,
    save_run_file,
)
from app.features.agent.table_columns import SpecIssue
from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext

MARKDOWN_CREATE_TOOL = "markdown_create"
FILE_NAME_MAX_CHARS = 100

MARKDOWN_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": FILE_NAME_MAX_CHARS,
            "description": "ファイル名（拡張子なし。.md が付く）。文書の題にそろえる",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "description": "ファイルの本文（Markdown の文）。そのまま UTF-8 で保存する",
        },
    },
    "required": ["file_name", "content"],
    "additionalProperties": False,
}
MARKDOWN_CREATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"file": FILE_OUTPUT_SCHEMA},
}

MARKDOWN_CREATE_DEFINITION = ToolDefinition(
    name=MARKDOWN_CREATE_TOOL,
    title="Markdown のファイルの作成",
    description=(
        "Markdown のファイル（.md）を新しく作る。利用者が Markdown・.md のファイルでの出力を"
        "求めたときに使う。\n\n"
        "## Step 1: 内容を引数にまとめる\n"
        "ファイル名と、ファイルに書く Markdown の本文を渡す。本文は書いたとおりに保存され、"
        "整形・画像の埋め込み・既存のファイルの編集はしない。\n\n"
        "## Step 2: 結果を確かめる\n"
        "作ったファイルは利用者の画面に表示され、利用者がダウンロードできる。結果の file.file_name "
        f"がファイル名。1 回の実行で作れるファイルは {MAX_FILES_PER_RUN} 個まで。"
    ),
    input_schema=MARKDOWN_CREATE_INPUT_SCHEMA,
    output_schema=MARKDOWN_CREATE_OUTPUT_SCHEMA,
    # Run の成果物を作るだけで、外部へ書かない（`xlsx_create` と同じ宣言。既定は承認なし）。
    side_effects=False,
    timeout_seconds=30.0,
    audit_tags=["output", "file"],
)


def _encoded(content: str) -> bytes:
    """本文の UTF-8 のバイト列（UTF-8 で表せない文字があれば引数の誤り）。"""
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise invalid_content(
            [
                SpecIssue(
                    "content",
                    "encoding",
                    "本文に UTF-8 で表せない文字（対になっていないサロゲート）があります。"
                    "その文字を除いて呼び直してください。",
                )
            ]
        ) from exc


def _create_markdown(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
    run = running_run(context.run_id)
    # 上限はこの module の値を呼ぶたびに読む（形式をまたいで同じ値。`file_tool_support`）。
    check_file_count(run, MAX_FILES_PER_RUN)
    data = _encoded(str(arguments.get("content") or ""))
    reference = save_run_file(
        run.id,
        MAX_FILES_PER_RUN,
        file_name=str(arguments.get("file_name") or ""),
        mime_type=MARKDOWN_MIME_TYPE,
        data=data,
    )
    return {"file": reference}


def markdown_create_tool() -> tuple[ToolDefinition, ToolHandler]:
    """`tool_registry` に登録する `markdown_create`（定義と handler）。"""
    return MARKDOWN_CREATE_DEFINITION, _create_markdown


__all__ = [
    "MARKDOWN_CREATE_DEFINITION",
    "MARKDOWN_CREATE_INPUT_SCHEMA",
    "MARKDOWN_CREATE_TOOL",
    "markdown_create_tool",
]
