"""回答フローのプロンプト(生成の役割の文・画像検索)の編集と、回答・解析への反映。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from rag_engine.knowledge.prompt_files import IMAGE_RETRIEVAL_PROMPT_KEY, read_prompt

from app.api.routes import settings as settings_routes
from app.clients.parser_service import ParserServiceClient
from app.config import Settings
from app.main import app
from app.rag.answer_engine import AnswerEngine
from app.rag.answer_prompts import (
    EDITABLE_PROMPT_KEYS,
    PROMPT_STAGES,
    default_prompt,
    prompt_overrides,
    required_placeholders,
    validate_prompt,
)
from app.schemas.search import SearchRequest
from app.security.permissions import ANSWER_PROMPT_EDIT_PERMISSIONS
from tests.prompt_support import UneditedPromptOracle
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, FakeOracle

client = AsgiTestClient(app)
# 生成で編集できるのは役割と回答の書き方だけ（#4782）。
ROLE_KEY = "generation.role"
CUSTOM_ROLE = "## Step 1: 合成の役割\n合成の内容を返す。"
CUSTOM_IMAGE = "独自 {{image_metadata}}"
# 編集欄を外した key（固定の部分・入力テンプレート・内部の段）。保存も既定に戻す操作も受けない。
FIXED_KEYS = (
    "vlm_answer",
    "generation.system",
    "generation.fixed",
    "generation.template",
    "generation.evidenceReading",
    "history_rewrite.system",
)


def test_saved_generation_prompt_does_not_need_the_language_placeholder() -> None:
    """既定の言語の差し込みは任意。無い指示も保存でき、差し込まれないだけ（#4755）。"""
    assert "language" not in required_placeholders(ROLE_KEY)
    validate_prompt(ROLE_KEY, CUSTOM_ROLE)


def test_validate_prompt_rejects_empty_missing_placeholder_and_unknown_key() -> None:
    validate_prompt(ROLE_KEY, CUSTOM_ROLE)
    with pytest.raises(ValueError, match="空"):
        validate_prompt(ROLE_KEY, "  ")
    with pytest.raises(ValueError, match=r"\{\{image_metadata\}\}"):
        validate_prompt(IMAGE_RETRIEVAL_PROMPT_KEY, "画像を説明して")
    for key in ("audit", *FIXED_KEYS):
        with pytest.raises(KeyError):
            validate_prompt(key, "x")


def test_prompt_overrides_apply_only_inside_block() -> None:
    default = read_prompt(IMAGE_RETRIEVAL_PROMPT_KEY)

    with prompt_overrides({IMAGE_RETRIEVAL_PROMPT_KEY: CUSTOM_IMAGE}):
        assert read_prompt(IMAGE_RETRIEVAL_PROMPT_KEY) == CUSTOM_IMAGE

    assert read_prompt(IMAGE_RETRIEVAL_PROMPT_KEY) == default


class FakePromptOracle:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, object]] = {}

    async def list_answer_prompts(self) -> dict[str, dict[str, object]]:
        return dict(self.saved)

    async def save_answer_prompt(self, key: str, content: str) -> None:
        self.saved[key] = {"content": content, "updated_at": datetime(2026, 9, 26, tzinfo=UTC)}

    async def delete_answer_prompt(self, key: str) -> bool:
        return self.saved.pop(key, None) is not None


def _prompt(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in data["prompts"] if item["key"] == key)


