"""Agent Runtime の Skill registry。

Skill は Agent に割り当てる業務手順（instructions）と、使ってよい MCP ツールの許可リスト
（mcp_requirements）を持つ。組み込み Runtime が instructions を Agent の指示へ合成し、
許可リストのツールだけを LLM へ渡す。
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.features.agent.profile_name_migration import migrate_tool_name

JsonObject = dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


class SkillMcpRequirement(BaseModel):
    """Skill が使う MCP 接続とツールの許可リスト（#757）。

    `server_id` は MCP 接続の ID（`rag` / `nl2sql` / 登録した接続）。`control-plane` は
    Control Plane のツール（`tool_registry`）。`tool_names` が空なら接続のすべてのツールを使う。
    `tool_names` は MCP のツールの素の名前（`rag_search`）。Skill の指示も素の名前で書いてよく、
    組み込み Runtime が Run のときにモデルへ渡す名前（`rag__rag_search`）へ書き直す（#1303）。
    """

    server_id: str
    tool_names: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_saved_names(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("tool_names"), list):
            return {
                **value,
                "tool_names": [
                    migrate_tool_name(item) if isinstance(item, str) else item
                    for item in value["tool_names"]
                ],
            }
        return value


class AgentSkillDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    # 由来層: builtin(code) / project(SKILL.md) / env(JSON 宣言) / runtime(UI/API)。
    # builtin は予約 id として保護し、宣言・runtime からの上書き/削除を拒否する。
    source: str = "runtime"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AgentSkillListOutput(BaseModel):
    skills: list[AgentSkillDefinition]
    metadata: JsonObject = Field(default_factory=dict)


class SkillRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._skills: dict[str, AgentSkillDefinition] = {}
        self._builtin_ids: set[str] = set()

    def register(self, skill: AgentSkillDefinition) -> None:
        """組込み(builtin)skill を登録する。id は予約され保護される。"""
        with self._lock:
            stamped = skill.model_copy(deep=True, update={"source": "builtin"})
            self._skills[stamped.id] = stamped
            self._builtin_ids.add(stamped.id)

    def set_declared(self, source: str, skills: list[AgentSkillDefinition]) -> None:
        """宣言層(project / env)を source 単位で置換する。builtin id は無視。"""
        with self._lock:
            for skill_id in [
                sid
                for sid, skill in self._skills.items()
                if skill.source == source and sid not in self._builtin_ids
            ]:
                del self._skills[skill_id]
            for skill in skills:
                if skill.id in self._builtin_ids:
                    continue
                self._skills[skill.id] = skill.model_copy(deep=True, update={"source": source})

    def upsert_custom(self, skill: AgentSkillDefinition) -> AgentSkillDefinition:
        """runtime(UI/API)層へ追加・更新する。builtin id は拒否。"""
        with self._lock:
            if skill.id in self._builtin_ids:
                raise ValueError("組み込みのスキルと同じ ID は使えません。")
            stored = skill.model_copy(deep=True, update={"source": "runtime", "updated_at": _now()})
            self._skills[stored.id] = stored
            return stored.model_copy(deep=True)

    def remove(self, skill_id: str) -> None:
        """runtime 層の skill のみ削除する。builtin / 宣言層は拒否。"""
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise KeyError(skill_id)
            if skill.source != "runtime":
                raise ValueError(
                    "組み込み・ファイル・環境変数・プラグインのスキルは画面から削除できません。"
                )
            del self._skills[skill_id]

    def export(self, source: str | None = None) -> list[AgentSkillDefinition]:
        with self._lock:
            return [
                skill.model_copy(deep=True)
                for skill in sorted(self._skills.values(), key=lambda item: item.id)
                if source is None or skill.source == source
            ]

    def list(self) -> list[AgentSkillDefinition]:
        with self._lock:
            return [
                skill.model_copy(deep=True)
                for skill in sorted(self._skills.values(), key=lambda item: item.id)
            ]

    def get(self, skill_id: str) -> AgentSkillDefinition | None:
        with self._lock:
            skill = self._skills.get(skill_id)
            return skill.model_copy(deep=True) if skill is not None else None


skill_registry = SkillRegistry()

skill_registry.register(
    AgentSkillDefinition(
        id="business_rag_research",
        name="業務 RAG 調査",
        description="業務 RAG（MCP 接続 rag）を使って根拠付き情報を検索する。",
        instructions=(
            "ユーザーの目的を rag_search の query として扱い、根拠（evidence）に基づいて答える。"
            "答える前に根拠だけを集める段（子目標ごとの調べ物など）では、回答を作らない"
            " rag_retrieve_evidence を使い、最後に答えるときだけ rag_search "
            "を呼ぶ（多段の質問は次の手順で、最後に rag_search を呼ばない）。"
            "複数の根拠をつないで初めて答えられる質問（多段の質問。例: システムの担当部署 → "
            "その部署の承認者 → 承認者の承認の期限、2 つの実体の比較）は、次の順で進める。"
            "1) 答えに要る事実を段に分け、前の段の答えが次の段の検索の語になる順に並べる。"
            "2) 段ごとに rag_retrieve_evidence を呼ぶ。query は質問全体や問いの文ではなく、"
            "その段の実体（前の段で分かった正式名・略号・ID・役職名を根拠の表記のまま）"
            "と引く属性だけにする（例: 「経費精算ポータル 担当部署」）。"
            "比べる質問は実体ごとに引き、略称・別表記しか分からなければ両方を入れる。"
            "evidence_limit は既定より小さくしない（指定しなくてよい）。"
            "3) 根拠が答えのある台帳・一覧・別の規程を示したら"
            "（「担当部署はシステム台帳で確かめる」など）、一般の規則や表で済ませず、"
            "その台帳・一覧の名前と実体と属性で次の段を引く"
            "（例: 「システム台帳 経費精算ポータル 担当部署」）。"
            "台帳・一覧の行の値が略号・区分（「担当部署: 経」「重要度: B」）なら、"
            "1 回目の結果だけで答えず、その意味を次の段で引く（例: 「担当部署 経 正式名」）。"
            "結果に record_codes が付いたら、答えに要る値を"
            "その next_step に従って引いてから答える。"
            "4) 同じ段で言い換えて 2 回引いても根拠が出なければ、それ以上言い換えず、"
            "その段を確かめられなかった点として次の段へ進む。"
            "結果に repeated_query が付いたら、同じ query を呼び直さずその next_step に従う。"
            "5) 全部の段の根拠がそろったら、質問全体で rag_search を呼び直さず"
            "（質問の語で引き直すと前の段の根拠が落ちる）、集めた根拠だけで答え、"
            "段ごとに確かめた文書と場所（節・頁）を示す。"
            "確かめられなかった段は推測で補わず、その点を示して分かった段までを答える。"
            "結果の rag_calls_remaining（この実行で残る検索の回数）が残りの段の数より少なければ、"
            "段をまとめるか、読み取り（rag_read_document・rag_read_source。回数に数えない）で補う。"
            "対象の検索・回答プロファイルが分からなければ "
            "rag_list_search_answer_profiles で確かめる。"
            "回答に使った根拠（used_in_answer）を優先し、文書名と場所（locator の節・頁）を示す。"
            "根拠の excerpt が切り詰められている（truncated）か、前後の条件・例外を確かめる必要が"
            "あるときは、rag_read_source に document_id と chunk_id を渡して本文を読む。"
            "検索で当たらなかった前後の章・本文が指す同じ文書の別の箇所（「第 4 章を参照」など。"
            "結果の references）・長い文書の続きは、rag_outline で文書の節の構成を見て、"
            "rag_read_document で読む（節の cursor・page・section・locator で読み始め、"
            "続きは next_cursor を渡す）。根拠の locator.element_locator は"
            " rag_read_source と rag_read_document の locator に渡せ、chunk が作り直されても"
            "同じ箇所を読める。"
            "rag_search の outcome で答え方を決める。answered は根拠に沿って答える。"
            "conditional は conditions（説明が成り立つ条件）と gaps（資料で確かめられない点）を"
            "示し、条件ごとに分けて答える。"
            "needs_environment_data は資料だけでは回答を確定できない（現場の値・記録の確認が"
            "要る）。結果の next_step に従う。next_step.action が continue_with_tools なら、"
            "confirmations（確かめる現場の値・記録）を next_step.tools のツールで確かめてから答え、"
            "確かめた値はツールの結果を出所として示す。answer_with_confirmations なら"
            "confirmations を利用者が確かめる点として挙げる。どちらでも、確かめられなかった現場の"
            "値を推測で断定しない。insufficient_evidence は"
            "資料で確かめられなかったことを伝え、推測で補わない。requests の missing は"
            "答えていない要求として利用者に示す。"
            "needs_clarification は clarifications の問いを利用者にそのまま確かめ、推測で選ばない。"
            "答えを得たら conditions（条件の id → 値）に入れて rag_search を呼び直す。"
            "手順を案内する依頼では、先に rag_lookup_guides で業務ガイド（確かめる条件・手順の順・"
            "影響範囲・引き継ぎ先）を確かめ、影響範囲が広い操作や承認が要る操作はその旨を示す。"
            "業務ガイドを引かずに rag_retrieve_evidence で根拠を集めると、結果の guide_check に"
            "当たる業務ガイドと次の手（next_step）が付く。ツールの結果の next_step.action が"
            " ask_clarification なら、手順や条件ごと（分岐ごと）の答えを書かずに、questions の問い"
            "だけを利用者に返す（すべての場合を並べて答えない）。answer_by_conditions は条件ごとに"
            "分けて答え、answer_with_guide は分かっている条件に当たる場合の手順だけを答える。"
            "handoff は引き継ぎ先を示す。"
            "needs_human は引き継ぎ先を示し、操作を代わりに進めない。"
        ),
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="rag",
                tool_names=[
                    "rag_search",
                    "rag_list_search_answer_profiles",
                    "rag_read_source",
                    "rag_lookup_guides",
                    "rag_retrieve_evidence",
                    "rag_outline",
                    "rag_read_document",
                ],
            )
        ],
        tags=["rag", "research", "business-data"],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="structured_data_query",
        name="構造化データ照会",
        description="NL2SQL（MCP 接続 nl2sql）へ質問を渡して表形式の結果を取得する。",
        instructions=(
            "SQL は監査・説明用途として受け取り、この Runtime 内では実行しない。"
            "業務プロファイルが分からなければ nl2sql_recommend_profile で選ぶ。"
            "nl2sql_query / nl2sql_get_job は、ジョブが終わるまでツールの中で待ってから返す。"
            "それでも status が pending / running なら、nl2sql_get_job に job_id と "
            "wait_seconds=40 を渡して続きを取る。結果が出ないまま答えるときは、"
            "実行中であることと job_id を伝え、結果を推測で補わない。"
            "status が error なら error_message と error_code を伝える。"
        ),
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="nl2sql",
                tool_names=[
                    "nl2sql_query",
                    "nl2sql_get_job",
                    "nl2sql_list_profiles",
                    "nl2sql_recommend_profile",
                ],
            )
        ],
        tags=["nl2sql", "structured-data", "audit-sql"],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="rag_then_structured_data",
        name="RAG 後に構造化データ照会",
        description="業務 RAG で文脈を確認した後、NL2SQL へ同じ目的を渡す。",
        instructions=(
            "非構造文脈と構造化表の両方が必要な調査に使う。先に rag_search で資料の文脈を確かめる。"
            "rag_search の outcome が needs_environment_data（資料だけでは確定できない）なら、"
            "結果の next_step に従い、confirmations の点を nl2sql_query で確かめてから答える"
            "（nl2sql_query が pending / running を返したら nl2sql_get_job で続きを取る）。"
            "確かめた値は NL2SQL の結果を、手順・規則は資料の根拠を出所として分けて示す。"
        ),
        mcp_requirements=[
            SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
            SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query", "nl2sql_get_job"]),
        ],
        tags=["rag", "nl2sql", "business-data"],
    )
)


def reload_declared_skills() -> dict[str, int]:
    """設定の中立ディレクトリ / JSON 宣言を読み込み、project / env 層を更新する。

    Claude/Codex に倣い宣言(ファイル/env)を永続層、runtime(API/UI)を
    セッション層とする。env を後勝ちにするため project → env の順で適用する。
    """
    from app.features.agent.skills_loader import (
        load_skills_from_dir,
        load_skills_from_json,
    )
    from app.settings import get_settings

    settings = get_settings()
    project = load_skills_from_dir(settings.agent_skills_dir)
    env = load_skills_from_json(settings.agent_skills_definitions_json)
    skill_registry.set_declared("project", project)
    skill_registry.set_declared("env", env)
    return {"project": len(project), "env": len(env)}


# 起動時に宣言層を読み込む(未設定なら何もしない)。
reload_declared_skills()
