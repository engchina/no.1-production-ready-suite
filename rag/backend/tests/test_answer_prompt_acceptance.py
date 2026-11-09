"""#3753 の受入: 工程の指示の保存・既定復帰の実行への反映、要求単位の解決、権限と差し込み口の検証。

#4678・#4675 で増えた段（generation.evidenceReading / guideRules、
picture_description.contextImage / contextRecords）を、API の保存から実行の組み立てまで通す。
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
from app.rag.answer_prompts import default_prompt, validate_prompt
from app.schemas.search import SearchRequest
from app.security.permissions import ANSWER_PROMPT_EDIT_PERMISSIONS
from tests.security_support import enable_production_auth, login
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm
from tests.test_answer_prompts import FakePromptOracle, _prompt, client

_EVIDENCE = "generation.evidenceReading"
_GUIDE = "generation.guideRules"
_IMAGE = "picture_description.contextImage"
_RECORDS = "picture_description.contextRecords"
_SAVED = {
    _EVIDENCE: "### 合成の根拠の読み方\n- image_generated: 合成の読み方。",
    _GUIDE: "### 合成のガイドの扱い\n- 業務ガイド: 合成のガイドの扱い。",
    _IMAGE: "### 合成の周辺の画像の案内\n枠を確認する。",
    _RECORDS: "### 合成の周辺の記録の案内\n前後の記録を確認する。",
}
_OWNER = {
    _EVIDENCE: "menu.settings_prompts",
    _GUIDE: "menu.settings_prompts",
    _IMAGE: "menu.settings_parser_adapters",
    _RECORDS: "menu.settings_parser_adapters",
}


def _executed(key: str) -> str:
    """実行の経路でモデルへ渡す部分を組み立てる（該当する入力があるときの内容）。"""
    if key == _EVIDENCE:
        tagged = [
            {
                "evidence_id": "id-1",
                "label": "E1",
                "source_id": "p1",
                "source": "synthetic.pdf",
                "text": "合成の原文。",
                "function": ("synthetic.pdf", "合成の説明"),
                "tags": ["image_generated"],
            }
        ]
        return grounded.evidence_reading_rules(tagged, attached_images=False)
    if key == _GUIDE:
        return grounded.guide_rules_context(scope=True, notices=True)
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


# 「- キー: 説明」の行だけが該当の入力で渡されるので、行が無い・キーが無い内容は保存を止める。
@pytest.mark.parametrize("key", [_EVIDENCE, _GUIDE])
@pytest.mark.parametrize(
    "content",
    ["### 合成の読み方\n根拠は前から順に読む。", "### 合成の読み方\n- 合成の注意: 順に読む。"],
    ids=["free-text", "unknown-key"],
)
def test_keyed_line_prompts_reject_content_that_would_never_run(
    monkeypatch: pytest.MonkeyPatch, key: str, content: str
) -> None:
    fake = FakePromptOracle()
    monkeypatch.setattr(settings_routes, "OracleClient", lambda: fake)
    response = client.put(f"/api/settings/answer-prompts/{key}", json={"content": content})
    assert response.status_code == 422
    assert "キー" in response.json()["error_messages"][0]
    assert fake.saved == {}
    with pytest.raises(ValueError, match="キー"):
        validate_prompt(key, content)


@pytest.mark.parametrize("key", [_EVIDENCE, _GUIDE])
def test_keyed_line_prompts_accept_known_keys_and_free_lines(key: str) -> None:
    validate_prompt(key, _SAVED[key] + "\n補足の説明の行。")
    validate_prompt(key, default_prompt(key))


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

    sent: list[str] = []

    def llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
        sent.append(prompt)
        return _fake_llm(system, prompt, settings, schema, **options)

    monkeypatch.setattr(engine_oci, "parse_text_response", llm)

    async def run(question: str, marker: str) -> None:
        oracle = FakeOracle()
        oracle.prompt_overrides = {
            _EVIDENCE: f"### 根拠の読み方\n- 補助情報: {marker} の読み方。\n",
        }
        await AnswerEngine(
            Settings(rag_answer_flow="standard_rag"),
            oracle=oracle,  # type: ignore[arg-type]
            genai=FakeGenAi(),  # type: ignore[arg-type]
        ).run(SearchRequest(query=question))

    await asyncio.gather(
        run("受注の登録方法は？", "合成の読み方X"), run("受注の登録手順は？", "合成の読み方Y")
    )
    drafts_x = [p for p in sent if "受注の登録方法は？" in p and "合成の読み方" in p]
    drafts_y = [p for p in sent if "受注の登録手順は？" in p and "合成の読み方" in p]
    assert drafts_x and all("合成の読み方X" in p and "合成の読み方Y" not in p for p in drafts_x)
    assert drafts_y and all("合成の読み方Y" in p and "合成の読み方X" not in p for p in drafts_y)
