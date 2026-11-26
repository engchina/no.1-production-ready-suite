"""組み込みの業務 Agent（10 個。#1573・#5342・#5837・#5867）と、組み込みのスキルの形。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest
from checkpoint_support import KEY, CheckpointTable, export_state, load_state

from app.features.agent.builtin_runtime import agent_mcp_requirements, agent_tool_names
from app.features.agent.data_scope import AgentDataScope
from app.features.agent.docx_tool import DOCX_CREATE_TOOL
from app.features.agent.markdown_tool import MARKDOWN_CREATE_TOOL
from app.features.agent.pptx_tool import PPTX_CREATE_TOOL
from app.features.agent.runtime import (
    BUILTIN_AGENT_IDS,
    RETIRED_RAG_RESEARCH_AGENT_ID,
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeRepository,
    AgentRuntimeSnapshot,
    AgentVersion,
    RunCreateRequest,
    agent_for_run,
)
from app.features.agent.skills import (
    DOCX_CREATE_SKILL_ID,
    MARKDOWN_CREATE_SKILL_ID,
    PPTX_CREATE_SKILL_ID,
    RAG_CHAT_AND_DATA_SKILL_ID,
    RAG_CHAT_SKILL_ID,
    RAG_SEARCH_AND_DATA_SKILL_ID,
    RAG_SEARCH_SKILL_ID,
    STRUCTURED_DATA_QUERY_SKILL_ID,
    XLSX_CREATE_SKILL_ID,
    FinalAnswerTool,
    pin_skill,
    skill_registry,
)
from app.features.agent.templates import AGENT_TEMPLATES
from app.features.agent.xlsx_tool import XLSX_CREATE_TOOL

LEGACY_DEFAULT_NAME = "汎用業務 Agent"
# #5342 より前の組み込みの内容（スキルは消した）。
PREVIOUS_DEFAULT: dict[str, Any] = {
    "name": "資料・データ横断アシスタント",
    "description": (
        "社内の資料（業務 RAG）と業務データ（NL2SQL）を、問いに合わせて使い分けて"
        "調べる既定の業務 Agent。"
    ),
    "instructions": (
        "問いと途中の結果から、資料の根拠と業務データの値のどちらが要るかを判断して"
        "ツールを呼ぶ。根拠と監査情報を残す。"
    ),
    "skill_ids": ["rag-and-structured-data", "business-rag-research", "structured-data-query"],
}
PREVIOUS_RAG_RESEARCH: dict[str, Any] = {
    "name": "資料調査アシスタント",
    "description": "社内の資料（業務 RAG）を調べ、根拠の文書と場所を示して答える業務 Agent。",
    "instructions": "資料の根拠に基づいて答え、根拠の文書名と場所を示す。",
    "skill_ids": ["business-rag-research"],
}


def _agent(repository: AgentRuntimeRepository, agent_id: str) -> AgentProfile:
    return next(item for item in repository.list_agents() if item.id == agent_id)


def _builtin(agent_id: str, content: Mapping[str, Any]) -> AgentProfile:
    return AgentProfile(
        id=agent_id,
        source="builtin",
        versioned=True,
        versions=[AgentVersion(version=1, **content)],
        published_version=1,
        **content,
    )


# #5837 のファイルを作るスキル（#5867 より前に保存した組み込みの業務 Agent が持つ）。
PREVIOUS_FILE_CREATE_SKILL_IDS = [XLSX_CREATE_SKILL_ID, DOCX_CREATE_SKILL_ID, PPTX_CREATE_SKILL_ID]
FILE_CREATE_SKILL_IDS = [*PREVIOUS_FILE_CREATE_SKILL_IDS, MARKDOWN_CREATE_SKILL_ID]
FILE_CREATE_TOOLS = [XLSX_CREATE_TOOL, DOCX_CREATE_TOOL, PPTX_CREATE_TOOL, MARKDOWN_CREATE_TOOL]
SOURCE_CHOICE_STEP = (
    "## Step 1: 必要な情報源を選ぶ\n"
    "問いと途中の結果から、資料と業務データのどちらが要るかを判断してツールを呼ぶ。"
)
# #5837 より前からある 5 つ（名前・説明・指示・スキルを変えない）。
ORIGINAL_BUILTIN: dict[str, dict[str, Any]] = {
    "default": {
        "name": "RAG チャット＋データアシスタント",
        "description": (
            "社内の資料（RAG のチャットと同じ流れ）と業務データ（NL2SQL）を、問いに合わせて"
            "使い分けて調べる既定の業務 Agent。"
        ),
        "instructions": SOURCE_CHOICE_STEP,
        "skill_ids": [RAG_CHAT_AND_DATA_SKILL_ID],
    },
    "rag-chat": {
        "name": "RAG チャットアシスタント",
        "description": (
            "社内の資料を RAG のチャットと同じ流れ（登録済みQ&A・業務ガイド・根拠の拡張）で"
            "調べ、RAG の回答と根拠をそのまま返す業務 Agent。"
        ),
        "instructions": (
            "## Step 1: 資料をチャットで調べる\n資料の質問に、RAG のチャットの流れで答える。"
        ),
        "skill_ids": [RAG_CHAT_SKILL_ID],
    },
    "rag-search": {
        "name": "RAG 検索アシスタント",
        "description": (
            "社内の資料を基本の検索（登録済みQ&A・業務ガイド・根拠の拡張なし）で調べ、RAG の"
            "回答と根拠をそのまま返す業務 Agent。"
        ),
        "instructions": "## Step 1: 資料を検索する\n資料の質問に、RAG の基本の検索で答える。",
        "skill_ids": [RAG_SEARCH_SKILL_ID],
    },
    "rag-search-data": {
        "name": "RAG 検索＋データアシスタント",
        "description": (
            "社内の資料（基本の検索）と業務データ（NL2SQL）を、問いに合わせて使い分けて調べる"
            "業務 Agent。"
        ),
        "instructions": SOURCE_CHOICE_STEP,
        "skill_ids": [RAG_SEARCH_AND_DATA_SKILL_ID],
    },
    "data-query": {
        "name": "データ照会アシスタント",
        "description": "業務データ（NL2SQL）を照会し、集計の条件と結果を示して答える業務 Agent。",
        "instructions": (
            "## Step 1: 業務データの結果を示す\n"
            "業務データの照会の結果に基づいて答え、集計の条件（期間・対象）を示す。"
        ),
        "skill_ids": [STRUCTURED_DATA_QUERY_SKILL_ID],
    },
}
# ファイルを作る 5 つ（#5837・#5867）。名前とスキルは決めたもの（説明・指示の文は固定しない）。
FILE_BUILTIN: dict[str, tuple[str, list[str]]] = {
    "doc-create": ("文書作成アシスタント", FILE_CREATE_SKILL_IDS),
    "rag-search-data-doc-create": (
        "RAG 検索＋データ＋文書作成アシスタント",
        [RAG_SEARCH_AND_DATA_SKILL_ID, *FILE_CREATE_SKILL_IDS],
    ),
    "rag-chat-data-doc-create": (
        "RAG チャット＋データ＋文書作成アシスタント",
        [RAG_CHAT_AND_DATA_SKILL_ID, *FILE_CREATE_SKILL_IDS],
    ),
    # RAG だけ＋文書作成（#5867。データつきの 2 つから NL2SQL を除いた構成）。
    "rag-search-doc-create": (
        "RAG 検索＋文書作成アシスタント",
        [RAG_SEARCH_SKILL_ID, *FILE_CREATE_SKILL_IDS],
    ),
    "rag-chat-doc-create": (
        "RAG チャット＋文書作成アシスタント",
        [RAG_CHAT_SKILL_ID, *FILE_CREATE_SKILL_IDS],
    ),
}
# #5837 で足した 3 つの、#5867 より前の内容（スキルはファイルを作る 3 つ）。
PREVIOUS_FILE_BUILTIN: dict[str, list[str]] = {
    "doc-create": PREVIOUS_FILE_CREATE_SKILL_IDS,
    "rag-search-data-doc-create": [RAG_SEARCH_AND_DATA_SKILL_ID, *PREVIOUS_FILE_CREATE_SKILL_IDS],
    "rag-chat-data-doc-create": [RAG_CHAT_AND_DATA_SKILL_ID, *PREVIOUS_FILE_CREATE_SKILL_IDS],
}


def _content(agent: AgentProfile | AgentVersion) -> dict[str, Any]:
    return {
        "name": agent.name,
        "description": agent.description,
        "instructions": agent.instructions,
        "skill_ids": agent.skill_ids,
    }


def test_new_environment_has_ten_builtin_agents() -> None:
    repository = AgentRuntimeRepository()

    agents = repository.list_agents()
    expected = [*ORIGINAL_BUILTIN, *FILE_BUILTIN]
    assert [agent.id for agent in agents] == expected
    assert list(BUILTIN_AGENT_IDS) == expected
    # 既定の業務 Agent（Run の `agent_id` の既定）は先頭の default のまま。
    assert RunCreateRequest(goal="質問").agent_id == agents[0].id == "default"
    by_id = {agent.id: agent for agent in agents}
    for agent_id, content in ORIGINAL_BUILTIN.items():
        assert _content(by_id[agent_id]) == content, agent_id
    for agent_id, (name, skill_ids) in FILE_BUILTIN.items():
        assert (by_id[agent_id].name, by_id[agent_id].skill_ids) == (name, skill_ids), agent_id
    assert len({agent.name for agent in agents}) == len(agents)
    for agent in agents:
        # 公開した版があり、利用者がすぐ使える（チャット・自動実行で選べる）。
        assert agent.source == "builtin"
        assert agent.enabled is True
        assert agent.published_version == 1
        assert agent.unpublished_changes is False
        assert agent.unavailable_skill_ids == []
        # モデルは既定、データの範囲は空（利用者が使えるすべてのプロファイルから選ぶ）。
        assert (agent.model_id, agent.data_scopes) == ("", {})
        run = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent.id))
        assert run.metadata["agent_version"] == 1


def test_builtin_agents_use_mcp_connections_of_their_skills() -> None:
    repository = AgentRuntimeRepository()

    def requirements(agent_id: str) -> dict[str, set[str] | None]:
        return agent_mcp_requirements(_agent(repository, agent_id).skill_ids)

    def control_plane_tools(agent_id: str) -> list[str]:
        return agent_tool_names(_agent(repository, agent_id).skill_ids)

    assert requirements("rag-chat")["rag"] == {
        "rag_chat",
        "rag_recommend_search_answer_profile",
        "rag_list_search_answer_profiles",
    }
    assert set(requirements("rag-search")) == {"rag"}
    assert "rag_search" in (requirements("rag-search")["rag"] or set())
    assert set(requirements("data-query")) == {"nl2sql"}
    for agent_id, tool in (("default", "rag_chat"), ("rag-search-data", "rag_search")):
        combined = requirements(agent_id)
        assert set(combined) == {"rag", "nl2sql"}
        assert tool in (combined["rag"] or set())
        assert {"nl2sql_query", "nl2sql_get_job"} <= (combined["nl2sql"] or set())
    # ファイルを作るツールは、ファイルを作る 5 つの業務 Agent にだけ渡る（#5837・#5867）。
    for agent_id in ORIGINAL_BUILTIN:
        assert control_plane_tools(agent_id) == [], agent_id
    for agent_id in FILE_BUILTIN:
        assert control_plane_tools(agent_id) == FILE_CREATE_TOOLS, agent_id
    # 文書作成アシスタントは MCP 接続（RAG・NL2SQL）を使わない。
    assert requirements("doc-create") == {}
    # 調べるスキルは対応する業務 Agent と同じ（使う接続とツールが同じ）。
    for agent_id, base in (
        ("rag-search-data-doc-create", "rag-search-data"),
        ("rag-chat-data-doc-create", "default"),
        ("rag-search-doc-create", "rag-search"),
        ("rag-chat-doc-create", "rag-chat"),
    ):
        assert requirements(agent_id) == requirements(base), agent_id
    # RAG だけ＋文書作成は NL2SQL を使わない（#5867）。
    assert set(requirements("rag-search-doc-create")) == {"rag"}
    assert set(requirements("rag-chat-doc-create")) == {"rag"}


def test_file_builtin_agent_instructions_are_step_markdown() -> None:
    """ファイルを作る 5 つの指示は Step 形式で、調べてからファイルにする順が分かる（#5837）。"""
    repository = AgentRuntimeRepository()
    steps = {
        agent_id: re.findall(
            r"^## Step (\d+): (\S.*)$", _agent(repository, agent_id).instructions, re.M
        )
        for agent_id in FILE_BUILTIN
    }
    for agent_id, found in steps.items():
        headings = re.findall(r"^#+ .*$", _agent(repository, agent_id).instructions, re.M)
        assert len(found) == len(headings), (agent_id, headings)
        assert [int(number) for number, _ in found] == list(range(1, len(found) + 1)), agent_id
    assert len(steps["doc-create"]) == 1
    for agent_id, base in (
        ("rag-search-data-doc-create", "rag-search-data"),
        ("rag-chat-data-doc-create", "default"),
        ("rag-search-doc-create", "rag-search"),
        ("rag-chat-doc-create", "rag-chat"),
    ):
        instructions = _agent(repository, agent_id).instructions
        # 調べる Step は対応する業務 Agent の指示と同じで、ファイルの Step がその後に続く。
        assert instructions.startswith(ORIGINAL_BUILTIN[base]["instructions"] + "\n\n## Step 2: ")
        assert len(steps[agent_id]) == 2
    assert (
        _agent(repository, "rag-search-data-doc-create").instructions
        == _agent(repository, "rag-chat-data-doc-create").instructions
    )


