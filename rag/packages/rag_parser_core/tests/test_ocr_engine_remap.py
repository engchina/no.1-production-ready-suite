"""GPU OCR の remap 層を fake SDK module で決定論検証する。

GPU は CI 非搭載のため、実 OCR(GPU シーム)が呼ぶ SDK を fake module へ差し替え、
出力(markdown / 要素)が `StructuredExtraction` へ正しく
再マップされることだけを検証する。実 GPU 実行は手動 integration で確認する。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from rag_parser_core import registry
from rag_parser_core.registry import _external_adapter_result
from rag_parser_core.source import SourceModality, SourceProfile


def _pdf_profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="scan.pdf",
        sanitized_file_name="scan.pdf",
        content_type="application/pdf",
        file_size_bytes=16,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )


@pytest.mark.parametrize(
    ("backend", "module_name", "entry"),
    [
        ("unlimited_ocr", "unlimited_ocr", "parse"),
        ("mineru", "mineru", "parse_document"),
        ("dots_ocr", "dots_ocr", "parse"),
        ("glm_ocr", "glm_ocr", "parse"),
    ],
)
def test_ocr_engine_markdown_remaps_to_structured_extraction(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    module_name: str,
    entry: str,
) -> None:
    fake = types.ModuleType(module_name)
    setattr(fake, entry, lambda _path: "# 請求書\n\n合計 1,200 円")
    monkeypatch.setitem(sys.modules, module_name, fake)

    result = _external_adapter_result(
        backend,
        source_bytes=b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
    )

    assert result.parser_backend == backend
    assert result.fallback_used is False
    assert result.extraction is not None
    assert "請求書" in result.extraction.raw_text
    assert result.extraction.parser_artifacts["external_adapter"] == backend
    assert result.extraction.parser_artifacts["ocr_engine"] is True
    assert any(
        element.source_parser == f"{backend}_adapter" for element in result.extraction.elements
    )


def test_ocr_engine_without_entry_point_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("mineru")  # エントリポイント無し
    monkeypatch.setitem(sys.modules, "mineru", fake)

    result = _external_adapter_result(
        "mineru",
        source_bytes=b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
    )

    assert result.extraction is None
    assert result.fallback_used is True
    assert "mineru_adapter_failed" in result.warnings


def test_mineru_cli_fallback_reads_generated_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_path = bin_dir / "python"
    python_path.write_text("", encoding="utf-8")
    mineru_bin = bin_dir / "mineru"
    mineru_bin.write_text(
        "#!/bin/sh\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then shift; out=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p \"$out/doc/ocr\"\n"
        "printf '# MinerU OCR\\n' > \"$out/doc/ocr/doc.md\"\n",
        encoding="utf-8",
    )
    mineru_bin.chmod(0o755)
    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    monkeypatch.setattr(registry.sys, "executable", str(python_path))

    assert registry._run_mineru_cli(source) == "# MinerU OCR\n"


@pytest.mark.parametrize(
    ("artifact_name", "payload", "artifact_type"),
    [
        (
            "doc_content_list.json",
            [{"type": "header", "text": "テストPDF", "bbox": [109, 63, 202, 80], "page_idx": 0}],
            "header",
        ),
        (
            "doc_content_list_v2.json",
            [
                [
                    {
                        "type": "page_header",
                        "content": {
                            "page_header_content": [
                                {"type": "text", "content": "テストPDF"}
                            ]
                        },
                        "bbox": [109, 63, 202, 80],
                    }
                ]
            ],
            "text",
        ),
        (
            "doc_model.json",
            [[{"type": "header", "content": "テストPDF", "bbox": [0.11, 0.064, 0.204, 0.081]}]],
            "header",
        ),
    ],
)
def test_mineru_cli_fallback_reads_json_artifacts_when_markdown_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact_name: str,
    payload: object,
    artifact_type: str,
) -> None:
    fake = types.ModuleType("mineru")  # MinerU 3.x は top-level API を持たない
    monkeypatch.setitem(sys.modules, "mineru", fake)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_path = bin_dir / "python"
    python_path.write_text("", encoding="utf-8")
    mineru_bin = bin_dir / "mineru"
    mineru_bin.write_text(
        "#!/bin/sh\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '-o' ]; then shift; out=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p \"$out/doc/hybrid_auto\"\n"
        ": > \"$out/doc/hybrid_auto/doc.md\"\n"
        f"cat > \"$out/doc/hybrid_auto/{artifact_name}\" <<'JSON'\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "JSON\n",
        encoding="utf-8",
    )
    mineru_bin.chmod(0o755)

    monkeypatch.setattr(registry.sys, "executable", str(python_path))

    result = _external_adapter_result(
        "mineru",
        source_bytes=b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
    )

    assert result.parser_backend == "mineru"
    assert result.fallback_used is False
    assert result.extraction is not None
    assert result.extraction.raw_text == "テストPDF"
    assert result.extraction.parser_artifacts["adapter_export"] == "structured_elements"
    assert result.extraction.elements[0].kind == "text"
    assert result.extraction.elements[0].page_number == 1
    assert result.extraction.elements[0].metadata["mineru_artifact_type"] == artifact_type


def test_dots_ocr_vllm_parser_reads_generated_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry._DOTS_OCR_VLLM_PARSER_CACHE.clear()

    class FakeDotsOCRParser:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["use_hf"] is False
            assert kwargs["ip"] == "parser-dots-ocr-vllm"
            assert kwargs["model_name"] == "model"

        def parse_file(
            self,
            _input_path: str,
            *,
            output_dir: str,
            prompt_mode: str,
            fitz_preprocess: bool,
        ) -> list[dict[str, str]]:
            assert prompt_mode == "prompt_layout_all_en"
            assert fitz_preprocess is True
            md_path = Path(output_dir) / "doc" / "doc.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text("# Dots OCR\n", encoding="utf-8")
            return [{"md_content_path": str(md_path)}]

    fake_parser_module = types.ModuleType("dots_ocr.parser")
    fake_parser_module.DotsOCRParser = FakeDotsOCRParser
    monkeypatch.setitem(sys.modules, "dots_ocr.parser", fake_parser_module)
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: fake_parser_module)

    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    assert registry._run_dots_ocr_parser(source) == "# Dots OCR\n"


def test_dots_ocr_hf_parser_reads_generated_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry._DOTS_OCR_PARSER_CACHE.clear()
    monkeypatch.setenv("DOTS_OCR_RUNTIME", "hf_explicit_cuda")

    class FakeDotsOCRParser:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["use_hf"] is True

        def parse_file(
            self,
            _input_path: str,
            *,
            output_dir: str,
            prompt_mode: str,
            fitz_preprocess: bool,
        ) -> list[dict[str, str]]:
            assert prompt_mode == "prompt_layout_all_en"
            assert fitz_preprocess is True
            md_path = Path(output_dir) / "doc" / "doc.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text("# Dots OCR\n", encoding="utf-8")
            return [{"md_content_path": str(md_path)}]

    fake_parser_module = types.ModuleType("dots_ocr.parser")
    fake_parser_module.DotsOCRParser = FakeDotsOCRParser
    monkeypatch.setitem(sys.modules, "dots_ocr.parser", fake_parser_module)
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: fake_parser_module)

    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    assert registry._run_dots_ocr_parser(source) == "# Dots OCR\n"


def test_glm_ocr_pipeline_uses_explicit_cuda_device(monkeypatch: pytest.MonkeyPatch) -> None:
    registry._GLM_OCR_PIPELINE_CACHE.clear()
    captured: dict[str, object] = {}

    class FakeModel:
        device: object | None = None
        evaluated = False

        def to(self, device: object) -> FakeModel:
            self.device = device
            return self

        def eval(self) -> None:
            self.evaluated = True

    class FakeAutoProcessor:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> object:
            captured["processor_model_id"] = model_id
            captured["processor_kwargs"] = kwargs
            return object()

    class FakeAutoModelForImageTextToText:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> FakeModel:
            captured["model_id"] = model_id
            captured["model_kwargs"] = kwargs
            return FakeModel()

    fake_torch = types.SimpleNamespace(
        bfloat16="bfloat16",
        float16="float16",
        float32="float32",
        cuda=types.SimpleNamespace(is_available=lambda: True),
        device=lambda value: f"device:{value}",
    )
    fake_transformers = types.SimpleNamespace(
        AutoProcessor=FakeAutoProcessor,
        AutoModelForImageTextToText=FakeAutoModelForImageTextToText,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    _processor, model = registry._load_glm_ocr_pipeline("test/glm-ocr")

    model_kwargs = captured["model_kwargs"]
    assert isinstance(model_kwargs, dict)
    assert model_kwargs["dtype"] == "bfloat16"
    assert "device_map" not in model_kwargs
    assert model.device == "device:cuda:0"
    assert model.evaluated is True


def test_glm_ocr_pipeline_requires_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    registry._GLM_OCR_PIPELINE_CACHE.clear()
    fake_torch = types.SimpleNamespace(
        bfloat16="bfloat16",
        float16="float16",
        float32="float32",
        cuda=types.SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with pytest.raises(RuntimeError, match="glm_ocr_cuda_unavailable"):
        registry._load_glm_ocr_pipeline("test/glm-ocr")


def test_unlimited_ocr_pipeline_uses_dtype_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry._UNLIMITED_OCR_PIPELINE_CACHE.clear()
    captured: dict[str, object] = {}

    class FakeModel:
        device: object | None = None

        def eval(self) -> FakeModel:
            return self

        def to(self, device: object) -> FakeModel:
            self.device = device
            return self

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> object:
            captured["tokenizer_model_id"] = model_id
            captured["tokenizer_kwargs"] = kwargs
            return object()

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> FakeModel:
            captured["model_id"] = model_id
            captured["model_kwargs"] = kwargs
            return FakeModel()

    fake_torch = types.SimpleNamespace(
        bfloat16="bfloat16",
        float16="float16",
        float32="float32",
        cuda=types.SimpleNamespace(is_available=lambda: True),
        device=lambda value: f"device:{value}",
    )
    fake_transformers = types.SimpleNamespace(
        AutoTokenizer=FakeAutoTokenizer,
        AutoModel=FakeAutoModel,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    _tokenizer, model = registry._load_unlimited_ocr_pipeline("test/unlimited-ocr")

    model_kwargs = captured["model_kwargs"]
    assert isinstance(model_kwargs, dict)
    assert model_kwargs["dtype"] == "bfloat16"
    assert "torch_dtype" not in model_kwargs
    assert model.device == "device:cuda:0"


def test_glm_ocr_without_wrapper_uses_transformers_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry._GLM_OCR_PIPELINE_CACHE.clear()
    sys.modules.pop("glm_ocr", None)
    monkeypatch.setenv("GLM_OCR_RUNTIME", "transformers")

    class FakeImage:
        def convert(self, _mode: str) -> FakeImage:
            return self

    class FakeImageModule:
        @staticmethod
        def open(_path: str) -> FakeImage:
            return FakeImage()

    class FakeInputs(dict):
        def to(self, _device: object) -> FakeInputs:
            return self

    class FakeProcessor:
        def apply_chat_template(self, *_args: object, **_kwargs: object) -> FakeInputs:
            return FakeInputs(input_ids=types.SimpleNamespace(shape=(1, 1)))

        def batch_decode(self, _tokens: object, skip_special_tokens: bool) -> list[str]:
            assert skip_special_tokens is True
            return ["# OCR"]

    class FakeModel:
        device = "cuda:0"

        def generate(self, **_kwargs: object) -> object:
            class FakeGenerated:
                def __getitem__(self, _key: object) -> object:
                    return object()

            return FakeGenerated()

    monkeypatch.setattr(registry, "_module_available", lambda name: name == "transformers")
    monkeypatch.setattr(
        registry,
        "_load_glm_ocr_pipeline",
        lambda _model_id: (FakeProcessor(), FakeModel()),
    )
    monkeypatch.setattr(registry.importlib, "import_module", lambda _name: FakeImageModule)

    result = registry._external_adapter_result(
        "glm_ocr",
        source_bytes=b"png",
        source_profile=_pdf_profile(),
        content_type="image/png",
    )

    assert result.fallback_used is False
    assert result.extraction is not None
    assert "OCR" in result.extraction.raw_text


def test_unlimited_ocr_without_wrapper_uses_transformers_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry._UNLIMITED_OCR_PIPELINE_CACHE.clear()
    sys.modules.pop("unlimited_ocr", None)
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    empty_cache_calls: list[str] = []
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(empty_cache=lambda: empty_cache_calls.append("empty"))
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    registry._UNLIMITED_OCR_PIPELINE_CACHE["test"] = (object(), object())

    class FakeModel:
        def infer(self, _image_path: str, output_path: str, **_kwargs: object) -> None:
            Path(output_path, "source.md").write_text("# Unlimited OCR\n", encoding="utf-8")

    monkeypatch.setattr(registry, "_module_available", lambda name: name == "transformers")
    monkeypatch.setattr(
        registry,
        "_load_unlimited_ocr_pipeline",
        lambda _model_id: (object(), FakeModel()),
    )

    assert registry._run_unlimited_ocr_transformers(source) == "# Unlimited OCR\n"
    assert registry._UNLIMITED_OCR_PIPELINE_CACHE == {}
    assert empty_cache_calls == ["empty"]


def test_glm_ocr_vllm_posts_image_and_reads_chat_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "# GLM OCR"}}]}
            ).encode("utf-8")

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(registry.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GLM_OCR_VLLM_BASE_URL", "http://glm-vllm:8080/v1")
    monkeypatch.setenv("GLM_OCR_VLLM_MODEL", "glm-ocr-test")
    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    text = registry._run_glm_ocr_vllm(source)

    assert text == "# GLM OCR"
    assert captured["url"] == "http://glm-vllm:8080/v1/chat/completions"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "glm-ocr-test"
    content = payload["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
