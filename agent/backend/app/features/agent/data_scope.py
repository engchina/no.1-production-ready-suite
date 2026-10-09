"""業務 Agent のデータの範囲（使える RAG / NL2SQL のプロファイル。#1378）。

業務 Agent の定義に、標準の MCP 接続ごとの「使えるプロファイルの一覧と既定」を持つ。

- ``nl2sql``: 業務プロファイル（``nl2sql_query`` の ``profile_id``）。
- ``rag``: 検索・回答プロファイル（``rag_search`` / ``rag_retrieve_evidence`` /
  ``rag_lookup_guides`` の ``search_answer_profile_id``）。

範囲を設定していない（一覧が空の）接続は今までどおり、Run の利用者が使えるすべてのプロファイルから
モデルが選ぶ（範囲の検査はしない）。範囲を設定した接続は、組み込み Runtime の tool handler が
モデルの指示に頼らずに強制する（``enforce``）。

- 1 つだけ: Runtime がプロファイルを埋める（モデルが渡した値は上書き）。モデルに見せるツールの
  schema からその引数を除く（``scoped_input_schema``）。
- 複数: 範囲外の値はツールのエラーにして呼び先へ送らない。値が無ければ Agent の既定を使う
  （NL2SQL の ``"default"`` には落とさない）。
- 一覧（``nl2sql_list_profiles`` / ``rag_list_search_answer_profiles``）と推薦
  （``nl2sql_recommend_profile``）の結果は範囲で絞る（推薦が範囲外なら ``null``）。
- RAG で範囲を設定したら、モデルが渡す ``knowledge_base_ids`` は使わない（プロファイルの範囲を
  ナレッジベースの直接の指定で迂回させない。schema からも除く）。
- Run の step（``ToolCall.data_scope``）に、使ったプロファイルと「埋めた / 上書きした / 拒否した /
  絞った」ことを残す。

Agent の範囲は利用者の権限を広げない（呼び先は今までどおり Run の利用者の権限で判定する。
積で効く）。
呼び先でも範囲を強制する（サービストークンの claim）のは後続の #1379。範囲は接続（= audience）ごとの
ID の一覧なので、そのまま claim に入れられる。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.features.agent.tools import (
    MCP_TOOL_SEPARATOR,
    ExternalToolError,
    JsonObject,
    McpConnectionClient,
    ToolInvocationContext,
    mcp_base_tool_name,
)

NL2SQL_CONNECTION_ID = "nl2sql"
RAG_CONNECTION_ID = "rag"
# 範囲を持てる接続（標準の接続だけ。サービストークンで Run の利用者として呼ぶ）。
DATA_SCOPE_CONNECTIONS: tuple[str, ...] = (NL2SQL_CONNECTION_ID, RAG_CONNECTION_ID)
# 1 つの接続に設定できるプロファイルの数の上限。
MAX_SCOPE_PROFILES = 50
MAX_PROFILE_ID_CHARS = 128

# 範囲の外のプロファイルを指定したツールのエラー（呼び先へは送らない）。
DATA_SCOPE_VIOLATION_CODE = "agent_data_scope_violation"

# 接続ごとの、プロファイルの呼び方（エラー・指示の文言）。
_PROFILE_NOUNS: dict[str, str] = {
    NL2SQL_CONNECTION_ID: "業務プロファイル",
    RAG_CONNECTION_ID: "検索・回答プロファイル",
}
_PRODUCT_LABELS: dict[str, str] = {
    NL2SQL_CONNECTION_ID: "データ問い合わせ（NL2SQL）",
    RAG_CONNECTION_ID: "ナレッジ検索（RAG）",
}

# プロファイルを引数に取るツール → 引数の名前。
_PROFILE_ARGUMENTS: dict[str, dict[str, str]] = {
    NL2SQL_CONNECTION_ID: {"nl2sql_query": "profile_id"},
    RAG_CONNECTION_ID: {
        "rag_search": "search_answer_profile_id",
        "rag_retrieve_evidence": "search_answer_profile_id",
        "rag_lookup_guides": "search_answer_profile_id",
    },
}
# RAG の、ナレッジベースを直接指定できるツール（範囲を設定したら使わない）。
KNOWLEDGE_BASE_ARGUMENT = "knowledge_base_ids"
_KNOWLEDGE_BASE_TOOLS: dict[str, frozenset[str]] = {
    RAG_CONNECTION_ID: frozenset({"rag_search", "rag_retrieve_evidence"}),
}


@dataclass(frozen=True)
class _ListTool:
    """プロファイルの一覧のツール（結果の項目の名前・件数の既定と上限）。"""

    items_key: str
    default_limit: int
    max_limit: int


_LIST_TOOLS: dict[str, dict[str, _ListTool]] = {
    NL2SQL_CONNECTION_ID: {"nl2sql_list_profiles": _ListTool("profiles", 20, 100)},
    RAG_CONNECTION_ID: {
        "rag_list_search_answer_profiles": _ListTool("search_answer_profiles", 50, 200)
    },
}
_RECOMMEND_TOOLS: dict[str, frozenset[str]] = {
    NL2SQL_CONNECTION_ID: frozenset({"nl2sql_recommend_profile"}),
}

ScopeAction = Literal["filled", "overridden", "kept", "rejected", "filtered"]


class AgentDataScope(BaseModel):
    """1 つの接続のデータの範囲（使えるプロファイルの ID と既定）。一覧が空は「範囲なし」。"""

    model_config = ConfigDict(extra="forbid")

    profile_ids: list[str] = Field(default_factory=list, max_length=MAX_SCOPE_PROFILES)
    default_profile_id: str = ""

    @field_validator("profile_ids")
    @classmethod
    def _normalize_ids(cls, value: list[str]) -> list[str]:
        ids: list[str] = []
        for raw in value:
            item = raw.strip()
            if not item:
                continue
            if len(item) > MAX_PROFILE_ID_CHARS:
                raise ValueError(
                    f"プロファイルの ID は {MAX_PROFILE_ID_CHARS} 文字以内にしてください。"
                )
            if item not in ids:
                ids.append(item)
        return ids

    @model_validator(mode="after")
    def _check_default(self) -> AgentDataScope:
        default = self.default_profile_id.strip()
        if not self.profile_ids:
            self.default_profile_id = ""
            return self
        if not default and len(self.profile_ids) == 1:
            default = self.profile_ids[0]
        if not default:
            raise ValueError("既定のプロファイルを選んでください。")
        if default not in self.profile_ids:
            raise ValueError("既定のプロファイルは、使えるプロファイルの中から選んでください。")
        self.default_profile_id = default
        return self


AgentDataScopes = dict[str, AgentDataScope]


def normalize_data_scopes(value: dict[str, AgentDataScope]) -> dict[str, AgentDataScope]:
    """範囲を持てる接続だけを受け付け、範囲の無い（一覧が空の）接続は除く。"""
    unknown = sorted(key for key in value if key not in DATA_SCOPE_CONNECTIONS)
    if unknown:
        raise ValueError(
            "データの範囲を設定できない接続です: "
            f"{', '.join(unknown)}（{' / '.join(DATA_SCOPE_CONNECTIONS)} だけ設定できます）。"
        )
    return {
        key: scope
        for key in DATA_SCOPE_CONNECTIONS
        if (scope := value.get(key)) is not None and scope.profile_ids
    }


def _connection_of(function_name: str) -> str | None:
    if MCP_TOOL_SEPARATOR not in function_name:
        return None
    return function_name.split(MCP_TOOL_SEPARATOR, 1)[0]


def _active_scope(
    function_name: str, scopes: AgentDataScopes | None
) -> tuple[str, str, AgentDataScope] | None:
    """ツールに効く範囲（接続・ツールの素の名前・範囲）。範囲が無ければ None。"""
    if not scopes:
        return None
    connection = _connection_of(function_name)
    if connection is None:
        return None
    scope = scopes.get(connection)
    if scope is None or not scope.profile_ids:
        return None
    return connection, mcp_base_tool_name(function_name), scope


def _profile_list(scope: AgentDataScope) -> str:
    return "、".join(scope.profile_ids)


def scoped_input_schema(
    function_name: str, schema: JsonObject, scopes: AgentDataScopes | None
) -> JsonObject:
    """モデルに見せるツールの schema（範囲が 1 つならプロファイルの引数を除く）。

    範囲が複数なら、引数の説明に使える ID と既定を足す。RAG で範囲を設定したら
    ``knowledge_base_ids`` を除く。範囲が無いツールは元のまま返す。
    """
    active = _active_scope(function_name, scopes)
    if active is None:
        return schema
    connection, tool, scope = active
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    removed: set[str] = set()
    updated = deepcopy(schema)
    updated_properties: dict[str, Any] = updated["properties"]
    argument = _PROFILE_ARGUMENTS.get(connection, {}).get(tool)
    if argument is not None and argument in updated_properties:
        if len(scope.profile_ids) == 1:
            removed.add(argument)
        else:
            prop = updated_properties[argument]
            if isinstance(prop, dict):
                note = (
                    f"この業務 Agent で使える ID: {_profile_list(scope)}"
                    f"（省略すると既定の {scope.default_profile_id}）。"
                )
                described = str(prop.get("description") or "").strip()
                prop["description"] = f"{described} {note}".strip()
    if tool in _KNOWLEDGE_BASE_TOOLS.get(connection, frozenset()):
        removed.add(KNOWLEDGE_BASE_ARGUMENT)
    for name in removed:
        updated_properties.pop(name, None)
    required = updated.get("required")
    if isinstance(required, list):
        updated["required"] = [name for name in required if name not in removed]
    return updated


@dataclass
class ScopeEnforcement:
    """1 回のツールの呼び出しへの範囲の適用（送る引数・step に残す記録・拒否の文言）。"""

    connection: str
    tool: str
    scope: AgentDataScope
    arguments: JsonObject
    note: JsonObject
    error: str | None = None
    # 一覧のツールで、モデルが求めた件数（範囲で絞った後にこの件数に切る）。
    result_limit: int | None = None
    _list_tool: _ListTool | None = field(default=None, repr=False)

    def error_payload(self) -> JsonObject:
        """モデルへ返すツールのエラー（使えるプロファイルの案内つき）。"""
        return {
            "error": self.error,
            "error_code": DATA_SCOPE_VIOLATION_CODE,
            "allowed_profile_ids": list(self.scope.profile_ids),
            "default_profile_id": self.scope.default_profile_id,
        }

    def filter_output(self, output: JsonObject) -> tuple[JsonObject, int]:
        """一覧・推薦の結果を範囲で絞る（絞った結果と、外した件数）。"""
        allowed = set(self.scope.profile_ids)
        if self._list_tool is not None:
            items = output.get(self._list_tool.items_key)
            if not isinstance(items, list):
                return output, 0
            kept = [item for item in items if isinstance(item, dict) and item.get("id") in allowed]
            removed = len(items) - len(kept)
            if self.result_limit is not None:
                kept = kept[: self.result_limit]
            return {**output, self._list_tool.items_key: kept}, removed
        if self.tool in _RECOMMEND_TOOLS.get(self.connection, frozenset()):
            removed = 0
            updated = dict(output)
            recommended = output.get("recommended_profile_id")
            if isinstance(recommended, str) and recommended not in allowed:
                updated["recommended_profile_id"] = None
                removed += 1
            candidates = output.get("candidates")
            if isinstance(candidates, list):
                kept = [
                    item
                    for item in candidates
                    if isinstance(item, dict) and item.get("id") in allowed
                ]
                removed += len(candidates) - len(kept)
                updated["candidates"] = kept
            return updated, removed
        return output, 0


def enforce(
    function_name: str, arguments: JsonObject, scopes: AgentDataScopes | None
) -> ScopeEnforcement | None:
    """ツールの呼び出しに範囲を当てる。範囲の効かないツールは None（今までどおり）。"""
    active = _active_scope(function_name, scopes)
    if active is None:
        return None
    connection, tool, scope = active
    argument = _PROFILE_ARGUMENTS.get(connection, {}).get(tool)
    list_tool = _LIST_TOOLS.get(connection, {}).get(tool)
    kb_tool = tool in _KNOWLEDGE_BASE_TOOLS.get(connection, frozenset())
    recommend_tool = tool in _RECOMMEND_TOOLS.get(connection, frozenset())
    if argument is None and list_tool is None and not kb_tool and not recommend_tool:
        return None
    updated = dict(arguments)
    note: JsonObject = {"connection": connection, "allowed_profile_ids": list(scope.profile_ids)}
    enforcement = ScopeEnforcement(
        connection=connection, tool=tool, scope=scope, arguments=updated, note=note
    )
    if argument is not None:
        _enforce_profile(enforcement, argument)
    if kb_tool and KNOWLEDGE_BASE_ARGUMENT in updated:
        ignored = updated.pop(KNOWLEDGE_BASE_ARGUMENT)
        if ignored:
            note["ignored_knowledge_base_ids"] = ignored
    if list_tool is not None:
        requested = updated.get("limit")
        enforcement.result_limit = (
            requested if isinstance(requested, int) and requested > 0 else list_tool.default_limit
        )
        # 範囲で絞ってから求めた件数に切るため、呼び先には上限まで求める。
        updated["limit"] = list_tool.max_limit
        enforcement._list_tool = list_tool
        note["action"] = "filtered"
    if recommend_tool:
        note["action"] = "filtered"
    return enforcement


def _enforce_profile(enforcement: ScopeEnforcement, argument: str) -> None:
    scope = enforcement.scope
    arguments = enforcement.arguments
    note = enforcement.note
    requested = arguments.get(argument)
    requested_id = requested.strip() if isinstance(requested, str) else ""
    note["argument"] = argument
    if requested_id:
        note["requested_profile_id"] = requested_id
    action: ScopeAction
    if len(scope.profile_ids) == 1:
        chosen = scope.profile_ids[0]
        if not requested_id:
            action = "filled"
        elif requested_id == chosen:
            action = "kept"
        else:
            action = "overridden"
    elif not requested_id:
        chosen = scope.default_profile_id
        action = "filled"
    elif requested_id in scope.profile_ids:
        chosen = requested_id
        action = "kept"
    else:
        noun = _PROFILE_NOUNS.get(enforcement.connection, "プロファイル")
        note["action"] = "rejected"
        enforcement.error = (
            f"{noun}「{requested_id}」はこの業務 Agent では使えません。"
            f"使える{noun}: {_profile_list(scope)}（既定: {scope.default_profile_id}）。"
            f"{argument} に使える ID を渡すか、省略して既定を使ってください。"
        )
        return
    arguments[argument] = chosen
    note["profile_id"] = chosen
    note["action"] = action


def scope_instructions(scopes: AgentDataScopes | None, exposed: list[str]) -> str:
    """モデルへの指示に足す「データの範囲」の節（範囲が無ければ空）。

    ツールの名前は素の名前で書く（`compose_instructions` と同じく、呼び出し側でモデルに渡す名前へ
    書き直す）。強制は Runtime がするので、指示は誤った指定でエラーを繰り返さないための案内。
    """
    if not scopes:
        return ""
    connections = {_connection_of(name) for name in exposed}
    lines: list[str] = []
    for connection in DATA_SCOPE_CONNECTIONS:
        scope = scopes.get(connection)
        if scope is None or not scope.profile_ids or connection not in connections:
            continue
        noun = _PROFILE_NOUNS[connection]
        label = _PRODUCT_LABELS[connection]
        if connection == NL2SQL_CONNECTION_ID:
            argument = "profile_id"
            chooser = "nl2sql_list_profiles / nl2sql_recommend_profile"
            extra = ""
        else:
            argument = "search_answer_profile_id"
            chooser = "rag_list_search_answer_profiles"
            extra = "knowledge_base_ids は使えない（検索・回答プロファイルの参照先を検索する）。"
        if len(scope.profile_ids) == 1:
            lines.append(
                f"- {label}: {noun}は「{scope.profile_ids[0]}」に決まっている。"
                f"{argument} は渡さない（実行環境が設定する）。{extra}"
            )
        else:
            lines.append(
                f"- {label}: {noun}は次の中から選ぶ: {_profile_list(scope)}"
                f"（{argument} を省略すると既定の {scope.default_profile_id}）。"
                f"ほかの{noun}は使えない。迷ったら {chooser}"
                f"（この範囲に絞った結果を返す）で選ぶ。{extra}"
            )
    if not lines:
        return ""
    return "# データの範囲\nこの業務 Agent が使えるデータは次の範囲に限る。\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 編集画面の候補と、保存のときの確認（編集者のサービストークンで呼ぶ）。
# ---------------------------------------------------------------------------


class DataScopeCandidate(BaseModel):
    """データの範囲に選べるプロファイル（編集者が使えるもの）。"""

    id: str
    name: str
    description: str = ""


class DataScopeCandidatesData(BaseModel):
    connection: str
    label: str
    profiles: list[DataScopeCandidate] = Field(default_factory=list)


class DataScopeCandidatesError(Exception):
    """候補を取得できない（画面へ返す status と文言）。"""

    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def list_candidates(connection: str, *, user_uuid: str | None) -> DataScopeCandidatesData:
    """編集者が使えるプロファイル（MCP の一覧のツールを編集者のサービストークンで呼ぶ。同期）。"""
    from app.features.agent.config import runtime_config_store

    if connection not in DATA_SCOPE_CONNECTIONS:
        raise DataScopeCandidatesError(
            404, "データの範囲を設定できない接続です。", "data_scope_connection_unknown"
        )
    label = _PRODUCT_LABELS[connection]
    try:
        config = runtime_config_store.get_mcp(connection)
    except KeyError:
        raise DataScopeCandidatesError(
            409,
            f"MCP 接続「{label}」が登録されていません。",
            "data_scope_connection_missing",
        ) from None
    tool_name, list_tool = next(iter(_LIST_TOOLS[connection].items()))
    trace_id = f"data_scope_{uuid4().hex}"
    client = McpConnectionClient(
        config, context=ToolInvocationContext(trace_id=trace_id, user_uuid=user_uuid)
    )
    try:
        output = client.call_tool(
            tool_name, {"limit": list_tool.max_limit}, idempotent=True, trace_id=trace_id
        )
    except ExternalToolError as exc:
        if exc.code == "mcp.not_configured":
            raise DataScopeCandidatesError(
                409,
                f"MCP 接続「{config.label or label}」の URL が設定されていないため、"
                "プロファイルを選べません。運用設定の MCP 接続で設定してください。",
                "data_scope_connection_not_configured",
            ) from None
        raise DataScopeCandidatesError(
            502,
            f"MCP 接続「{config.label or label}」からプロファイルの一覧を取得できませんでした"
            f"（{exc.message}）。",
            "data_scope_candidates_unavailable",
        ) from None
    items = output.get(list_tool.items_key)
    profiles: list[DataScopeCandidate] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        profiles.append(
            DataScopeCandidate(
                id=item["id"],
                name=str(item.get("name") or item["id"]),
                description=str(item.get("description") or ""),
            )
        )
    return DataScopeCandidatesData(connection=connection, label=label, profiles=profiles)


def added_profile_ids(
    current: AgentDataScopes | None, requested: AgentDataScopes
) -> dict[str, list[str]]:
    """保存で新しく範囲に加えるプロファイル（接続 → ID）。既に範囲にあるものは確かめない。

    別の編集者が加えたプロファイル（今の編集者が使えないもの）を残したまま、ほかの項目を保存できる
    ようにするため、確かめるのは加えたものだけ。
    """
    added: dict[str, list[str]] = {}
    for connection, scope in requested.items():
        before = set(current.get(connection, AgentDataScope()).profile_ids) if current else set()
        new_ids = [item for item in scope.profile_ids if item not in before]
        if new_ids:
            added[connection] = new_ids
    return added


def verify_editor_can_use(added: dict[str, list[str]], *, user_uuid: str | None) -> None:
    """加えるプロファイルを編集者が使えるかを確かめる（使えなければ ValueError。同期）。"""
    for connection, ids in added.items():
        candidates = list_candidates(connection, user_uuid=user_uuid)
        known = {item.id for item in candidates.profiles}
        missing = [item for item in ids if item not in known]
        if missing:
            noun = _PROFILE_NOUNS[connection]
            raise ValueError(
                f"{candidates.label}の{noun}「{'、'.join(missing)}」は、あなたが使えないか"
                "見つかりません。候補から選んでください。"
            )