def _saved_original_builtin() -> list[AgentProfile]:
    """#5837 より前の保存先にある 5 つ（v1 を公開した状態）。"""
    return [_builtin(agent_id, content) for agent_id, content in ORIGINAL_BUILTIN.items()]


def test_environment_with_five_saved_builtin_agents_gets_the_missing_file_agents() -> None:
    """前の 5 つだけを保存した環境の読み込みで、足りないファイルを作る 5 つが入る（#5837・#5867）。

    保存済みの 5 つ（利用者が変えた内容・モデル・データの範囲）と、利用者が作った業務 Agent は
    変えない。
    """
    saved = _saved_original_builtin()
    changed = saved[3].model_copy(
        deep=True,
        update={
            "instructions": "利用者が変えた指示。",
            "model_id": "synthetic-model",
            "data_scopes": {"rag": AgentDataScope(profile_ids=["profile-a"])},
        },
    )
    custom = AgentProfile(
        id="agent_custom", name="利用者が作った業務 Agent", skill_ids=[RAG_SEARCH_SKILL_ID]
    )
    before = [saved[0], saved[1], saved[2], changed, saved[4], custom]
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=before))

    agents = {agent.id: agent for agent in repository.list_agents()}
    assert set(agents) == {*BUILTIN_AGENT_IDS, "agent_custom"}
    for item in before:
        kept = agents[item.id]
        assert _content(kept) == _content(item), item.id
        assert (kept.model_id, kept.data_scopes) == (item.model_id, item.data_scopes), item.id
        assert kept.created_at == item.created_at, item.id
    assert agents["rag-search-data"].published_version == 1
    assert agents["agent_custom"].source == "runtime"
    for agent_id, (name, skill_ids) in FILE_BUILTIN.items():
        added = agents[agent_id]
        assert (added.name, added.skill_ids, added.source) == (name, skill_ids, "builtin")
        assert added.published_version == 1 and added.unpublished_changes is False
        run = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent_id))
        assert run.metadata["agent_version"] == 1
        with pytest.raises(ValueError, match="組み込みの業務 Agent は削除できません。"):
            repository.delete_agent(agent_id)
    # 保存し直して読み込んでも、数も内容も変わらない（冪等）。
    exported = export_state(repository)
    load_state(repository, exported)
    assert export_state(repository).agents == exported.agents


