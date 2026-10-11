"""回答で当たった chunk の章節から親の章節へ広げる（#1436）の backend 側。

設定・章節の読み込み・引用・診断を確かめる。章節と chunk の対応（#1438）は
``section_map_service`` の関数をスタブにし、LLM は決まった応答のスタブにする
（DB と OCI を呼ばない）。並列でも直列でも通るように、状態はテストごとに作る。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.clients.section_map_store import ReadableChunkSet, SectionChunkRow
from app.config import PROFILE_ONLY_SETTING_FIELDS, Settings
from app.rag import section_map_service
from app.rag.answer_engine import AnswerEngine, build_engine_settings
from app.rag.section_chunk_map import SectionNode
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle, _chunk, _fake_llm

SECTION_FIELDS = {
    "rag_section_expansion_enabled",
    "rag_section_expansion_max_levels",
    "rag_section_expansion_input_token_percent",
    "rag_section_expansion_time_limit_seconds",
    "rag_section_extraction_max_calls",
}


def _section_chunk(chunk_id: str, text: str, seq: int, page: int) -> RetrievedChunk:
    chunk = _chunk(chunk_id, text, seq)
    metadata = dict(chunk.metadata)
    # 章節の後ろのページの chunk は別の親（small_to_big の組）にする。
    metadata.update(
        chunk_group_id=f"chunk-docling-p{page:06d}",
        parent_text=text,
        page_start=page,
        page_end=page,
        chunk_set_id="cs-1",
        engine_metadata_json=json.dumps(
            {
                **json.loads(str(metadata["engine_metadata_json"])),
                "content_hash": f"s{seq}",
                "section_path": ["受注入力画面"],
            }
        ),
    )
    return chunk.model_copy(update={"metadata": metadata})


SECTION_TAIL = [
    _section_chunk("doc-1:c3", "受注の登録の後に、承認者が受注一覧で承認します。", 3, 2),
    _section_chunk("doc-1:c4", "承認の後は、受注の取り消しができません。", 4, 3),
]


@dataclass
class SectionMapStub:
    """章節の一覧（文書全体 > 受注入力画面）と章節の本文のスタブ。"""

    reads: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    outline_calls: int = 0

    def sections(self) -> list[SectionNode]:
        return [
            SectionNode("document", "manual.pdf", 0, 0, None, 1, 3, chunk_count=4, chars=200),
            SectionNode("s1", "受注入力画面", 1, 1, "document", 1, 3, chunk_count=4, chars=200),
        ]

    async def chunk_section(
        self, document_id: str, chunk_id: str, *, oracle: Any = None
    ) -> tuple[str, str] | None:
        return ("cs-1", "s1") if document_id == "doc-1" else None

    async def section_outline(
        self, document_id: str, *, chunk_set_id: str | None = None, oracle: Any = None
    ) -> section_map_service.SectionOutline:
        self.outline_calls += 1
        return section_map_service.SectionOutline(
            chunk_set=ReadableChunkSet("doc-1", "cs-1", "manual.pdf", False),
            sections=self.sections(),
        )

    async def read_section(
        self,
        document_id: str,
        section_id: str,
        *,
        chunk_set_id: str | None = None,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
        oracle: Any = None,
        **_: Any,
    ) -> section_map_service.SectionText:
        self.reads.append((section_id, dict(filters or {})))
        oracle_chunks = [
            _chunk("doc-1:c1", "受注番号を入力し、登録ボタンを押します。", 1),
            _chunk("doc-1:c2", "登録後は受注一覧に表示されます。", 2),
        ]
        rows = [
            SectionChunkRow(order, chunk)
            for order, chunk in enumerate([*oracle_chunks, *SECTION_TAIL])
        ]
        outline = await self.section_outline(document_id)
        node = outline.get(section_id)
        assert node is not None
        return section_map_service.SectionText(
            outline=outline, section=node, chunks=rows, complete=True
        )


@pytest.fixture
def section_map(monkeypatch: pytest.MonkeyPatch) -> SectionMapStub:
    stub = SectionMapStub()
    monkeypatch.setattr(section_map_service, "chunk_section", stub.chunk_section)
    monkeypatch.setattr(section_map_service, "section_outline", stub.section_outline)
    monkeypatch.setattr(section_map_service, "read_section", stub.read_section)
    return stub


def _engine(**settings: Any) -> AnswerEngine:
    return AnswerEngine(
        Settings(rag_domain_keywords=["受注番号"], **settings),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import rag_engine.adapters.oci as engine_oci

    # CRAG の判定は候補の関連を返さない（不足）ので、章節を広げる。
    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)


@pytest.mark.usefixtures("stub_llm")
async def test_answer_reads_the_section_of_the_hit_chunk_and_cites_chunks(
    section_map: SectionMapStub,
) -> None:
    request = SearchRequest(query="受注の登録方法は？", filters={"knowledge_base_id": "kb-1"})

    outcome = await _engine().run(request)

    # 章節の本文は回答の検索と同じ条件（ナレッジベース）で読む。
    assert section_map.reads and section_map.reads[0][0] == "s1"
    assert section_map.reads[0][1]["knowledge_base_id"] == "kb-1"
    # 章節の chunk は chunk の ID のまま引用に入り、役割は章節の文脈。
    roles = {chunk.chunk_id: chunk.metadata.get("evidence_role") for chunk in outcome.citations}
    assert roles.get("doc-1:c3") == "section_context"
    assert roles.get("doc-1:c4") == "section_context"
    # 当たった chunk（最も関連の高い根拠）は引用の先頭のまま。
    assert outcome.citations[0].chunk_id == "doc-1:c1"
    expansion = outcome.diagnostics["section_expansion"]
    assert expansion["applied"] is True
    assert expansion["levels_read"] >= 1
    assert expansion["used_tokens"] <= expansion["input_token_limit"]
    assert "grades" not in expansion
    # 章節の一覧は 1 回の回答で 1 回だけ読む（読むたびの確認の分を除く）。
    assert section_map.outline_calls >= 1


@pytest.mark.usefixtures("stub_llm")
async def test_max_levels_limit_makes_the_answer_conditional(
    section_map: SectionMapStub,
) -> None:
    outcome = await _engine(rag_section_expansion_max_levels=1).run(
        SearchRequest(query="受注の登録方法は？")
    )

    expansion = outcome.diagnostics["section_expansion"]
    assert expansion["stop_reason"] == "max_levels"
    assert [item["section_id"] for item in expansion["unread"]] == ["document"]
    assert outcome.diagnostics["outcome"] == "conditional"
    assert "確かめられていない点" in outcome.answer
    assert "「manual.pdf」の全体" in outcome.answer


@pytest.mark.usefixtures("stub_llm")
async def test_disabled_profile_does_not_read_sections(section_map: SectionMapStub) -> None:
    outcome = await _engine(rag_section_expansion_enabled=False).run(
        SearchRequest(query="受注の登録方法は？")
    )

    assert section_map.reads == []
    assert outcome.diagnostics["section_expansion"] == {}
    assert all(
        chunk.metadata.get("evidence_role") != "section_context" for chunk in outcome.citations
    )


@pytest.mark.usefixtures("stub_llm")
async def test_answer_continues_when_section_map_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("ORA-00942")

    monkeypatch.setattr(section_map_service, "chunk_section", broken)

    outcome = await _engine().run(SearchRequest(query="受注の登録方法は？"))

    assert "登録ボタン" in outcome.answer
    assert outcome.diagnostics["section_expansion"]["stop_reason"] == "no_sections"


def test_section_expansion_settings_are_profile_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """拡張の開閉と上限は検索・回答プロファイルで選ぶ。環境変数 / .env は読まない（#1436）。"""
    assert SECTION_FIELDS <= PROFILE_ONLY_SETTING_FIELDS
    monkeypatch.setenv("RAG_SECTION_EXPANSION_ENABLED", "false")
    env_file = tmp_path / "backend.env"
    env_file.write_text("RAG_SECTION_EXPANSION_MAX_LEVELS=2\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.rag_section_expansion_enabled is True
    assert settings.rag_section_expansion_max_levels == 4
    assert settings.rag_section_expansion_input_token_percent == 50
    assert settings.rag_section_expansion_time_limit_seconds == 600.0
    assert settings.rag_section_extraction_max_calls == 8


def test_search_answer_profile_selects_section_expansion_limits() -> None:
    from app.rag.search_answer_profile_config import (
        parse_search_answer_profile_config,
        resolve_search_answer_profile_settings,
    )

    base = Settings()
    selected, applied = resolve_search_answer_profile_settings(
        base,
        parse_search_answer_profile_config(
            {
                "knowledge_base_ids": ["kb-1"],
                "query": {
                    "section_expansion_enabled": False,
                    "section_expansion_max_levels": 2,
                    "section_expansion_input_token_percent": 30,
                    "section_expansion_time_limit_seconds": 300,
                    "section_extraction_max_calls": 4,
                },
            }
        ),
    )

    assert applied is True
    assert (
        selected.rag_section_expansion_enabled,
        selected.rag_section_expansion_max_levels,
        selected.rag_section_expansion_input_token_percent,
        selected.rag_section_expansion_time_limit_seconds,
        selected.rag_section_extraction_max_calls,
    ) == (False, 2, 30, 300.0, 4)
    assert base.rag_section_expansion_enabled is True
    # 範囲の外の保存値は読まない。
    broken = parse_search_answer_profile_config(
        {"knowledge_base_ids": ["kb-1"], "query": {"section_expansion_max_levels": 9}}
    )
    assert broken.query.section_expansion_max_levels is None
    too_many = parse_search_answer_profile_config(
        {"knowledge_base_ids": ["kb-1"], "query": {"section_extraction_max_calls": 17}}
    )
    assert too_many.query.section_extraction_max_calls is None


def test_build_engine_settings_passes_section_expansion(tmp_path: Any) -> None:
    default = build_engine_settings(Settings(), output_dir=tmp_path)
    custom = build_engine_settings(
        Settings(
            rag_section_expansion_enabled=False,
            rag_section_expansion_max_levels=2,
            rag_section_expansion_input_token_percent=30,
            rag_section_expansion_time_limit_seconds=500,
            rag_answer_timeout_seconds=300,
        ),
        output_dir=tmp_path,
    )

    assert (default.section_expansion_enabled, default.section_expansion_max_levels) == (True, 4)
    assert default.section_expansion_input_token_percent == 50
    assert default.section_expansion_time_limit_seconds == 600.0
    assert (custom.section_expansion_enabled, custom.section_expansion_max_levels) == (False, 2)
    assert custom.section_expansion_input_token_percent == 30
    # 回答の通しの上限より長くしない。
    assert custom.section_expansion_time_limit_seconds == 300.0
    assert default.section_extraction_max_calls == 8


def test_build_engine_settings_passes_model_input_limit_and_extraction_calls(tmp_path: Any) -> None:
    """回答のモデルの入力の上限は、共通のモデル設定の登録モデルの値（#1437。既定 128000）。"""
    from pr_system_settings.model import EnterpriseAiConfiguredModel

    models = [
        EnterpriseAiConfiguredModel(model_id="llm-a", max_input_tokens=200_000),
        EnterpriseAiConfiguredModel(model_id="llm-b"),
    ]
    settings = Settings(
        oci_enterprise_ai_models=models,
        oci_enterprise_ai_default_text_model="llm-a",
        rag_section_extraction_max_calls=4,
    )

    engine = build_engine_settings(settings, output_dir=tmp_path)
    other = build_engine_settings(settings, output_dir=tmp_path, answer_model_id="llm-b")

    assert engine.answer_max_input_tokens == 200_000
    assert other.answer_max_input_tokens == 128_000
    assert engine.section_extraction_max_calls == 4
    assert build_engine_settings(Settings(), output_dir=tmp_path).answer_max_input_tokens == 128_000


def test_token_estimate_and_default_limit_match_platform() -> None:
    """rag_engine の見積もり・既定は platform の値の解釈と同じ（3 製品で共通の 1 か所。#1437）。"""
    from pr_system_settings.model import (
        DEFAULT_ENTERPRISE_AI_MAX_INPUT_TOKENS,
        estimate_input_tokens,
    )
    from rag_engine.generation.section_expansion import (
        DEFAULT_MODEL_INPUT_TOKENS,
        estimate_tokens,
    )

    assert DEFAULT_MODEL_INPUT_TOKENS == DEFAULT_ENTERPRISE_AI_MAX_INPUT_TOKENS
    for text in ["", "点検", "abcd", "abcde", "UPS の点検", "手順 1: 電源を切る。\n" * 30]:
        assert estimate_tokens(text) == estimate_input_tokens(text)


def test_evaluation_overrides_switch_section_expansion() -> None:
    from app.rag.evaluation import evaluation_settings
    from app.schemas.evaluation import EvaluationRagOverrides

    settings = evaluation_settings(
        Settings(),
        EvaluationRagOverrides(section_expansion_enabled=False, section_extraction_max_calls=2),
    )

    assert settings.rag_section_expansion_enabled is False
    assert settings.rag_section_extraction_max_calls == 2


# ---------------------------------------------------------------- 抽出してから統合（#1437）

APPROVAL = "承認者は受注一覧で対象を選び、承認ボタンを押します。"
FILLER = "この段落は画面の項目の並びについての説明で、承認の手順には関係しません。"


def _long_text(page: int) -> str:
    """入力の上限に入らない章節の 1 頁（承認の手順の 1 文と、関係の無い説明の段落）。"""
    filler = FILLER * 25
    return f"{filler}\n\n{APPROVAL}\n\n{filler}"


@dataclass
class LongSectionStub(SectionMapStub):
    """受注入力画面の章節が長い（頁 2〜9 の 8 件。モデルの入力の上限 4096 の半分に入らない）。"""

    def sections(self) -> list[SectionNode]:
        return [
            SectionNode("document", "manual.pdf", 0, 0, None, 1, 9, chunk_count=10, chars=20_000),
            SectionNode("s1", "受注入力画面", 1, 1, "document", 1, 9, chunk_count=10, chars=20_000),
        ]

    async def read_section(self, document_id: str, section_id: str, **kwargs: Any) -> Any:
        self.reads.append((section_id, dict(kwargs.get("filters") or {})))
        chunks = [
            _chunk("doc-1:c1", "受注番号を入力し、登録ボタンを押します。", 1),
            _chunk("doc-1:c2", "登録後は受注一覧に表示されます。", 2),
            *(
                _section_chunk(f"doc-1:l{page}", _long_text(page), 10 + page, page)
                for page in range(2, 10)
            ),
        ]
        outline = await self.section_outline(document_id)
        node = outline.get(section_id)
        assert node is not None
        return section_map_service.SectionText(
            outline=outline,
            section=node,
            chunks=[SectionChunkRow(order, chunk) for order, chunk in enumerate(chunks)],
            complete=True,
        )


@pytest.fixture
def long_section(monkeypatch: pytest.MonkeyPatch) -> LongSectionStub:
    stub = LongSectionStub()
    monkeypatch.setattr(section_map_service, "chunk_section", stub.chunk_section)
    monkeypatch.setattr(section_map_service, "section_outline", stub.section_outline)
    monkeypatch.setattr(section_map_service, "read_section", stub.read_section)
    return stub


async def test_long_section_is_extracted_then_answered_with_verbatim_quotes(
    monkeypatch: pytest.MonkeyPatch, long_section: LongSectionStub
) -> None:
    import re

    import rag_engine.adapters.oci as engine_oci
    from pr_system_settings.model import EnterpriseAiConfiguredModel
    from rag_engine.models.llm import (
        GroundedDraft,
        SectionExtractionOutput,
        SectionExtractionQuote,
    )

    prompts: dict[str, list[str]] = {"extract": [], "draft": []}
    block = re.compile(r"^\[(C\d+)[^\]\n]*\]\n(.*?)(?=\n\n\[C\d+|\n\n</資料>)", re.M | re.S)

    def llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
        if schema is SectionExtractionOutput:
            prompts["extract"].append(prompt)
            quotes = [
                SectionExtractionQuote(chunk_ref=ref, location="", quote=sentence)
                for ref, text in block.findall(prompt)
                for sentence in re.findall(r"[^。\n]+。", text)
                if "承認ボタン" in sentence
            ]
            # 作られた引用（原文に無い）も混ぜる。照合で捨てる。
            quotes.append(
                SectionExtractionQuote(chunk_ref="C1", location="", quote="承認は不要です。")
            )
            return SectionExtractionOutput(quotes=quotes)
        if schema is GroundedDraft:
            prompts["draft"].append(prompt)
        return _fake_llm(system, prompt, settings, schema, **options)

    monkeypatch.setattr(engine_oci, "parse_text_response", llm)
    engine = AnswerEngine(
        Settings(
            rag_domain_keywords=["受注番号"],
            oci_enterprise_ai_models=[
                EnterpriseAiConfiguredModel(model_id="llm-a", max_input_tokens=4096)
            ],
            oci_enterprise_ai_default_text_model="llm-a",
        ),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    outcome = await engine.run(SearchRequest(query="受注の承認の手順は？"))

    expansion = outcome.diagnostics["section_expansion"]
    entry = expansion["levels"][0]["sections"][0]
    assert entry["read"] == "extracted"
    assert expansion["large_section_handoff"] == "extract_then_integrate"
    # 抽出の呼び出しは上限（既定 8 回）の中で、作られた引用は捨てる。
    assert 1 <= len(prompts["extract"]) <= 8
    assert entry["extraction"]["dropped_quotes"] == len(prompts["extract"])
    assert entry["extraction"]["quotes"] == 8
    # 根拠の入力は、モデルの入力の上限（4096）の 50% の中。
    assert expansion["input_token_limit"] == 2048
    assert expansion["used_tokens"] <= expansion["input_token_limit"]
    # 回答の生成に渡す原文は、抜き出した原文だけ（関係の無い説明の段落は渡さない）。
    assert prompts["draft"] and APPROVAL in prompts["draft"][0]
    assert FILLER not in prompts["draft"][0]
    assert "登録ボタン" in outcome.answer