def test_answer_prompts_api_saves_validates_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)

    initial = client.get("/api/settings/answer-prompts").json()["data"]
    saved = client.put(f"/api/settings/answer-prompts/{ROLE_KEY}", json={"content": CUSTOM_ROLE})
    invalid = client.put(f"/api/settings/answer-prompts/{ROLE_KEY}", json={"content": " "})
    unknown = client.put("/api/settings/answer-prompts/audit", json={"content": "x"})
    reset = client.delete(f"/api/settings/answer-prompts/{ROLE_KEY}")

    answer = _prompt(initial, ROLE_KEY)
    assert answer["customized"] is False
    assert answer["content"] == answer["default_content"] == default_prompt(ROLE_KEY)
    assert answer["required_placeholders"] == []
    # 各段は取込から評価まで処理の順(一覧とコードの照合は test_answer_prompt_stages.py。#2054)。
    assert [(stage["id"], stage["phase"]) for stage in initial["stages"]] == [
        (stage.id, stage.phase) for stage in PROMPT_STAGES
    ]
    assert initial["stages"][0]["phase"] == "ingestion"
    assert initial["stages"][-1]["phase"] == "evaluation"
    assert saved.status_code == 200
    assert _prompt(saved.json()["data"], ROLE_KEY)["content"] == CUSTOM_ROLE
    assert _prompt(saved.json()["data"], ROLE_KEY)["customized"] is True
    assert invalid.status_code == 422
    assert unknown.status_code == 404
    assert _prompt(reset.json()["data"], ROLE_KEY)["customized"] is False
    assert fake.saved == {}