def test_missing_builtin_agents_are_added_on_storage_load_and_keep_user_changes() -> None:
    """保存先（1 件 = 1 行）に前の 5 つだけがある環境の起動（#5837）。

    起動は保存先を書き換えずに足りない 5 つをメモリに足し、利用者が変えたものだけを保存する。
    次の起動は保存した内容を使う（組み込みの内容で上書きしない）。
    """
    custom = AgentProfile(id="agent_custom", name="利用者が作った業務 Agent")
    table = CheckpointTable()
    table.seed([], [*_saved_original_builtin(), custom])
    stored_before = deepcopy(table.entities)

    first = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)

    assert {agent.id for agent in first.list_agents()} == {*BUILTIN_AGENT_IDS, "agent_custom"}
    # 読み込みだけでは保存先を書かない（保存済みの 5 つと利用者の業務 Agent の行はそのまま）。
    assert table.entities == stored_before and table.version() == 0

    first.patch_agent(
        "doc-create",
        AgentProfilePatch(instructions="利用者が変えた指示。", model_id="synthetic-model"),
    )
    first.flush_writes()

    # 変えた 1 件だけを書く（ほかの行は変えない）。
    assert set(table.entities) - set(stored_before) == {(KEY, "agent", "doc-create")}
    assert {key: table.entities[key] for key in stored_before} == stored_before
    second = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    agents = {agent.id: agent for agent in second.list_agents()}
    assert set(agents) == {*BUILTIN_AGENT_IDS, "agent_custom"}
    kept = agents["doc-create"]
    assert (kept.instructions, kept.model_id) == ("利用者が変えた指示。", "synthetic-model")
    assert kept.source == "builtin" and kept.skill_ids == FILE_CREATE_SKILL_IDS
    # 下書きを変えただけなので、公開中の版は組み込みの内容のまま。
    assert kept.published_version == 1 and kept.unpublished_changes is True
    for agent_id, content in ORIGINAL_BUILTIN.items():
        assert _content(agents[agent_id]) == content, agent_id


