"""呼び出し元が選んだルール（pinned。業務ガイド #1238）は語の一致に関係なく使い、手がかりを検索文に足す。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from rag_engine.knowledge.runtime_knowledge import (
    PINNED_RULE_TAG,
    RUNTIME_KNOWLEDGE_FILE_NAME,
    build_runtime_knowledge_context,
)


def test_pinned_rule_is_always_matched_first_and_adds_its_triggers() -> None:
    payload = {
        "schema_version": 1,
        "terms": [],
        "rules": [
            {"id": "r1", "title": "取消の注意", "triggers": ["取消"], "content": "取消後は戻せません。",
             "status": "approved"},
            {"id": "guide-g1", "title": "権限の付与", "triggers": ["権限タブ"],
             "content": "手順 1. 権限タブを開く", "status": "approved",
             "tags": [PINNED_RULE_TAG, "support_guide"]},
        ],
    }
    with TemporaryDirectory() as work:
        output_dir = Path(work)
        (output_dir / RUNTIME_KNOWLEDGE_FILE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        context = build_runtime_knowledge_context("取消したい", output_dir)
    assert [rule.rule_id for rule in context.matched_rules] == ["guide-g1", "r1"]
    assert "権限タブ" in context.expanded_question
    # pinned の題名は質問に無いので検索文に足さない（手がかりだけ）。
    assert "権限の付与" not in context.expanded_question
