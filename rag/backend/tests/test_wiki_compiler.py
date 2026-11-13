"""合成の原文で、切断からの再開・原文の隔離・依存と開閉を確認する。"""

import json
from typing import Any

import pytest
from pr_backend_core.observability.model_purpose import model_purpose_attributes
from pydantic import ValidationError
from rag_parser_core.oci_enterprise_ai import EnterpriseAiIncompleteResponseError

from app.rag.wiki_compiler import build_input, compile_pages
from app.rag.wiki_models import (
    WikiContext,
    WikiDependency,
    WikiPage,
    WikiUnit,
    context_schema,
    dependency_units,
    fingerprint,
    wiki_available,
)


def units() -> list[WikiUnit]:
    return [
        WikiUnit(
            unit_id=f"unit-{index}",
            document_id="document-a",
            chunk_set_id="version-a",
            source_chunk_ids=[f"chunk-{index}"],
            section_id="section-a",
            section_path=["架空の操作"],
            title=f"架空の項目 {index}",
            text=text,
            source_hash=fingerprint(text),
        )
        for index, text in enumerate(
            [
                "前提の記録を確認する。",
                "処理前に保存する。",
                "処理を開始する。",
                "完了表示を確認する。",
            ]
        )
    ]


class Model:
    def __init__(self, *, clip: bool = False, invalid: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.clip, self.invalid = clip, invalid

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        assert model_purpose_attributes() == {"pr.gen_ai.purpose": "document.extract"}
        data = json.loads(context)
        self.calls.append(data)
        targets = data["target_unit_ids"]
        if self.clip and len(targets) > 1:
            raise EnterpriseAiIncompleteResponseError("合成の出力上限")
        return json.dumps(
            {
                "items": [
                    {
                        "unit_id": uid,
                        "context": "架空の操作の案内。",
                        "possible_questions": ["開始前に何を確認するか。"],
                        "dependencies": [
                            {
                                "unit_id": "outside" if self.invalid else "unit-1",
                                "relation": "warning",
                            }
                        ],
                        "unresolved": [],
                    }
                    for uid in targets
                ]
            }
        )


@pytest.mark.asyncio
async def test_split_and_resume_reuses_completed_units_and_keeps_original_text() -> None:
    source, model = units(), Model(clip=True)
    checkpoints: list[WikiPage] = []
    checks: list[bool] = []

    async def authorize() -> None:
        checks.append(True)

    async def save(pages: Any) -> None:
        checkpoints.extend(pages)

    result = await compile_pages(
        source,
        model=model,
        model_id="model-a",
        max_input_chars=2000,
        batch_size=4,
        authorize=authorize,
        checkpoint=save,
    )
    assert len(result) == 4 and len(model.calls) == 7
    assert [page.unit.text for page in result] == [unit.text for unit in source]
    assert all(page.navigation.context != page.unit.text for page in result)
    resumed = Model()
    again = await compile_pages(
        source,
        model=resumed,
        model_id="model-a",
        max_input_chars=2000,
        batch_size=4,
        saved=checkpoints,
        authorize=authorize,
        checkpoint=save,
    )
    assert again == result and not resumed.calls
    assert len(checks) > len(model.calls)


@pytest.mark.asyncio
async def test_invalid_dependency_is_not_checkpointed() -> None:
    saved: list[WikiPage] = []

    async def authorize() -> None:
        pass

    async def save(pages: Any) -> None:
        saved.extend(pages)

    with pytest.raises(ValidationError):
        await compile_pages(
            units(),
            model=Model(invalid=True),
            model_id="model-a",
            max_input_chars=2000,
            batch_size=4,
            authorize=authorize,
            checkpoint=save,
        )
    assert not saved


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["json", "dependency"])
async def test_invalid_large_output_is_recovered_without_accepting_invalid_ids(
    failure: str,
) -> None:
    class LargeInvalidModel(Model):
        async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
            large = len(json.loads(context)["target_unit_ids"]) > 1
            self.invalid = large and failure == "dependency"
            raw = await super().generate(prompt, context, **kwargs)
            return "invalid-json" if large and failure == "json" else raw

    model, saved = LargeInvalidModel(), []

    async def authorize() -> None:
        pass

    async def save(pages: Any) -> None:
        saved.extend(pages)

    result = await compile_pages(
        units(),
        model=model,
        model_id="synthetic",
        max_input_chars=2000,
        batch_size=4,
        authorize=authorize,
        checkpoint=save,
    )
    assert len(result) == len(saved) == 4
    assert len(model.calls) == 7
    assert all(
        dependency.unit_id == "unit-1"
        for page in result
        for dependency in page.navigation.dependencies
    )