def test_rag_only_skills_declare_the_final_answer_tool() -> None:
    """RAG だけのスキルは RAG の回答をそのまま返す（データつきのスキルは宣言しない）。"""
    finals = {
        skill_id: (skill.final_answer_tools if (skill := skill_registry.get(skill_id)) else None)
        for skill_id in (
            RAG_CHAT_SKILL_ID,
            RAG_SEARCH_SKILL_ID,
            RAG_CHAT_AND_DATA_SKILL_ID,
            RAG_SEARCH_AND_DATA_SKILL_ID,
            STRUCTURED_DATA_QUERY_SKILL_ID,
            *FILE_CREATE_SKILL_IDS,
        )
    }
    assert finals == {
        RAG_CHAT_SKILL_ID: [FinalAnswerTool(server_id="rag", tool_name="rag_chat")],
        RAG_SEARCH_SKILL_ID: [FinalAnswerTool(server_id="rag", tool_name="rag_search")],
        RAG_CHAT_AND_DATA_SKILL_ID: [],
        RAG_SEARCH_AND_DATA_SKILL_ID: [],
        STRUCTURED_DATA_QUERY_SKILL_ID: [],
        # ファイルを作るスキルと組み合わせても、回答はモデルが書く（#5837）。
        **{skill_id: [] for skill_id in FILE_CREATE_SKILL_IDS},
    }