@pytest.mark.parametrize("key", FIXED_KEYS)
def test_answer_prompts_api_rejects_keys_without_an_edit_field(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """固定の部分と内部の段は保存できず、残っている保存値も画面・実行に出ない（#4782）。"""
    fake = FakePromptOracle()
    fake.saved[key] = {"content": "## Step 1: 残っている保存値", "updated_at": None}
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    path = f"/api/settings/answer-prompts/{key}"

    assert client.put(path, json={"content": CUSTOM_ROLE}).status_code == 404
    assert client.delete(path).status_code == 404
    assert fake.saved[key]["content"] == "## Step 1: 残っている保存値"

    data = client.get("/api/settings/answer-prompts").json()["data"]
    assert key not in {item["key"] for item in data["prompts"]}
    shown = {
        f"{stage['id']}.{part['id']}": part for stage in data["stages"] for part in stage["prompts"]
    }
    if key in shown:
        assert shown[key]["prompt_key"] is None
        assert shown[key]["content"] != "## Step 1: 残っている保存値"


def test_answer_prompt_edit_requires_the_screen_permission_of_each_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答生成のプロンプトは「回答プロンプト」、図・画像の読み取りは「文書解析」の権限で変える。

    画面はそれぞれ 1 つのプロンプトだけを編集するので、API も同じ範囲にする（片方の権限で
    もう片方のプロンプトを変えられない）。読むのはどちらの権限でもよい。
    """
    # 編集できるプロンプトはすべて、変更に要る権限を持つ。
    assert set(ANSWER_PROMPT_EDIT_PERMISSIONS) == set(EDITABLE_PROMPT_KEYS)
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("prompt-admin", ["menu.settings_prompts"])
    auth.user_with_permissions("parser-admin", ["menu.settings_parser_adapters"])
    image_prompt = CUSTOM_IMAGE

    prompt_admin = login(client, "prompt-admin")
    parser_admin = login(client, "parser-admin")
    for headers in (prompt_admin, parser_admin):
        assert client.get("/api/settings/answer-prompts", headers=headers).status_code == 200

    answer_path = f"/api/settings/answer-prompts/{ROLE_KEY}"
    image_path = f"/api/settings/answer-prompts/{IMAGE_RETRIEVAL_PROMPT_KEY}"
    forbidden = [
        client.put(answer_path, json={"content": CUSTOM_ROLE}, headers=parser_admin),
        client.delete(answer_path, headers=parser_admin),
        client.put(image_path, json={"content": image_prompt}, headers=prompt_admin),
        client.delete(image_path, headers=prompt_admin),
    ]
    assert [response.status_code for response in forbidden] == [403, 403, 403, 403]
    assert forbidden[0].json()["error_messages"] == ["この機能を利用する権限がありません。"]
    assert fake.saved == {}

    assert (
        client.put(answer_path, json={"content": CUSTOM_ROLE}, headers=prompt_admin).status_code
        == 200
    )
    assert (
        client.put(image_path, json={"content": image_prompt}, headers=parser_admin).status_code
        == 200
    )
    assert set(fake.saved) == {ROLE_KEY, IMAGE_RETRIEVAL_PROMPT_KEY}
    assert client.delete(answer_path, headers=prompt_admin).status_code == 200
    assert client.delete(image_path, headers=parser_admin).status_code == 200
    assert fake.saved == {}


@pytest.mark.parametrize("key", EDITABLE_PROMPT_KEYS)
@pytest.mark.parametrize("permission", ["menu.settings_retrieval", "menu.settings_evaluation"])
def test_retrieval_and_evaluation_permissions_can_read_but_cannot_edit_prompts(
    monkeypatch: pytest.MonkeyPatch, key: str, permission: str
) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("retrieval-admin", [permission])
    headers = login(client, "retrieval-admin")
    result = client.get("/api/settings/answer-prompts", headers=headers)
    assert result.status_code == 200
    assert any(stage["phase"] == "retrieval" for stage in result.json()["data"]["stages"])
    path = f"/api/settings/answer-prompts/{key}"
    allowed = ANSWER_PROMPT_EDIT_PERMISSIONS[key] == permission
    content = default_prompt(key)
    assert client.put(path, json={"content": content}, headers=headers).status_code == (
        200 if allowed else 403
    )
    assert client.delete(path, headers=headers).status_code == (200 if allowed else 403)
    assert fake.saved == {}


class PromptFakeOracle(FakeOracle):
    def __init__(self, overrides: dict[str, str] | Exception) -> None:
        super().__init__()
        self.overrides = overrides

    async def answer_prompt_overrides(self) -> dict[str, str]:
        if isinstance(self.overrides, Exception):
            raise self.overrides
        return self.overrides


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({ROLE_KEY: CUSTOM_ROLE}, [CUSTOM_ROLE]),
        ({}, [default_prompt(ROLE_KEY)]),
        # 読み込み失敗を未編集と見なして生成へ進まない。
        (RuntimeError("db down"), []),
    ],
)
async def test_answer_uses_saved_generation_role(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str] | Exception, expected: list[str]
) -> None:
    import rag_engine.generation.answering as answering
    from rag_engine.resources.runtime import resolve_prompt

    seen: list[str] = []

    def fake_answer(*args: Any, **kwargs: Any) -> Any:
        seen.append(resolve_prompt(ROLE_KEY, default_prompt(ROLE_KEY)))
        raise RuntimeError("stop")

    monkeypatch.setattr(answering, "answer_question_result", fake_answer)
    engine = AnswerEngine(
        Settings(),
        oracle=PromptFakeOracle(overrides),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        RuntimeError, match="db down" if isinstance(overrides, Exception) else "stop"
    ):
        await engine.run(SearchRequest(query="操作 A の手順は？"))

    assert seen == expected


def test_parser_service_does_not_send_vision_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vision は backend の解析後の共通の段で行う(#497)。Docling サービスへ指定を送らない。"""
    sent: dict[str, Any] = {}

    def fake_post(self: Any, backend: str, url: str, **kwargs: Any) -> dict[str, Any]:
        sent.update(kwargs["data"])
        raise RuntimeError("stop")

    monkeypatch.setattr(ParserServiceClient, "_post_parse_json", fake_post)
    parser = ParserServiceClient(Settings(rag_vision_enabled=True))
    monkeypatch.setattr(parser, "service_url", lambda backend: "http://parser.test")

    with pytest.raises(RuntimeError, match="stop"):
        parser.runner("docling", b"%PDF-1.4", None, "application/pdf")

    assert "parser_options" not in sent
    assert not hasattr(parser, "image_retrieval_prompt")


async def test_ingestion_loads_image_retrieval_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ingestion import IngestionPipeline

    class Oracle:
        def __init__(self, result: dict[str, str] | Exception) -> None:
            self.result = result

        async def answer_prompt_overrides(self) -> dict[str, str]:
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
    with pytest.raises(RuntimeError, match="down"):
        await failing._image_retrieval_prompt()
    assert await unsaved._image_retrieval_prompt() is None


async def test_answer_prompt_overrides_pass_only_editable_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """編集欄の無い key の保存値は、残っていても回答・解析へ渡さない（#4782）。"""
    from app.clients.oracle import OracleClient

    async def rows(self: OracleClient) -> dict[str, dict[str, object]]:
        return {
            ROLE_KEY: {"content": CUSTOM_ROLE},
            **{key: {"content": "残っている保存値"} for key in FIXED_KEYS},
        }

    monkeypatch.setattr(OracleClient, "list_answer_prompts", rows)
    assert await OracleClient().answer_prompt_overrides() == {ROLE_KEY: CUSTOM_ROLE}


@pytest.mark.usefixtures("oracle_db")
async def test_answer_prompts_round_trip_on_real_oracle() -> None:
    """実 Oracle AI Database で、編集したプロンプトの保存・上書き・削除ができる。"""
    from app.clients.oracle import OracleClient

    oracle = OracleClient()
    try:
        await oracle.save_answer_prompt(ROLE_KEY, "## Step 1: 一回目")
        await oracle.save_answer_prompt(ROLE_KEY, CUSTOM_ROLE)
        await oracle.save_answer_prompt(IMAGE_RETRIEVAL_PROMPT_KEY, CUSTOM_IMAGE)
        overrides = await oracle.answer_prompt_overrides()
        assert overrides[ROLE_KEY] == CUSTOM_ROLE
        assert overrides[IMAGE_RETRIEVAL_PROMPT_KEY] == CUSTOM_IMAGE
        saved = await oracle.list_answer_prompts()
        assert saved[ROLE_KEY]["updated_at"] is not None
    finally:
        assert await oracle.delete_answer_prompt(ROLE_KEY)
        assert await oracle.delete_answer_prompt(IMAGE_RETRIEVAL_PROMPT_KEY)
    assert ROLE_KEY not in await oracle.answer_prompt_overrides()
    assert not await oracle.delete_answer_prompt(ROLE_KEY)


def test_catalog_uses_current_image_template_with_runtime_rules() -> None:
    from rag_engine.adapters.oci import _picture_output_contract

    content = "## 独自の読み取り指示\n{{image_metadata}}"
    data = settings_routes._answer_prompts_data(
        {IMAGE_RETRIEVAL_PROMPT_KEY: {"content": content, "updated_at": None}}
    )
    stage = next(stage for stage in data.stages if stage.id == "picture_description")
    assert [part.id for part in stage.prompts] == [
        "system",
        "template",
        "rules",
        "picture",
        "table",
        "contextImage",
        "contextRecords",
        "contract",
    ]
    assert stage.prompts[1].content == content
    assert stage.prompts[2].content == default_prompt("picture_description.rules")
    assert stage.prompts[-1].content == _picture_output_contract()
    assert stage.prompts[-1].prompt_key is None
    assert (
        next(prompt.content for prompt in data.prompts if prompt.key == IMAGE_RETRIEVAL_PROMPT_KEY)
        == content
    )


@pytest.mark.parametrize(
    "key",
    [
        "external_ocr",
        "heading_levels",
        "field_extraction",
        "navigation_summary",
    ],
)
def test_feature_prompts_save_and_reset_with_their_screen_permission(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    auth = enable_production_auth(monkeypatch)
    for name, permission in [
        ("parser", "menu.settings_parser_adapters"),
        ("chunking", "menu.settings_chunking"),
        ("answer", "menu.settings_prompts"),
    ]:
        auth.user_with_permissions(name, [permission])
    headers = {name: login(client, name) for name in ("parser", "chunking", "answer")}
    owner = "parser"
    path = f"/api/settings/answer-prompts/{key}"
    for name in headers:
        response = client.put(path, json={"content": "合成の独自指示"}, headers=headers[name])
        assert response.status_code == (200 if name == owner else 403)
        if name != owner:
            assert client.delete(path, headers=headers[name]).status_code == 403
    data = client.get("/api/settings/answer-prompts", headers=headers[owner]).json()["data"]
    assert _prompt(data, key)["content"] == "合成の独自指示"
    assert _prompt(data, key)["customized"] is True
    assert any(
        part["content"] == "合成の独自指示" for stage in data["stages"] for part in stage["prompts"]
    )
    assert client.put(path, json={"content": " "}, headers=headers[owner]).status_code == 422
    reset = client.delete(path, headers=headers[owner]).json()["data"]
    assert _prompt(reset, key)["content"] == default_prompt(key)
    assert _prompt(reset, key)["customized"] is False


async def test_saved_summary_prompt_reaches_the_model_as_system_text() -> None:
    from typing import cast

    from app.rag.ingestion import IngestionPipeline
    from app.schemas.extraction import DocumentElement, StructuredExtraction

    calls: list[tuple[str, str | None]] = []

    class Oracle:
        async def answer_prompt_overrides(self) -> dict[str, str]:
            return {"navigation_summary": "独自の章節指示"}

    class Model:
        async def generate(self, prompt: str, context: str, **options: Any) -> str:
            calls.append((prompt, options.get("system_prompt")))
            return "合成の要約"

    pipeline = IngestionPipeline(
        oracle=cast(Any, Oracle()),
        vlm=cast(Any, Model()),
        settings=Settings(rag_navigation_summary_enabled=True),
    )
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="text", text="本文。" * 100, order=1, section_path=["合成の章"])
        ]
    )
    result = await pipeline._attach_navigation_tree("trace", extraction)
    assert result.navigation[0].summary == "合成の要約"
    # 段の指示はシステムの文として渡し、利用者側には文書の内容だけを渡す(#4776)。
    assert calls == [("", "独自の章節指示")]
    unsaved = IngestionPipeline(oracle=cast(Any, UneditedPromptOracle()), settings=Settings())
    assert await unsaved._stage_prompt("navigation_summary", "既定") == "既定"


