"""#3753 の受入: 工程の指示の保存・既定復帰の実行への反映、要求単位の解決、権限と差し込み口の検証。

編集できる部分（生成の役割の文 generation.role、picture_description.contextImage /
contextRecords）を、API の保存から実行の組み立てまで通す。
生成の固定の部分と読み方は編集できない（#4782）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from rag_engine.adapters.oci import _render_prompt
from rag_engine.generation import grounded
from rag_engine.knowledge.prompt_files import DEFAULT_IMAGE_RETRIEVAL_PROMPT
from rag_engine.resources.runtime import bind_prompt_overrides

from app.api.routes import settings as settings_routes
from app.config import Settings
from app.rag.answer_engine import AnswerEngine
from app.rag.answer_prompts import default_prompt
from app.schemas.search import SearchRequest
from app.security.permissions import ANSWER_PROMPT_EDIT_PERMISSIONS
from tests.security_support import enable_production_auth, login
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm
from tests.test_answer_prompts import FakePromptOracle, _prompt, client

_ROLE = "generation.role"
_IMAGE = "picture_description.contextImage"
_RECORDS = "picture_description.contextRecords"
_SAVED = {
    _ROLE: "## Step 1: 合成の役割\n合成の役割で回答する。",
    _IMAGE: "### 合成の周辺の画像の案内\n枠を確認する。",
    _RECORDS: "### 合成の周辺の記録の案内\n前後の記録を確認する。",
}
_OWNER = {
    _ROLE: "menu.settings_prompts",
    _IMAGE: "menu.settings_parser_adapters",
    _RECORDS: "menu.settings_parser_adapters",
}


def _executed(key: str) -> str:
    """実行の経路でモデルへ渡す部分を組み立てる（該当する入力があるときの内容）。"""
    if key == _ROLE:
        return grounded.generation_system_prompt()
    metadata: dict[str, Any] = (
        {"image_inputs": [{}, {}]} if key == _IMAGE else {"next_record": {"text_preview": "合成"}}
    )
    return _render_prompt(metadata, template=DEFAULT_IMAGE_RETRIEVAL_PROMPT)


def _builtin_marker(key: str) -> str:
    return default_prompt(key).splitlines()[0]


@pytest.mark.parametrize("key", _SAVED)
def test_saved_then_reset_instruction_is_what_runs(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    path = f"/api/settings/answer-prompts/{key}"
    custom = _SAVED[key]

    saved = client.put(path, json={"content": custom})
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert _prompt(data, key)["content"] == custom
    assert _prompt(data, key)["customized"] is True
    assert custom in [
        part["content"] for stage in data["stages"] for part in stage["prompts"]
    ]  # 画面の表示
    overrides = {name: str(row["content"]) for name, row in fake.saved.items()}
    with bind_prompt_overrides(overrides):
        running = _executed(key)
    assert custom.splitlines()[1] in running
    assert _builtin_marker(key) not in running

    reset = client.delete(path)
    assert reset.status_code == 200
    assert _prompt(reset.json()["data"], key)["customized"] is False
    assert fake.saved == {}
    with bind_prompt_overrides({}):
        assert _builtin_marker(key) in _executed(key)
        assert custom.splitlines()[1] not in _executed(key)


@pytest.mark.parametrize("key", _SAVED)
def test_new_stage_parts_are_edited_only_with_the_screen_permission(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    auth = enable_production_auth(monkeypatch)
    owner = _OWNER[key]
    other = (
        "menu.settings_parser_adapters"
        if owner == "menu.settings_prompts"
        else "menu.settings_prompts"
    )
    assert ANSWER_PROMPT_EDIT_PERMISSIONS[key] == owner
    auth.user_with_permissions("owner-admin", [owner])
    auth.user_with_permissions("other-admin", [other])
    path = f"/api/settings/answer-prompts/{key}"
    denied = login(client, "other-admin")
    allowed = login(client, "owner-admin")
    assert client.put(path, json={"content": _SAVED[key]}, headers=denied).status_code == 403
    assert client.delete(path, headers=denied).status_code == 403
    assert fake.saved == {}
    assert client.put(path, json={"content": _SAVED[key]}, headers=allowed).status_code == 200
    assert client.delete(path, headers=allowed).status_code == 200


@pytest.mark.parametrize("key", _SAVED)
def test_new_stage_parts_reject_blank_content(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    assert (
        client.put(f"/api/settings/answer-prompts/{key}", json={"content": "  "}).status_code == 422
    )
    assert fake.saved == {}


@pytest.mark.parametrize("key", _SAVED)
async def test_concurrent_requests_resolve_their_own_saved_instruction(key: str) -> None:
    start = asyncio.Barrier(2)

    async def request(line: str) -> str:
        body = _SAVED[key].replace("合成", line)
        with bind_prompt_overrides({key: body}):
            await start.wait()
            return await asyncio.to_thread(_executed, key)

    first, second = await asyncio.gather(request("甲の"), request("乙の"))
    assert "甲の" in first and "乙の" not in first
    assert "乙の" in second and "甲の" not in second
    assert "甲の" not in _executed(key)


async def test_concurrent_answers_resolve_the_saved_instruction_of_each_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同時の回答の要求ごとに、保存値を読んだ内容だけが生成の入力に入る。"""
    import rag_engine.adapters.oci as engine_oci

    sent: list[tuple[str, str]] = []

    def llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
        sent.append((system, prompt))
        return _fake_llm(system, prompt, settings, schema, **options)

    monkeypatch.setattr(engine_oci, "parse_text_response", llm)

    async def run(question: str, marker: str) -> None:
        oracle = FakeOracle()
        oracle.prompt_overrides = {_ROLE: f"## Step 1: 役割\n{marker} で回答する。\n"}
        await AnswerEngine(
            Settings(),
            oracle=oracle,  # type: ignore[arg-type]
            genai=FakeGenAi(),  # type: ignore[arg-type]
        ).run(SearchRequest(query=question))

    await asyncio.gather(
        run("受注の登録方法は？", "合成の役割X"), run("受注の登録手順は？", "合成の役割Y")
    )
    drafts_x = [s for s, p in sent if "受注の登録方法は？" in p and "合成の役割" in s]
    drafts_y = [s for s, p in sent if "受注の登録手順は？" in p and "合成の役割" in s]
    assert drafts_x and all("合成の役割X" in s and "合成の役割Y" not in s for s in drafts_x)
    assert drafts_y and all("合成の役割Y" in s and "合成の役割X" not in s for s in drafts_y)