def test_data_skills_combine_without_fixing_the_order() -> None:
    for skill_id, tool in (
        (RAG_CHAT_AND_DATA_SKILL_ID, "rag_chat"),
        (RAG_SEARCH_AND_DATA_SKILL_ID, "rag_search"),
    ):
        skill = skill_registry.get(skill_id)
        assert skill is not None and skill.source == "builtin"
        # 識別子は Markdown の `code`（#1795）。規則の文は記号を除いて確かめる。
        instructions = skill.instructions.replace("`", "")
        for phrase in (
            "順序は決めない",
            "nl2sql_query が先でもよい",
            "何度でも呼んでよい",
            "それ以上ツールを呼ばずに答える",
            "値がどこに載っているかで決める",
            f"そこで終えずに資料（{tool}）を引く",
            "「資料からは確認できない」と答える前に nl2sql_query で引く",
            "資料で分かった基準を値の条件に置き換えて書く",
            # RAG の回答は言い換えず、業務データの値と分けて示す（#5342）。
            f"{tool} の answer を言い換えず",
        ):
            assert phrase in instructions, (skill_id, phrase)
        tools = {item.server_id: item.tool_names for item in skill.mcp_requirements}
        assert tools["rag"] == [
            tool,
            "rag_recommend_search_answer_profile",
            "rag_list_search_answer_profiles",
        ]
        assert "nl2sql_query" in tools["nl2sql"]


BUILTIN_SKILL_IDS = [skill.id for skill in skill_registry.list() if skill.source == "builtin"]


# 組み込みの Skill で同じ見出しの並び（使う見出しだけを、この順に置く。#1795 / #1816。
# 「入力の種類を分ける」は #1929）。見出しはすべて処理順の `## Step N:`（docs/agents/prompts.md。
# #3830）で、手順の Step を前と後の節の間に置く。
# 「いつ使うか」は SKILL.md の frontmatter の description に書く（Agent Skills の best practices）。
# 使うツールの一覧は frontmatter の metadata とモデルに渡すツールの定義にあるので、
# 本文に節を置かない（1 つの規則は 1 層。#2269）。
SKILL_LEADING_SECTIONS = ["入力の種類を分ける"]
SKILL_TRAILING_SECTIONS = ["結果ごとの対応", "回答の書き方", "してはいけないこと"]