@pytest.mark.parametrize("backend", ["docling", "dots_ocr"])
async def test_partition_loads_the_ocr_prompt_only_for_dots(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import cast

    from app.rag.ingestion import IngestionPipeline

    loads: list[str] = []
    seen: list[dict[str, Any]] = []

    class Oracle:
        async def answer_prompt_overrides(self) -> dict[str, str]:
            loads.append("loaded")
            return {"external_ocr": "合成の OCR 指示"}

    pipeline = IngestionPipeline(
        settings=Settings(rag_parser_adapter_backend=backend), oracle=cast(Any, Oracle())
    )

    def partition(**options: Any) -> Any:
        seen.append(options)
        raise RuntimeError("stop before parser")

    monkeypatch.setattr(pipeline, "_partition_source", partition)
    with pytest.raises(RuntimeError, match="stop before parser"):
        await pipeline._run_partition_with_progress(
            trace_id="trace",
            parse_bytes=b"synthetic",
            source_profile=None,
            content_type="application/pdf",
            attributes={},
            on_progress=None,
        )
    assert len(loads) == (1 if backend == "dots_ocr" else 0)
    assert seen[0].get("dots_ocr_prompt") == ("合成の OCR 指示" if backend == "dots_ocr" else None)


async def test_partition_cleans_progress_poll_if_prompt_loading_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from typing import cast

    from app.rag.ingestion import IngestionPipeline

    closed: list[bool] = []

    class Oracle:
        async def answer_prompt_overrides(self) -> dict[str, str]:
            await asyncio.sleep(0)
            raise asyncio.CancelledError

    async def poll(*args: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    pipeline = IngestionPipeline(
        settings=Settings(rag_parser_adapter_backend="dots_ocr"), oracle=cast(Any, Oracle())
    )
    monkeypatch.setattr(pipeline, "_poll_parse_progress", poll)
    with pytest.raises(asyncio.CancelledError):
        await pipeline._run_partition_with_progress(
            trace_id="trace",
            parse_bytes=b"synthetic",
            source_profile=None,
            content_type="application/pdf",
            attributes={},
            on_progress=lambda _: None,
        )
    assert closed == [True]
