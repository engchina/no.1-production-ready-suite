"""DocRAG のプロンプト(回答生成テンプレート・画像検索)の編集と、回答・解析への反映。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from docrag.knowledge.prompt_files import (
    IMAGE_RETRIEVAL_PROMPT_KEY,
    VLM_ANSWER_PROMPT_KEY,
    read_prompt,
)

from app.api.routes import settings as settings_routes
from app.clients.parser_service import ParserServiceClient
from app.config import Settings
from app.main import app
from app.rag.docrag_answer import DocragAnswerEngine
from app.rag.docrag_prompts import default_prompt, prompt_overrides, validate_prompt
from app.schemas.search import SearchRequest
from tests.support import AsgiTestClient
from tests.test_docrag_answer_engine import FakeGenAi, FakeOracle

client = AsgiTestClient(app)
CUSTOM_ANSWER = "独自の指示\n{{question}}\n{{images}}"


def test_validate_prompt_rejects_empty_missing_placeholder_and_unknown_key() -> None:
    validate_prompt(VLM_ANSWER_PROMPT_KEY, CUSTOM_ANSWER)
    with pytest.raises(ValueError, match="空"):
        validate_prompt(VLM_ANSWER_PROMPT_KEY, "  ")
    with pytest.raises(ValueError, match=r"\{\{images\}\}"):
        validate_prompt(VLM_ANSWER_PROMPT_KEY, "{{question}} だけ")
    with pytest.raises(ValueError, match=r"\{\{image_metadata\}\}"):
        validate_prompt(IMAGE_RETRIEVAL_PROMPT_KEY, "画像を説明して")
    with pytest.raises(KeyError):
        validate_prompt("audit", "x")


def test_prompt_overrides_apply_only_inside_block() -> None:
    default = read_prompt(VLM_ANSWER_PROMPT_KEY)

    with prompt_overrides({VLM_ANSWER_PROMPT_KEY: CUSTOM_ANSWER}):
        assert read_prompt(VLM_ANSWER_PROMPT_KEY) == CUSTOM_ANSWER
        assert read_prompt(IMAGE_RETRIEVAL_PROMPT_KEY) == default_prompt(IMAGE_RETRIEVAL_PROMPT_KEY)

    assert read_prompt(VLM_ANSWER_PROMPT_KEY) == default


class FakePromptOracle:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, object]] = {}

    async def list_docrag_prompts(self) -> dict[str, dict[str, object]]:
        return dict(self.saved)

    async def save_docrag_prompt(self, key: str, content: str) -> None:
        self.saved[key] = {"content": content, "updated_at": datetime(2026, 9, 26, tzinfo=UTC)}

    async def delete_docrag_prompt(self, key: str) -> bool:
        return self.saved.pop(key, None) is not None


def _prompt(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in data["prompts"] if item["key"] == key)


def test_docrag_prompts_api_saves_validates_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)

    initial = client.get("/api/settings/docrag-prompts").json()["data"]
    saved = client.put(
        f"/api/settings/docrag-prompts/{VLM_ANSWER_PROMPT_KEY}", json={"content": CUSTOM_ANSWER}
    )
    invalid = client.put(
        f"/api/settings/docrag-prompts/{VLM_ANSWER_PROMPT_KEY}", json={"content": "{{question}}"}
    )
    unknown = client.put("/api/settings/docrag-prompts/audit", json={"content": "x"})
    reset = client.delete(f"/api/settings/docrag-prompts/{VLM_ANSWER_PROMPT_KEY}")

    answer = _prompt(initial, VLM_ANSWER_PROMPT_KEY)
    assert answer["customized"] is False
    assert answer["content"] == answer["default_content"] == default_prompt(VLM_ANSWER_PROMPT_KEY)
    assert answer["required_placeholders"] == ["question", "images"]
    assert [stage["id"] for stage in initial["stages"]] == [
        "routing",
        "expansion",
        "crag",
        "generation",
        "audit",
        "evaluation",
    ]
    assert saved.status_code == 200
    assert _prompt(saved.json()["data"], VLM_ANSWER_PROMPT_KEY)["content"] == CUSTOM_ANSWER
    assert _prompt(saved.json()["data"], VLM_ANSWER_PROMPT_KEY)["customized"] is True
    assert invalid.status_code == 422
    assert "{{images}}" in invalid.json()["error_messages"][0]
    assert unknown.status_code == 404
    assert _prompt(reset.json()["data"], VLM_ANSWER_PROMPT_KEY)["customized"] is False
    assert fake.saved == {}


class PromptFakeOracle(FakeOracle):
    def __init__(self, overrides: dict[str, str] | Exception) -> None:
        super().__init__()
        self.overrides = overrides

    async def docrag_prompt_overrides(self) -> dict[str, str]:
        if isinstance(self.overrides, Exception):
            raise self.overrides
        return self.overrides


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({VLM_ANSWER_PROMPT_KEY: CUSTOM_ANSWER}, CUSTOM_ANSWER),
        # 読み込みに失敗しても既定値で回答する。
        (RuntimeError("db down"), default_prompt(VLM_ANSWER_PROMPT_KEY)),
    ],
)
async def test_docrag_answer_uses_saved_answer_template(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str] | Exception, expected: str
) -> None:
    import docrag.generation.answering as answering

    seen: list[str] = []

    def fake_answer(*args: Any, **kwargs: Any) -> Any:
        seen.append(read_prompt(VLM_ANSWER_PROMPT_KEY))
        raise RuntimeError("stop")

    monkeypatch.setattr(answering, "answer_question_result", fake_answer)
    engine = DocragAnswerEngine(
        Settings(),
        oracle=PromptFakeOracle(overrides),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="stop"):
        await engine.run(SearchRequest(query="受注の登録方法は？"))

    assert seen == [expected]
    assert read_prompt(VLM_ANSWER_PROMPT_KEY) == default_prompt(VLM_ANSWER_PROMPT_KEY)


def test_parser_options_send_saved_image_retrieval_prompt() -> None:
    parser = ParserServiceClient(Settings(rag_parser_docling_vision_enabled=True))

    assert parser._parser_options("docling") == {"vision_enabled": True}
    parser.image_retrieval_prompt = "独自 {{image_metadata}}"
    assert parser._parser_options("docling") == {
        "vision_enabled": True,
        "image_retrieval_prompt": "独自 {{image_metadata}}",
    }
    assert parser._parser_options("marker") == {}


async def test_ingestion_loads_image_retrieval_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ingestion import IngestionPipeline

    class Oracle:
        def __init__(self, result: dict[str, str] | Exception) -> None:
            self.result = result

        async def docrag_prompt_overrides(self) -> dict[str, str]:
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    saved = IngestionPipeline(
        oracle=Oracle({IMAGE_RETRIEVAL_PROMPT_KEY: "独自 {{image_metadata}}"}),  # type: ignore[arg-type]
        settings=Settings(),
    )
    failing = IngestionPipeline(oracle=Oracle(RuntimeError("down")), settings=Settings())  # type: ignore[arg-type]
    unsaved = IngestionPipeline(oracle=Oracle({}), settings=Settings())  # type: ignore[arg-type]

    assert await saved._image_retrieval_prompt() == "独自 {{image_metadata}}"
    assert await failing._image_retrieval_prompt() is None
    assert await unsaved._image_retrieval_prompt() is None


@pytest.mark.usefixtures("oracle_db")
async def test_docrag_prompts_round_trip_on_real_oracle() -> None:
    """実 Oracle 26ai で、編集したプロンプトの保存・上書き・削除ができる。"""
    from app.clients.oracle import OracleClient

    oracle = OracleClient()
    try:
        await oracle.save_docrag_prompt(VLM_ANSWER_PROMPT_KEY, "一回目 {{question}} {{images}}")
        await oracle.save_docrag_prompt(VLM_ANSWER_PROMPT_KEY, CUSTOM_ANSWER)
        assert (await oracle.docrag_prompt_overrides())[VLM_ANSWER_PROMPT_KEY] == CUSTOM_ANSWER
        saved = await oracle.list_docrag_prompts()
        assert saved[VLM_ANSWER_PROMPT_KEY]["updated_at"] is not None
    finally:
        assert await oracle.delete_docrag_prompt(VLM_ANSWER_PROMPT_KEY)
    assert VLM_ANSWER_PROMPT_KEY not in await oracle.docrag_prompt_overrides()
    assert not await oracle.delete_docrag_prompt(VLM_ANSWER_PROMPT_KEY)