@pytest.mark.parametrize("skill_id", BUILTIN_SKILL_IDS, ids=BUILTIN_SKILL_IDS)
def test_builtin_skill_instructions_are_flow_markdown(skill_id: str) -> None:
    """組み込みの Skill の指示は、手順で進める Step 形式の Markdown（#1795 / #1816 / #3830）。

    本文は目的の文から始まり、見出しはすべて `## Step N:`（1 から順に。入れ子の Step を置かない）。
    節の並びは組み込みの Skill で同じ。本文の「Step N」（「→ Step N」の行き先など）は
    ある Step だけを指す。
    """
    skill = skill_registry.get(skill_id)
    assert skill is not None
    instructions = skill.instructions
    assert not instructions.splitlines()[0].startswith("#")
    headings = re.findall(r"^#+ .*$", instructions, re.M)
    steps = re.findall(r"^## Step (\d+): (\S.*)$", instructions, re.M)
    assert len(steps) == len(headings), headings
    numbers = [int(number) for number, _ in steps]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    titles = [title for _, title in steps]
    leading = [name for name in SKILL_LEADING_SECTIONS if name in titles]
    trailing = [name for name in SKILL_TRAILING_SECTIONS if name in titles]
    procedure = titles[len(leading) : len(titles) - len(trailing)]
    assert titles == leading + procedure + trailing, titles
    assert "結果ごとの対応" in trailing, titles
    assert procedure and not set(procedure) & {*SKILL_LEADING_SECTIONS, *SKILL_TRAILING_SECTIONS}
    targets = {int(number) for number in re.findall(r"Step (\d+)", instructions)}
    assert targets <= set(numbers), targets
    # 1 つの長い段落に戻さない（書き直しの前は 1 行が 4,000 字を超えていた）。
    lines = instructions.splitlines()
    assert all(len(line) <= 300 for line in lines), max(lines, key=len)


def test_templates_use_registered_skills() -> None:
    used = {template.id: template.skill_ids for template in AGENT_TEMPLATES}
    assert all(
        skill_registry.get(skill_id) is not None for ids in used.values() for skill_id in ids
    )
    # 窓口系は RAG チャット、確認は RAG 検索、経費・調達は RAG チャット＋データ、品質管理は
    # RAG 検索＋データ。
    assert used["internal-policy-helpdesk"] == [RAG_CHAT_SKILL_ID]
    assert used["customer-support"] == [RAG_CHAT_SKILL_ID]
    assert used["it-helpdesk"] == [RAG_CHAT_SKILL_ID]
    assert used["compliance-check"] == [RAG_SEARCH_SKILL_ID]
    assert used["expense-assistant"] == [RAG_CHAT_AND_DATA_SKILL_ID]
    assert used["procurement"] == [RAG_CHAT_AND_DATA_SKILL_ID]
    assert used["manufacturing-quality"] == [RAG_SEARCH_AND_DATA_SKILL_ID]


def test_rag_only_templates_have_no_answer_style_instructions() -> None:
    """RAG だけのテンプレートは回答の書き方を指示しない（回答は RAG が書く。#5342）。"""
    rag_only = {RAG_CHAT_SKILL_ID, RAG_SEARCH_SKILL_ID}
    for template in AGENT_TEMPLATES:
        if set(template.skill_ids) <= rag_only:
            heading, body = template.instructions.split("\n", 1)
            assert heading.startswith("## Step 1: "), template.id
            assert "\n" not in body, template.id
            assert "根拠" not in template.instructions, template.id


@pytest.mark.parametrize(
    "previous",
    [
        PREVIOUS_DEFAULT,
        {**PREVIOUS_DEFAULT, "name": LEGACY_DEFAULT_NAME},
    ],
    ids=["previous_builtin", "legacy_name"],
)
def test_unchanged_default_is_published_with_the_new_content(previous: dict[str, Any]) -> None:
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[_builtin("default", previous)]))

    default = _agent(repository, "default")
    assert default.name == "RAG チャット＋データアシスタント"
    assert default.skill_ids == [RAG_CHAT_AND_DATA_SKILL_ID]
    assert default.published_version == 2
    assert [item.version for item in default.versions] == [1, 2]
    assert default.versions[0].name == previous["name"]
    assert default.versions[1].skill_ids == [RAG_CHAT_AND_DATA_SKILL_ID]
    assert default.unpublished_changes is False
    assert default.unavailable_skill_ids == []
    # 保存し直して読み込んでも、版は増えない（冪等）。
    load_state(repository, export_state(repository))
    assert _agent(repository, "default").published_version == 2


