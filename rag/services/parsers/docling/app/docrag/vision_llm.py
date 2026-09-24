"""Vision 画像説明の prompt 組み立てと openai SDK 呼び出し(rag_poc adapters/oci から移植)。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rag_parser_core.openai_structured import parse_structured

from app.docrag.llm_models import PictureDescriptionOutput
from app.docrag.prompts import DEFAULT_IMAGE_RETRIEVAL_PROMPT, render_prompt_template
from app.docrag.settings import Settings
from app.docrag.vision_prompt_rules import (
    TABLE_EXTRACTION_INSTRUCTION,
    refine_image_retrieval_prompt,
)

VISION_SYSTEM_PROMPT = "あなたは問い合わせRAG用の画像説明作成担当です。JSONだけを返してください。"


def _image_retrieval_template() -> str:
    """共通規則を合成した Vision 用テンプレート(編集 UI は持たないため既定値のみ)。"""
    return refine_image_retrieval_prompt(DEFAULT_IMAGE_RETRIEVAL_PROMPT).strip()


def _render_prompt(
    metadata: dict[str, Any], *, target_kind: str = "picture", template: str | None = None
) -> str:
    """Vision へ送る prompt を組み立てる。"""
    if target_kind not in {"picture", "table"}:
        raise ValueError(f"Unknown Vision target kind: {target_kind}")
    if template is None:
        template = _image_retrieval_template()
    prompt = render_prompt_template(
        template,
        {"image_metadata": _json_dumps(metadata), "image": _image_prompt_note(metadata)},
    )
    instruction = (
        TABLE_EXTRACTION_INSTRUCTION
        if target_kind == "table"
        else "実行対象: 画像1の独立した画像領域。"
    )
    return prompt + "\n\n" + instruction + "\n\n" + _picture_output_contract()


def describe_picture(
    image_path: Path,
    metadata: dict[str, Any],
    settings: Settings,
    *,
    context_image_paths: Sequence[str | Path] = (),
    target_kind: str = "picture",
    rendered_prompt: str | None = None,
) -> dict[str, Any]:
    """対象 crop と文脈画像から Vision 説明を生成する(openai SDK Responses API)。"""
    if target_kind not in {"picture", "table"}:
        raise ValueError(f"Unknown Vision target kind: {target_kind}")
    prompt = (
        rendered_prompt
        if rendered_prompt is not None
        else _render_prompt(metadata, target_kind=target_kind)
    )
    images = [Path(image_path)]
    images.extend(Path(path) for path in context_image_paths if Path(path).is_file())
    parsed = parse_structured(
        settings.llm,
        system_prompt=VISION_SYSTEM_PROMPT,
        prompt=prompt,
        text_format=PictureDescriptionOutput,
        model=settings.vision_model,
        images=images,
        max_output_tokens=settings.answer_max_tokens,
    )
    return parsed.model_dump(mode="json")


def _picture_output_contract() -> str:
    schema = _compact_schema(PictureDescriptionOutput.model_json_schema())
    return (
        "実行時の構造化出力契約（出力形式は以下を優先。上記の読取・業務説明方針は維持）:\n"
        "required の全キーを返し、該当しない文字列は空文字、配列は空配列にしてください。\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"title", "description", "default"}:
            continue
        if key in {"properties", "$defs"}:
            compact[key] = {name: _compact_schema(item) for name, item in value.items()}
        elif isinstance(value, dict):
            compact[key] = _compact_schema(value)
        elif isinstance(value, list):
            compact[key] = [
                _compact_schema(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            compact[key] = value
    return compact


def _image_prompt_note(metadata: dict[str, Any]) -> str:
    image_inputs = metadata.get("image_inputs")
    if not isinstance(image_inputs, list) or not image_inputs:
        return "画像はこのメッセージに添付されています。"
    lines = [
        "画像はこのメッセージに次の順序で添付されています。",
        "画像1が主対象、画像2以降が周辺文脈です。抽出と帰属は共通規則に従ってください。",
    ]
    for index, image_input in enumerate(image_inputs, start=1):
        if not isinstance(image_input, dict):
            continue
        role = str(image_input.get("role") or f"image_{index}")
        description = str(image_input.get("description") or "").strip()
        bbox = image_input.get("bbox")
        bbox_text = f" bbox={bbox}" if isinstance(bbox, list) and bbox else ""
        if description:
            lines.append(f"画像{index}: {role} - {description}{bbox_text}")
        else:
            lines.append(f"画像{index}: {role}{bbox_text}")
    return "\n".join(lines)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
