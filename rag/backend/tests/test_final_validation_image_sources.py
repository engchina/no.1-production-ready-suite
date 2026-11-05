"""正式producer・保存・ACL読回を経て最終検証へ用途を届ける。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from rag_engine.chunking import builder
from rag_engine.chunking.metadata import _project_chunk_metadata

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.answer_validation import EvidenceRef, validate_answer
from app.rag.chunking_small_to_big import _backend_chunk
from tests.test_image_text_source_storage import SyntheticTransaction, save_synthetic

CASES: list[list[tuple[str, str, str | None]]] = [
    [("Picture", "実行ボタンで確認します。表示値は900です。", "example")],
    [("Picture", "記録値は42です。", "record")],
    [("Picture", "用途未記録の値は17です。", "unknown")],
    [
        ("Text", "本文の指定値は8です。", None),
        ("Picture", "記録値は42です。", "record"),
        ("Picture", "例示値は900です。", "example"),
    ],
]


def chunks_for(parts: list[tuple[str, str, str | None]]) -> list[Any]:
    children = []
    for index, (category, text, purpose) in enumerate(parts, 1):
        record = dict(
            id=f"synthetic-element-{index}",
            engine="docling",
            page=1,
            seq_no=index,
            category=category,
            raw_type=category.lower(),
            text=text,
            raw={"vision_image_purpose": purpose} if purpose else {},
        )
        child = builder._child_chunk(
            index,
            [record],
            source_run_id="abcdef0123456789",
            source_file_name="synthetic.pdf",
            source_engine_id="docling",
            source_engine_label="Docling",
            metadata_by_page={},
            section_path=(),
            atomic=True,
        )
        child.parent_chunk_id = "synthetic-parent"
        child.metadata = _project_chunk_metadata(child.metadata)
        children.append(child)
    parent = builder._parent_chunk(
        "synthetic-parent",
        1,
        children,
        source_run_id="abcdef0123456789",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
    )
    return [_backend_chunk(child, i, parent, {}) for i, child in enumerate(children)]


@pytest.mark.parametrize("parts", CASES)
async def test_saved_scoped_images_reach_final_validation(
    monkeypatch: pytest.MonkeyPatch, parts: list[tuple[str, str, str | None]]
) -> None:
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks_for(parts))
    client = OracleClient()

    async def fetch_one(statement: str, binds: Any, **_kw: Any) -> Any:
        row = next(row for row in tx.rows if row["chunk_id"] == binds["read_chunk_id"])
        return {**row, **tx.scope, "file_name": "synthetic.pdf"}

    monkeypatch.setattr(client, "_fetch_one", fetch_one)
    captured: list[dict[str, Any]] = []

    def fake_claims(
        _question: str, _answer: str, items: list[dict[str, Any]], _settings: Any, **_options: Any
    ) -> dict[str, Any]:
        captured.extend(items)
        return {"status": "completed", "counts": {"supported": 1}, "claim_checks": []}

    import rag_engine.evaluation.answer_validation as engine

    import app.rag.answer_validation as backend

    monkeypatch.setattr(engine, "validate_answer_claims", fake_claims)
    monkeypatch.setattr(backend, "build_engine_settings", lambda *_a, **_kw: None)

    async def overrides() -> dict[str, Any]:
        return {}

    monkeypatch.setattr(client, "answer_prompt_overrides", overrides)
    refs = [EvidenceRef(str(tx.scope["document_id"]), row["chunk_id"]) for row in tx.rows]
    await validate_answer(
        "合成の資料を説明してください。", "合成の回答。", refs, get_settings(), oracle=client
    )
    assert len(captured) == len(parts)
    for part, item in zip(parts, captured, strict=True):
        assert item["text"] == part[1]
        context = item.get("image_text_source_context")
        if part[2]:
            assert context is not None
            assert {r["vision_image_purpose"] for r in context["ranges"]} == {part[2]}
            assert context["scope"] == {
                k: tx.scope[k] for k in ("document_id", "recipe_id", "chunk_set_id")
            }
        else:
            assert context is None


@pytest.mark.parametrize(
    "change", ["scope", "hash", "missing_scope", "missing_recipe", "missing_recipe_column"]
)
async def test_invalid_saved_scope_never_promotes_record(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks_for(CASES[1]))
    row = {**json.loads(json.dumps(tx.rows[0])), **tx.scope, "file_name": "synthetic.pdf"}
    metadata = json.loads(row["metadata_json"])
    if change == "scope":
        value = json.loads(metadata["image_text_source_scope_json"])
        value["recipe_id"] = "other-recipe"
        metadata["image_text_source_scope_json"] = json.dumps(value)
    elif change == "hash":
        row["chunk_text"] += "原文変更"
    elif change == "missing_scope":
        metadata.pop("image_text_source_scope_json")
    elif change == "missing_recipe":
        row["recipe_id"] = None
    else:
        row.pop("recipe_id")
    # 保存済みの単一fieldや偽の一時投影からは認可しない。
    metadata["vision_image_purpose"] = "record"
    metadata["answer_image_text_source_map"] = {"version": 1, "scope": tx.scope}
    row["metadata_json"] = json.dumps(metadata)
    client = OracleClient()

    async def fetch_one(*_args: Any, **_kwargs: Any) -> Any:
        return row

    monkeypatch.setattr(client, "_fetch_one", fetch_one)
    hit = await client.retrievable_chunk(str(tx.scope["document_id"]), row["chunk_id"])
    assert hit is not None
    from app.rag.answer_validation import validation_image_context

    assert validation_image_context(hit) == {}


def test_validation_reuse_digest_includes_scoped_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    from copy import deepcopy
    from types import SimpleNamespace

    import app.rag.answer_validation as backend

    monkeypatch.setattr(
        backend, "enterprise_ai_default_model_id", lambda _settings: "synthetic-model"
    )
    monkeypatch.setattr(
        backend,
        "enterprise_ai_connection_for_model",
        lambda *_a: SimpleNamespace(
            connection_id="synthetic",
            base_url="https://synthetic.invalid",
            project_ocid="synthetic",
        ),
    )
    monkeypatch.setattr(backend, "enterprise_ai_reasoning_effort_to_send", lambda *_a: "low")
    item: dict[str, Any] = {
        "id": "synthetic-chunk",
        "text": "同じ原文",
        "image_text_source_context": {
            "scope": {"recipe_id": "synthetic-recipe"},
            "ranges": [{"vision_image_purpose": "record"}],
        },
    }

    def digest(value: dict[str, Any]) -> str:
        return backend.validation_reuse_context(
            "合成の質問",
            "合成の回答",
            get_settings(),
            prompt_version="synthetic-version",
            evidence=[value],
            premises=[],
            data_results=[],
            guide=None,
            requests=None,
            gaps=[],
        )

    original = digest(item)
    changed = deepcopy(item)
    changed["image_text_source_context"]["ranges"][0]["vision_image_purpose"] = "example"
    assert digest(changed) != original
    changed = deepcopy(item)
    changed.pop("image_text_source_context")
    assert digest(changed) != original