def test_changed_default_keeps_user_content() -> None:
    changed = _builtin("default", {**PREVIOUS_DEFAULT, "instructions": "利用者が変えた指示。"})
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[changed]))

    default = _agent(repository, "default")
    assert default.instructions == "利用者が変えた指示。"
    assert default.published_version == 1
    # 消えたスキルは画面が示す（下書き・常に最新の版の Run は失敗する）。
    assert default.unavailable_skill_ids == [
        "rag-and-structured-data",
        "business-rag-research",
    ]


def test_retired_rag_research_is_removed_unless_changed() -> None:
    unchanged = _builtin(RETIRED_RAG_RESEARCH_AGENT_ID, PREVIOUS_RAG_RESEARCH)
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[unchanged]))
    ids = [agent.id for agent in repository.list_agents()]
    assert RETIRED_RAG_RESEARCH_AGENT_ID not in ids
    # 無かった組み込みの Agent は足す。
    assert set(BUILTIN_AGENT_IDS) <= set(ids)
    assert RETIRED_RAG_RESEARCH_AGENT_ID not in export_state(repository).model_dump_json()

    changed = _builtin(
        RETIRED_RAG_RESEARCH_AGENT_ID, {**PREVIOUS_RAG_RESEARCH, "name": "利用者が付けた名前"}
    )
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[changed]))
    kept = _agent(repository, RETIRED_RAG_RESEARCH_AGENT_ID)
    # 組織の業務 Agent として残す（組み込みではないので削除できる）。
    assert kept.source == "runtime"
    assert kept.name == "利用者が付けた名前"
    assert kept.unavailable_skill_ids == ["business-rag-research"]
    repository.delete_agent(RETIRED_RAG_RESEARCH_AGENT_ID)


@pytest.mark.parametrize("agent_id", BUILTIN_AGENT_IDS)
def test_builtin_agents_cannot_be_deleted(agent_id: str) -> None:
    repository = AgentRuntimeRepository()
    with pytest.raises(ValueError, match="組み込みの業務 Agent は削除できません。"):
        repository.delete_agent(agent_id)


# ---- 保存済みのファイルを作る組み込みの業務 Agent に、後から足したスキルを足す（#5867） -------


def _saved_file_builtin(agent_id: str, published: list[str], **draft: Any) -> AgentProfile:
    """#5867 より前に保存したファイルを作る組み込みの業務 Agent（v1 を公開。下書きは `draft`）。

    `published` は v1（と、`draft` で変えなければ下書き）のスキル。
    """
    repository = AgentRuntimeRepository()
    base = _agent(repository, agent_id)
    content = {
        "name": base.name,
        "description": "保存したときの説明。",
        "instructions": base.instructions,
        "skill_ids": published,
    }
    saved = _builtin(agent_id, content)
    return saved.model_copy(deep=True, update=draft)


@pytest.mark.parametrize("agent_id", list(PREVIOUS_FILE_BUILTIN))
def test_saved_file_agents_get_only_the_added_skill_and_keep_other_settings(
    agent_id: str,
) -> None:
    """足りないスキル（Markdown 作成）だけを下書きと次の公開の版に足し、ほかは変えない。"""
    previous = PREVIOUS_FILE_BUILTIN[agent_id]
    saved = _saved_file_builtin(agent_id, previous)
    # 利用者が変えた公開中の版（指示・モデル・データの範囲）と、未公開の下書き。
    saved.versions[0] = saved.versions[0].model_copy(
        update={
            "instructions": "利用者が公開した指示。",
            "model_id": "synthetic-model",
            "data_scopes": {"rag": AgentDataScope(profile_ids=["profile-a"])},
        }
    )
    saved = saved.model_copy(update={"instructions": "利用者が書いた下書きの指示。"})
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[saved]))

    agent = _agent(repository, agent_id)
    assert agent.skill_ids == [*previous, MARKDOWN_CREATE_SKILL_ID]
    assert (agent.instructions, agent.description) == (
        "利用者が書いた下書きの指示。",
        "保存したときの説明。",
    )
    # 公開中の版の内容にスキルだけを足した次の版を公開する（前の版は残す）。
    assert [item.version for item in agent.versions] == [1, 2]
    assert agent.published_version == 2
    v1, v2 = agent.versions
    assert v1.skill_ids == previous
    assert v2.skill_ids == [*previous, MARKDOWN_CREATE_SKILL_ID]
    assert (v2.instructions, v2.model_id, v2.data_scopes) == (
        v1.instructions,
        v1.model_id,
        v1.data_scopes,
    )
    assert (v2.name, v2.description) == (v1.name, v1.description)
    assert agent.unavailable_skill_ids == []
    # 保存し直して読み込んでも、版もスキルも増えない（冪等）。
    exported = export_state(repository)
    load_state(repository, exported)
    assert export_state(repository).agents == exported.agents


