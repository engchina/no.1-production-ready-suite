"""設定由来用途の解析閉ループ。実設定/DB/モデルは使用しない。"""

import inspect
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from rag_engine.parsing.vision_checkpoints import VisionCheckpoints
from rag_parser_core.image_purpose import ImagePurpose

from app.config import Settings
from app.rag.ingestion import IngestionPipeline
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig, resolve_effective_settings
from app.rag.variant_keys import compute_extraction_recipe_id
from app.schemas.extraction import DocumentElement, StructuredExtraction


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*a: Any, **kw: Any) -> None:
        raise AssertionError("API0_network_forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def settings(purpose: str = "record") -> Settings:
    # model_constructは環境/永続設定sourceを読まない。値は全て合成。
    return Settings.model_construct(
        rag_vision_enabled=False, rag_vision_image_purpose=cast(ImagePurpose, purpose)
    )


def pipeline(cfg: Settings, oracle: Any = None) -> IngestionPipeline:
    unused: Any = object()
    return IngestionPipeline(
        settings=cfg,
        vlm=unused,
        genai=unused,
        oracle=oracle or unused,
        object_storage=unused,
        document_understanding=unused,
        recipe_id="synthetic-recipe-a",
        recipe_revision=3,
    )


def test_global_default_is_real_supported_setting() -> None:
    field = Settings.model_fields.get("rag_vision_image_purpose")
    assert field is not None and field.default == "record"


@pytest.mark.parametrize(
    "global_purpose,override,expected",
    [
        ("record", None, "record"),
        ("record", "example", "example"),
        ("example", None, "example"),
        ("example", "record", "record"),
    ],
)
def test_formal_recipe_resolution(global_purpose: str, override: str | None, expected: str) -> None:
    config = KnowledgeBaseAdapterConfig.model_validate(
        {"ingestion": {"vision_image_purpose": override}}
    )
    actual = resolve_effective_settings(settings(global_purpose), config, scope="ingestion")
    assert actual.rag_vision_image_purpose == expected


def test_real_extraction_key_separates_purpose() -> None:
    assert compute_extraction_recipe_id(
        "a" * 64, settings("record")
    ) != compute_extraction_recipe_id("a" * 64, settings("example"))


def test_pipeline_snapshots_settings_at_start() -> None:
    cfg = settings("record")
    p = pipeline(cfg)
    cfg.rag_vision_enabled = True
    assert p._settings.rag_vision_enabled is False


@pytest.mark.asyncio
async def test_formal_save_readback_keeps_parse_purpose() -> None:
    class Store:
        async def upsert_document_extraction_artifact(self, **kwargs: Any) -> None:
            self.saved = json.loads(json.dumps(kwargs))

        async def update_document_recipe_status(self, **kwargs: Any) -> None:
            self.pointer = kwargs

    store = Store()
    p = pipeline(settings("record"), store)
    source = StructuredExtraction(
        elements=[DocumentElement(element_id="native-a", kind="text", text="合成本文")]
    )
    stamped = await p._attach_vision(
        "synthetic-trace",
        source,
        source_bytes=b"",
        content_type="application/pdf",
        file_name="synthetic.pdf",
        parser_backend="docling",
        cancel_checker=None,
    )
    eid = await p._persist_extraction_layer(
        "synthetic-document-a", cast(Any, SimpleNamespace(content_sha256="a" * 64)), stamped
    )
    loaded = StructuredExtraction.model_validate(store.saved["extraction"])
    assert loaded.parser_artifacts.get("vision_image_purpose") == "record"
    assert store.pointer["active_extraction_recipe_id"] == eid
    assert "vision_image_purpose" not in source.parser_artifacts
    assert loaded.elements[0].metadata == {}


def test_checkpoint_separates_purpose_and_reuses_same_purpose(tmp_path: Path) -> None:
    image = tmp_path / "crop.png"
    image.write_bytes(b"synthetic")
    provider = SimpleNamespace(
        provider_id="test", model="test", region="test", base_url="test", project_id="test"
    )
    keys = []
    for purpose in ["record", "record", "example"]:
        kwargs: dict[str, Any] = (
            {"image_purpose": purpose}
            if "image_purpose" in inspect.signature(VisionCheckpoints).parameters
            else {}
        )
        cp = VisionCheckpoints(tmp_path, "a" * 64, **kwargs)
        keys.append(
            cp.key(
                crop=image,
                context=image,
                prompt="合成",
                system_prompt="合成",
                provider=provider,
                max_tokens=100,
                kind="picture",
                api_mode="responses",
            )
        )
    assert keys[0] == keys[1] and keys[0] != keys[2]


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_actual_crop_prompt_cached_response_and_writeback(purpose: str, tmp_path: Path) -> None:
    from PIL import Image
    from rag_engine.models.layout import LayoutRecord, PageImage
    from rag_engine.parsing.picture_descriptions import describe_layout_pictures
    from rag_parser_core.image_purpose import ImagePurpose

    from app.rag.vision import _Layout, _write_back_docling

    class Describer:
        api_mode = "synthetic"
        max_tokens = 100
        calls: list[dict[str, Any]]

        def __init__(self) -> None:
            self.calls = []

        def provider(self) -> Any:
            return SimpleNamespace(
                provider_id="synthetic", model="synthetic", region="", base_url="", project_id=""
            )

        def describe(
            self, crop_path: Path, metadata: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            assert crop_path.is_file() and len(kwargs["context_image_paths"]) == 1
            self.calls.append({"metadata": metadata, **kwargs})
            return {
                "retrieval_text": "合成画像の値42",
                "visible_values": ["42"],
                "vision_image_purpose": "forged",
            }

    image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    page = PageImage(
        page=1, width=100, height=100, pdf_width=100, pdf_height=100, image_path=str(image)
    )
    describer = Describer()

    def record() -> LayoutRecord:
        return LayoutRecord(
            id="picture-a",
            engine="docling",
            page=1,
            seq_no=0,
            bbox=[10, 10, 80, 80],
            coord_system="image_top_left",
            page_width=100,
            page_height=100,
            category="Picture",
            raw_type="picture",
        )

    def run(value: str) -> LayoutRecord:
        item = record()
        result = describe_layout_pictures(
            [item],
            [page],
            run_dir=tmp_path,
            pdf_name="synthetic.pdf",
            describer=describer,
            prompt_template="## Step 1: 読み取り\n{image_metadata}",
            image_purpose=cast(ImagePurpose, value),
        )
        assert (result.targets, result.succeeded, result.failed) == (1, 1, 0)
        assert item.raw["vision_image_purpose"] == value
        return item

    actual = run(purpose)
    assert len(describer.calls) == 1
    call = describer.calls[0]
    assert call["metadata"]["vision_image_purpose"] == purpose
    assert "metadata.vision_image_purpose" in call["rendered_prompt"]
    assert "画面の値の扱い" in call["rendered_prompt"]
    assert "原則として文書の例示" not in call["rendered_prompt"]
    assert "42" in actual.text
    run(purpose)
    assert len(describer.calls) == 1  # raw checkpointを実load、再呼出し無し。
    other = "example" if purpose == "record" else "record"
    run(other)
    assert len(describer.calls) == 2
    updated = _write_back_docling(StructuredExtraction(), _Layout(records=[actual]))
    assert updated.elements and updated.elements[0].metadata["vision_image_purpose"] == purpose
    saved = StructuredExtraction.model_validate_json(updated.model_dump_json())
    assert saved.elements[0].metadata["vision_image_purpose"] == purpose


def test_native_table_and_legacy_unknown_are_not_upgraded() -> None:
    from rag_parser_core.image_purpose import recorded_image_purpose

    from app.rag.vision import stamp_image_purpose

    source = StructuredExtraction(
        elements=[
            DocumentElement(
                element_id="table-a",
                kind="table",
                text="<table><tr><td>42</td></tr></table>",
                metadata={"vision_image_purpose": "forged"},
            ),
            DocumentElement(element_id="text-a", kind="text", text="合成の本文"),
        ]
    )
    stamped = stamp_image_purpose(source, "example")
    assert all("vision_image_purpose" not in e.metadata for e in stamped.elements)
    assert recorded_image_purpose({}) == "unknown"
    assert source.elements[0].metadata["vision_image_purpose"] == "forged"


@pytest.mark.parametrize("rules_source", ["builtin", "saved", "legacy"])
@pytest.mark.parametrize("purpose", ["record", "example"])
@pytest.mark.asyncio
async def test_pipeline_formal_vlm_and_saved_rules_precedence(
    rules_source: str, purpose: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    from io import BytesIO

    from PIL import Image
    from rag_engine.knowledge.prompt_files import IMAGE_RETRIEVAL_PROMPT_KEY
    from rag_engine.models.layout import LayoutRecord
    from rag_engine.models.llm import PictureDescriptionOutput
    from rag_engine.parsing.vision_prompt_rules import RULES_END, RULES_START
    from rag_engine.resources.runtime import bind_prompt_overrides

    from app.clients import oci_enterprise_ai

    legacy = (Path(__file__).parent / "fixtures/vision-purpose-legacy-rules.txt").read_text()
    custom = "## Step 1: 独自の読み取り\n見える原文と位置だけを残す。"
    overrides: dict[str, str] = {}
    if rules_source == "saved":
        overrides["picture_description.rules"] = custom
    elif rules_source == "legacy":
        overrides[IMAGE_RETRIEVAL_PROMPT_KEY] = (
            "## Step 1: 画像を読む\n{{image_metadata}}\n"
            + RULES_START
            + "\n"
            + legacy
            + "\n"
            + RULES_END
        )

    class Reader:
        calls: list[dict[str, Any]]

        def __init__(self) -> None:
            self.calls = []

        async def generate_from_images(self, images: Any, prompt: str, **kwargs: Any) -> str:
            self.calls.append(
                {
                    "images": [hashlib.sha256(b).hexdigest() for b in images],
                    "prompt": prompt,
                    **kwargs,
                }
            )
            return json.dumps(
                {
                    "retrieval_text": "合成の値42",
                    "visible_values": ["42"],
                    "visual_kind": "screenshot",
                }
            )

    class Store:
        async def upsert_document_extraction_artifact(self, **kwargs: Any) -> None:
            self.saved = json.loads(json.dumps(kwargs))

        async def update_document_recipe_status(self, **kwargs: Any) -> None:
            self.pointer = kwargs

    reader = Reader()
    store = Store()
    cfg = settings(purpose)
    cfg.rag_vision_enabled = True
    unused: Any = object()
    p = IngestionPipeline(
        settings=cfg,
        vlm=cast(Any, reader),
        genai=unused,
        oracle=cast(Any, store),
        object_storage=unused,
        document_understanding=unused,
        recipe_id="synthetic-recipe-a",
        recipe_revision=3,
    )
    synthetic_config = SimpleNamespace(
        vision_model_id="synthetic",
        oci_enterprise_ai_endpoint="https://synthetic.invalid",
        oci_enterprise_ai_project_ocid="",
    )
    monkeypatch.setattr(
        oci_enterprise_ai,
        "config_from_settings",
        lambda *_a, **_k: SimpleNamespace(for_vision=lambda: synthetic_config),
    )
    picture = LayoutRecord(
        id="picture-a",
        engine="docling",
        page=1,
        seq_no=0,
        bbox=[10, 10, 80, 80],
        coord_system="image_top_left",
        page_width=100,
        page_height=100,
        category="Picture",
        raw_type="picture",
    )
    extraction = StructuredExtraction(
        parser_artifacts={"layout_records": {"records": [picture.to_dict()]}}
    )
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    png = buffer.getvalue()
    with bind_prompt_overrides(overrides):
        updated = await p._attach_vision(
            "synthetic-trace",
            extraction,
            source_bytes=png,
            content_type="image/png",
            file_name="synthetic.png",
            parser_backend="docling",
            cancel_checker=None,
        )
    assert len(reader.calls) == 1
    call = reader.calls[0]
    assert len(call["images"]) == 2
    assert call["response_schema"] == PictureDescriptionOutput.model_json_schema()
    assert call["response_schema_name"] == "picture_description"
    assert f'"vision_image_purpose": "{purpose}"' in call["prompt"]
    if rules_source == "builtin":
        assert "metadata.vision_image_purpose" in call["prompt"]
        assert "原則として文書の例示" not in call["prompt"]
    elif rules_source == "saved":
        assert custom in call["prompt"]
        assert "metadata.vision_image_purpose" not in call["prompt"]
    else:
        assert legacy.strip() in call["prompt"]
        assert "原則として文書の例示" in call["prompt"]
        assert "metadata.vision_image_purpose" not in call["prompt"]
    assert updated.parser_artifacts["vision_image_purpose"] == purpose
    assert updated.elements and updated.elements[0].metadata["vision_image_purpose"] == purpose
    await p._persist_extraction_layer(
        "synthetic-document-a",
        cast(Any, SimpleNamespace(content_sha256=hashlib.sha256(png).hexdigest())),
        updated,
    )
    loaded = StructuredExtraction.model_validate(store.saved["extraction"])
    assert loaded.elements[0].metadata["vision_image_purpose"] == purpose
    # 保存されたlegacy/customを黙って新規則へ変えていない。意味互換は別のreadiness課題。


def test_purpose_is_frozen_for_pipeline_after_global_change() -> None:
    cfg = settings("record")
    actual = pipeline(cfg)
    first = actual._segment_parse_key(b"synthetic")
    cfg.rag_vision_image_purpose = "example"
    assert actual._settings.rag_vision_image_purpose == "record"
    assert actual._segment_parse_key(b"synthetic") == first
    assert pipeline(cfg)._segment_parse_key(b"synthetic") != first


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.asyncio
async def test_native_ocr_table_sources_are_not_promoted_when_vision_off_or_fails(
    failed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag_engine.retrieval.evidence_selection import _span_origin
    from rag_parser_core.vision_text_map import VISION_TEXT_BINDING_KEY

    from app.clients import oci_enterprise_ai
    from app.rag import ingestion as ingestion_module

    elements = []
    for index, kind in enumerate(["text", "figure", "table"]):
        text = "<table><tr><td>42</td></tr></table>" if kind == "table" else "合成の原文42"
        metadata = {
            "source_id": f"synthetic-source-{index}",
            "source_kind": "native_table"
            if kind == "table"
            else "native_ocr"
            if kind == "figure"
            else "native_text",
            "source_start": 0,
            "source_end": len(text),
        }
        if kind == "figure":
            metadata["figure_text_source"] = "ocr"
        elements.append(
            DocumentElement(
                element_id=f"element-{index}",
                kind=kind,
                text=text,
                page_number=2,
                bbox=[1, 2, 30, 40],
                source_parser="synthetic-parser",
                metadata=metadata,
            )
        )
    source = StructuredExtraction(elements=elements)
    before = source.model_dump_json()
    cfg = settings("record")
    cfg.rag_vision_enabled = failed

    class Store:
        async def answer_prompt_overrides(self) -> dict[str, str]:
            return {}

    actual = pipeline(cfg, Store())
    if failed:
        fake_config = SimpleNamespace(
            vision_model_id="synthetic",
            oci_enterprise_ai_endpoint="https://synthetic.invalid",
            oci_enterprise_ai_project_ocid="",
        )
        monkeypatch.setattr(
            oci_enterprise_ai,
            "config_from_settings",
            lambda *_a, **_k: SimpleNamespace(for_vision=lambda: fake_config),
        )

        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("synthetic render failure")

        monkeypatch.setattr(ingestion_module, "read_figures_with_vision", broken)
    result = await actual._attach_vision(
        "synthetic",
        source,
        source_bytes=b"",
        content_type="application/pdf",
        file_name="synthetic.pdf",
        parser_backend="docling",
        cancel_checker=None,
    )
    assert source.model_dump_json() == before
    assert len(result.elements) == len(source.elements)
    for original, saved in zip(source.elements, result.elements, strict=True):
        assert saved.element_id == original.element_id
        assert saved.kind == original.kind
        assert saved.source_parser == original.source_parser
        assert saved.text == original.text
        assert saved.page_number == original.page_number
        assert saved.bbox == original.bbox
        assert saved.metadata["source_id"] == original.metadata["source_id"]
        assert saved.metadata["source_kind"] == original.metadata["source_kind"]
        assert saved.metadata["source_start"] == original.metadata["source_start"]
        assert saved.metadata["source_end"] == original.metadata["source_end"]
        assert VISION_TEXT_BINDING_KEY not in saved.metadata
        assert "image_text_source_context" not in saved.metadata
        assert "image_text_source_scope" not in saved.metadata
        before_origin = _span_origin(
            SimpleNamespace(text=original.text, metadata=original.metadata),
            0,
            len(original.text),
            [],
        )
        after_origin = _span_origin(
            SimpleNamespace(text=saved.text, metadata=saved.metadata), 0, len(saved.text), []
        )
        assert before_origin == after_origin
        assert after_origin != "image_extraction"
        if original.kind in {"text", "table"}:
            assert saved.metadata == original.metadata
        else:
            assert saved.metadata == {**original.metadata, "vision_image_purpose": "record"}
            assert saved.metadata["figure_text_source"] == "ocr"
    assert ("vision_failed" in result.warnings) == failed
