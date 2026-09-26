"""DocRAG(rag_poc)のプロンプトの編集と反映。

rag_poc はプロンプトをファイル(`prompts/<key>.txt`)へ保存したが、rag では DB
(`rag_docrag_prompts`)に保存し、docrag の runtime の ``prompt_overrides`` で渡す。
編集できるのは rag_poc と同じ 2 つ(回答生成のテンプレートと画像検索のプロンプト)で、
ほかの段はコードで管理する読み取り専用。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from docrag.knowledge.prompt_files import (
    IMAGE_RETRIEVAL_PROMPT_KEY,
    PROMPT_FILES,
    VLM_ANSWER_PROMPT_KEY,
)

EDITABLE_PROMPT_KEYS = (VLM_ANSWER_PROMPT_KEY, IMAGE_RETRIEVAL_PROMPT_KEY)
DOCRAG_PROMPT_MAX_CHARS = 50_000


def default_prompt(key: str) -> str:
    return PROMPT_FILES[key].default_content or ""


def required_placeholders(key: str) -> tuple[str, ...]:
    return PROMPT_FILES[key].required_placeholders


def validate_prompt(key: str, content: str) -> None:
    """rag_poc の save_prompt_file と同じ検証(空と必須 placeholder の欠落は保存しない)。"""
    if key not in EDITABLE_PROMPT_KEYS:
        raise KeyError(key)
    if not content.strip():
        raise ValueError("内容が空です。既定の内容を使う場合は「既定に戻す」を使ってください。")
    missing = [
        f"{{{{{name}}}}}" for name in required_placeholders(key) if f"{{{{{name}}}}}" not in content
    ]
    if missing:
        raise ValueError(f"必須の placeholder がありません: {', '.join(missing)}")


def readonly_prompt_stages() -> list[dict[str, object]]:
    """回答フローの各段のプロンプト(コードで管理し、画面では読み取り専用で示す)。"""
    from docrag.evaluation.answer_eval import SCOPE_PROMPT, SYSTEM_PROMPT
    from docrag.generation.answering import (
        CRAG_GRADER_SYSTEM_PROMPT,
        QUERY_EXPANSION_SYSTEM_PROMPT,
        QUERY_ROUTING_SYSTEM_PROMPT,
    )
    from docrag.generation.crag_support import CRAG_GRADE_PROMPT_TEMPLATE
    from docrag.generation.grounded import AUDIT_SYSTEM_PROMPT, GENERATE_SYSTEM_PROMPT
    from docrag.generation.query_prompts import (
        QUERY_EXPANSION_PROMPT_TEMPLATE,
        QUERY_ROUTING_PROMPT_TEMPLATE,
    )

    def stage(stage_id: str, *prompts: tuple[str, str]) -> dict[str, object]:
        return {
            "id": stage_id,
            "prompts": [{"id": prompt_id, "content": content} for prompt_id, content in prompts],
        }

    return [
        stage(
            "routing",
            ("system", QUERY_ROUTING_SYSTEM_PROMPT),
            ("template", QUERY_ROUTING_PROMPT_TEMPLATE),
        ),
        stage(
            "expansion",
            ("system", QUERY_EXPANSION_SYSTEM_PROMPT),
            ("template", QUERY_EXPANSION_PROMPT_TEMPLATE),
        ),
        stage(
            "crag",
            ("system", CRAG_GRADER_SYSTEM_PROMPT),
            ("template", CRAG_GRADE_PROMPT_TEMPLATE),
        ),
        stage("generation", ("system", GENERATE_SYSTEM_PROMPT)),
        stage("audit", ("system", AUDIT_SYSTEM_PROMPT)),
        stage("evaluation", ("scope", SCOPE_PROMPT), ("system", SYSTEM_PROMPT)),
    ]


@contextmanager
def prompt_overrides(overrides: Mapping[str, str]) -> Iterator[None]:
    """docrag の read_prompt が overrides を返すように、同期処理の間だけ runtime を有効にする。

    profile は今の current_profile()(runtime なしの既定)に上書きだけを足し、ほかの挙動を変えない。
    保存先(paths.prompts)を持たないので、read_prompt はファイルを読まず上書きか既定値を返す。
    """
    if not overrides:
        yield
        return
    from docrag.resources.runtime import ResourcePaths, Runtime, current_profile

    profile = current_profile()
    merged = {**dict(profile.prompt_overrides), **dict(overrides)}
    with tempfile.TemporaryDirectory(prefix="docrag-prompts-") as work:
        runtime = Runtime(
            ResourcePaths(workspace=Path(work), output=Path(work)),
            replace(profile, prompt_overrides=tuple(merged.items())),
        )
        with runtime.activate():
            yield