def test_added_skill_is_pinned_when_the_published_version_pins_skills() -> None:
    """公開中の版がスキルを固定していれば、足すスキルも固定して Run で使える。"""
    previous = PREVIOUS_FILE_BUILTIN["doc-create"]
    saved = _saved_file_builtin("doc-create", previous)
    pins = [pin_skill(skill) for skill_id in previous if (skill := skill_registry.get(skill_id))]
    saved.versions[0] = saved.versions[0].model_copy(update={"skills": pins})
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[saved]))

    agent = _agent(repository, "doc-create")
    published = agent.published()
    assert published is not None and published.skills is not None
    assert [item.id for item in published.skills] == FILE_CREATE_SKILL_IDS
    run = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id="doc-create"))
    used = agent_for_run(agent, run)
    assert used.skill_ids == FILE_CREATE_SKILL_IDS
    assert [item.id for item in used.pinned_skills or []] == FILE_CREATE_SKILL_IDS


@pytest.mark.parametrize(
    ("draft", "published"),
    [
        # 利用者が Excel 作成を外していた（足すのは Markdown 作成だけ。外したものは戻さない）。
        ([DOCX_CREATE_SKILL_ID, PPTX_CREATE_SKILL_ID], [DOCX_CREATE_SKILL_ID]),
        # 足した後に利用者が Markdown 作成を外して公開した（前の版に残っているので戻さない）。
        (PREVIOUS_FILE_CREATE_SKILL_IDS, PREVIOUS_FILE_CREATE_SKILL_IDS),
    ],
    ids=["removed_before", "removed_after"],
)
def test_skills_removed_by_the_user_are_not_added_back(
    draft: list[str], published: list[str]
) -> None:
    saved = _saved_file_builtin("doc-create", published, skill_ids=draft)
    if draft == published:
        # 足した版（v2）の後に、外した版（v3）を公開した。
        versions = [
            saved.versions[0],
            saved.versions[0].model_copy(update={"version": 2, "skill_ids": FILE_CREATE_SKILL_IDS}),
            saved.versions[0].model_copy(update={"version": 3}),
        ]
        saved = saved.model_copy(update={"versions": versions, "published_version": 3})
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[saved]))

    agent = _agent(repository, "doc-create")
    if draft == published:
        assert agent.skill_ids == draft
        assert agent.published_version == 3 and len(agent.versions) == 3
    else:
        assert agent.skill_ids == [*draft, MARKDOWN_CREATE_SKILL_ID]
        assert agent.versions[-1].skill_ids == [*published, MARKDOWN_CREATE_SKILL_ID]
        assert XLSX_CREATE_SKILL_ID not in agent.skill_ids


def test_skills_are_added_only_to_the_builtin_file_agents() -> None:
    """利用者の業務 Agent と、ファイルを作らない組み込みの業務 Agent には足さない。"""
    custom = AgentProfile(
        id="agent_custom", name="利用者の業務 Agent", skill_ids=PREVIOUS_FILE_CREATE_SKILL_IDS
    )
    repository = AgentRuntimeRepository()
    load_state(repository, AgentRuntimeSnapshot(agents=[custom, *_saved_original_builtin()]))

    assert _agent(repository, "agent_custom").skill_ids == PREVIOUS_FILE_CREATE_SKILL_IDS
    for agent_id, content in ORIGINAL_BUILTIN.items():
        assert _agent(repository, agent_id).skill_ids == content["skill_ids"], agent_id
