"""組み込みの業務 Agent（5 つ。#1573・#5342）と、組み込みのスキルの形。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pytest
from checkpoint_support import export_state, load_state

from app.features.agent.builtin_runtime import agent_mcp_requirements
from app.features.agent.runtime import (
    BUILTIN_AGENT_IDS,
    RETIRED_RAG_RESEARCH_AGENT_ID,
    AgentProfile,
    AgentRuntimeRepository,
    AgentRuntimeSnapshot,
    AgentVersion,
    RunCreateRequest,
)
from app.features.agent.skills import (
    RAG_CHAT_AND_DATA_SKILL_ID,
    RAG_CHAT_SKILL_ID,
    RAG_SEARCH_AND_DATA_SKILL_ID,
    RAG_SEARCH_SKILL_ID,
    STRUCTURED_DATA_QUERY_SKILL_ID,
    FinalAnswerTool,
    skill_registry,
)
from app.features.agent.templates import AGENT_TEMPLATES

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


def test_new_environment_has_five_builtin_agents() -> None:
    repository = AgentRuntimeRepository()

    agents = repository.list_agents()
    expected = ["default", "rag-chat", "rag-search", "rag-search-data", "data-query"]
    assert [agent.id for agent in agents] == expected
    assert list(BUILTIN_AGENT_IDS) == expected
    assert [agent.name for agent in agents] == [
        "RAG チャット＋データアシスタント",
        "RAG チャットアシスタント",
        "RAG 検索アシスタント",
        "RAG 検索＋データアシスタント",
        "データ照会アシスタント",
    ]
    assert [agent.skill_ids for agent in agents] == [
        [RAG_CHAT_AND_DATA_SKILL_ID],
        [RAG_CHAT_SKILL_ID],
        [RAG_SEARCH_SKILL_ID],
        [RAG_SEARCH_AND_DATA_SKILL_ID],
        [STRUCTURED_DATA_QUERY_SKILL_ID],
    ]
    for agent in agents:
        # 公開した版があり、利用者がすぐ使える（チャット・自動実行で選べる）。
        assert agent.source == "builtin"
        assert agent.published_version == 1
        assert agent.unpublished_changes is False
        assert agent.unavailable_skill_ids == []
        run = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent.id))
        assert run.metadata["agent_version"] == 1


def test_builtin_agents_use_mcp_connections_of_their_skills() -> None:
    repository = AgentRuntimeRepository()

    def requirements(agent_id: str) -> dict[str, set[str] | None]:
        return agent_mcp_requirements(_agent(repository, agent_id).skill_ids)

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
        )
    }
    assert finals == {
        RAG_CHAT_SKILL_ID: [FinalAnswerTool(server_id="rag", tool_name="rag_chat")],
        RAG_SEARCH_SKILL_ID: [FinalAnswerTool(server_id="rag", tool_name="rag_search")],
        RAG_CHAT_AND_DATA_SKILL_ID: [],
        RAG_SEARCH_AND_DATA_SKILL_ID: [],
        STRUCTURED_DATA_QUERY_SKILL_ID: [],
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
            assert "\n" not in template.instructions, template.id
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