@pytest.mark.asyncio
async def test_authorization_change_after_generation_prevents_checkpoint() -> None:
    calls, saved = 0, []

    async def authorize() -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise PermissionError("合成の権限失効")

    async def save(pages: Any) -> None:
        saved.extend(pages)

    with pytest.raises(PermissionError):
        await compile_pages(
            units(),
            model=Model(),
            model_id="model-a",
            max_input_chars=2000,
            batch_size=4,
            authorize=authorize,
            checkpoint=save,
        )
    assert not saved


def test_schema_rejects_missing_and_duplicate_targets() -> None:
    schema = context_schema(["unit-0", "unit-1"], ["unit-0", "unit-1"])
    item = {
        "unit_id": "unit-0",
        "context": "案内",
        "possible_questions": [],
        "dependencies": [],
        "unresolved": [],
    }
    for values in ([item], [item, item]):
        with pytest.raises(ValidationError):
            schema.model_validate({"items": values})


def test_partial_input_never_slices_a_source_unit() -> None:
    source = units()
    data = build_input(source, [source[-1]], max_chars=len(source[-1].text))
    assert data["document_complete"] is False
    assert data["original_units"] == [[source[-1].unit_id, source[-1].text]]
    assert len(data["outline"]) == len(source)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_changed_navigation_schema_does_not_reuse_an_old_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import wiki_compiler

    model, saved = Model(), []

    async def authorize() -> None:
        return None

    async def save(pages: Any) -> None:
        saved.extend(pages)

    options: dict[str, Any] = dict(
        model=model,
        model_id="model-a",
        max_input_chars=2000,
        batch_size=4,
        authorize=authorize,
        checkpoint=save,
    )
    first = await compile_pages(units(), **options)
    monkeypatch.setattr(wiki_compiler, "WIKI_SCHEMA_VERSION", "synthetic-next")
    second = await compile_pages(units(), saved=first, **options)
    assert len(model.calls) == 2
    assert first[0].input_fingerprint != second[0].input_fingerprint


def test_dependency_cycles_and_unavailable_sources_are_distinct() -> None:
    source = units()
    pages = {
        unit.unit_id: WikiPage(
            unit=unit,
            navigation=WikiContext(
                unit_id=unit.unit_id,
                context="案内",
                possible_questions=[],
                dependencies=[
                    WikiDependency(unit_id="unit-1", relation="warning"),
                    WikiDependency(unit_id="missing", relation="reference"),
                ],
                unresolved=[],
            ),
            input_fingerprint="input",
        )
        for unit in source
    }
    selected, blocked = dependency_units(["unit-0"], pages, set(pages))
    assert selected == ["unit-0", "unit-1"] and blocked == ["missing"]


@pytest.mark.parametrize("kb", [False, True])
@pytest.mark.parametrize("profile", [False, True])
def test_two_switches_and_current_version_are_required(kb: bool, profile: bool) -> None:
    kwargs: dict[str, Any] = dict(
        profile_enabled=profile,
        requested_scope=["kb-a"],
        memberships=["kb-a", "kb-b"],
        preferences={"kb-a": kb, "kb-b": True},
    )
    assert wiki_available(**kwargs, source_current=True) == (kb and profile)
    assert not wiki_available(**kwargs, source_current=False)
