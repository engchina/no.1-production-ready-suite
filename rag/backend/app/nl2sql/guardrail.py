"""NL2SQL Guardrail アダプター(SQL 安全ポリシーの手動選択プリセット)。

「``SELECT`` のみ」を prompt 任せにせず、生成 SQL を **実行前に決定論で静的検査**する多層防御の
中核。read_only(既定)/ strict / sandboxed を束ね、文種別判定・複文検出・破壊的トークン検出・
object allowlist・row limit・semantic_verify(逆翻訳突合)要否を解決する。

このモジュールは **非 network・決定論**。実際の read-only 物理強制(低権限ロール/セッション)と
EXPLAIN 検証・semantic_verify の LLM 逆翻訳は実行層が担い、ここは判定だけを返す。
外部安全 SaaS は導入しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Nl2SqlGuardrailPolicy, Settings

GuardrailPolicy = Nl2SqlGuardrailPolicy
DEFAULT_GUARDRAIL_POLICY: GuardrailPolicy = "read_only"
GUARDRAIL_POLICY_ORDER: tuple[GuardrailPolicy, ...] = ("read_only", "strict", "sandboxed")

# 読み取り専用として許可する先頭キーワード。
_READ_ONLY_LEADING = frozenset({"SELECT", "WITH"})
# 書き込み(DML)キーワード。
_WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT"})
# DDL / 権限 / 破壊的キーワード。
_DDL_KEYWORDS = frozenset(
    {
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "RENAME",
        "GRANT",
        "REVOKE",
        "FLASHBACK",
        "PURGE",
        "COMMENT",
        "ANALYZE",
        "AUDIT",
    }
)
# PL/SQL / 動的実行の入口。
_PLSQL_KEYWORDS = frozenset({"BEGIN", "DECLARE", "CALL", "EXEC", "EXECUTE"})
# どこに現れてもブロックする危険トークン。
_DANGEROUS_TOKENS = frozenset({"EXECUTE", "IMMEDIATE", "GRANT", "DROP", "TRUNCATE", "ALTER"})

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?)", re.IGNORECASE
)


@dataclass(frozen=True)
class GuardrailAdapterParams:
    """Guardrail 段へ渡す解決済み effective パラメータ。"""

    policy: GuardrailPolicy
    enforce_read_only: bool
    max_rows: int
    require_object_allowlist: bool
    semantic_verify: bool
    run_role: str


@dataclass(frozen=True)
class GuardrailPolicyStatus:
    """1 安全ポリシーの選択状態と効果。"""

    name: GuardrailPolicy
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    enforce_read_only: bool
    require_object_allowlist: bool
    semantic_verify: bool


@dataclass(frozen=True)
class GuardrailAdapterRuntimeSettings:
    """Guardrail アダプターの非機密 runtime snapshot。"""

    policy: GuardrailPolicy
    enforce_read_only: bool
    max_rows: int
    require_object_allowlist: bool
    semantic_verify: bool
    run_role: str
    policies: tuple[GuardrailPolicyStatus, ...]


@dataclass(frozen=True)
class GuardrailVerdict:
    """1 つの生成 SQL に対する静的検査結果。"""

    allowed: bool
    policy: GuardrailPolicy
    statement_type: str
    violations: tuple[str, ...] = field(default_factory=tuple)
    normalized_sql: str = ""
    semantic_verify_required: bool = False
    max_rows: int | None = None
    run_role: str | None = None
    object_allowlist_checked: bool = False


# 各ポリシーの静的 spec(origin / 推奨 / 効果)。
_POLICY_SPECS: dict[GuardrailPolicy, dict[str, object]] = {
    "read_only": {
        "origin": "default",
        "recommended_for": ("一般利用", "BI 参照", "既定"),
        "require_object_allowlist": False,
        "semantic_verify": False,
    },
    "strict": {
        "origin": "hardened",
        "recommended_for": ("本番", "機微データ", "監査要件"),
        "require_object_allowlist": True,
        "semantic_verify": True,
    },
    "sandboxed": {
        "origin": "isolated",
        "recommended_for": ("検証", "未知スキーマ", "最小権限実行"),
        "require_object_allowlist": True,
        "semantic_verify": False,
    },
}


def normalize_guardrail_policy(value: object) -> GuardrailPolicy:
    """未知のポリシー名は既定 read_only へ寄せる。"""
    text = str(value or "").strip().lower()
    if text in _POLICY_SPECS:
        return text
    return DEFAULT_GUARDRAIL_POLICY


def resolve_guardrail_adapter(settings: Settings) -> GuardrailAdapterParams:
    """Settings から Guardrail アダプターの effective パラメータを作る。"""
    policy = normalize_guardrail_policy(
        getattr(settings, "nl2sql_guardrail_policy", DEFAULT_GUARDRAIL_POLICY)
    )
    spec = _POLICY_SPECS[policy]
    return GuardrailAdapterParams(
        policy=policy,
        enforce_read_only=True,  # 全ポリシーで SELECT のみを強制(多層防御の前提)。
        max_rows=int(getattr(settings, "nl2sql_guardrail_max_rows", 1000)),
        require_object_allowlist=bool(spec["require_object_allowlist"]),
        semantic_verify=bool(spec["semantic_verify"]),
        run_role=str(getattr(settings, "nl2sql_guardrail_run_role", "") or "").strip(),
    )


def guardrail_adapter_runtime_settings(settings: Settings) -> GuardrailAdapterRuntimeSettings:
    """Settings から Guardrail アダプター readiness snapshot を作る。"""
    params = resolve_guardrail_adapter(settings)
    statuses = tuple(
        GuardrailPolicyStatus(
            name=name,
            origin=str(_POLICY_SPECS[name]["origin"]),
            recommended_for=tuple(_POLICY_SPECS[name]["recommended_for"]),  # type: ignore[arg-type]
            selected=name == params.policy,
            enforce_read_only=True,
            require_object_allowlist=bool(_POLICY_SPECS[name]["require_object_allowlist"]),
            semantic_verify=bool(_POLICY_SPECS[name]["semantic_verify"]),
        )
        for name in GUARDRAIL_POLICY_ORDER
    )
    return GuardrailAdapterRuntimeSettings(
        policy=params.policy,
        enforce_read_only=params.enforce_read_only,
        max_rows=params.max_rows,
        require_object_allowlist=params.require_object_allowlist,
        semantic_verify=params.semantic_verify,
        run_role=params.run_role,
        policies=statuses,
    )


def strip_sql_comments(sql: str) -> str:
    """``--`` 行コメントと ``/* */`` ブロックコメントを除去する(文字列リテラルは保護)。"""
    out: list[str] = []
    i = 0
    n = len(sql)
    in_string = False
    while i < n:
        ch = sql[i]
        if in_string:
            out.append(ch)
            if ch == "'":
                # 連続 '' はエスケープ。
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """文字列リテラルを尊重して ``;`` で文分割し、空文を除く。"""
    stmts: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_string:
            current.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            current.append(ch)
            i += 1
            continue
        if ch == ";":
            stmts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    stmts.append("".join(current))
    return [s.strip() for s in stmts if s.strip()]


def strip_string_literals(sql: str) -> str:
    """``'...'`` 文字列リテラルを除去する(リテラル内のキーワード誤検出を防ぐ)。"""
    out: list[str] = []
    i = 0
    n = len(sql)
    in_string = False
    while i < n:
        ch = sql[i]
        if in_string:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokens(sql_upper: str) -> list[str]:
    return re.findall(r"[A-Z_][A-Z0-9_$#]*", sql_upper)


def classify_statement(stmt: str) -> str:
    """先頭キーワードから文種別を判定する(SELECT/WITH/INSERT/.../DDL/PLSQL/EMPTY/UNKNOWN)。"""
    tokens = _tokens(stmt.upper())
    if not tokens:
        return "EMPTY"
    head = tokens[0]
    if head in {"SELECT", "WITH"}:
        return head
    if head in _WRITE_KEYWORDS:
        return head
    if head in _DDL_KEYWORDS:
        return "DDL"
    if head in _PLSQL_KEYWORDS:
        return "PLSQL"
    return "UNKNOWN"


def _extract_table_refs(stmt: str) -> set[str]:
    refs: set[str] = set()
    for m in _TABLE_REF_RE.finditer(stmt):
        refs.add(m.group(1).upper())
    return refs


def _object_matches_allowlist(ref: str, allowed: frozenset[str]) -> bool:
    # owner.name と name のどちらでも許可集合に当たれば OK。
    if ref in allowed:
        return True
    last = ref.split(".")[-1]
    return last in allowed or any(a.split(".")[-1] == last for a in allowed)


def enforce(
    sql: str,
    params: GuardrailAdapterParams,
    *,
    allowed_objects: tuple[str, ...] = (),
) -> GuardrailVerdict:
    """生成 SQL を静的検査し、実行可否(``allowed``)と違反理由を返す。

    全ポリシーで read-only(単一 SELECT/WITH)を強制し、strict/sandboxed では object allowlist と
    row limit / semantic_verify 要否を上乗せする。**実行は allowed=True の後に人手承認を経て行う。**
    """
    normalized = strip_sql_comments(sql or "").strip()
    statements = split_statements(normalized)
    violations: list[str] = []

    if not statements:
        return GuardrailVerdict(
            allowed=False,
            policy=params.policy,
            statement_type="EMPTY",
            violations=("empty_statement",),
            normalized_sql="",
        )

    if len(statements) > 1:
        violations.append("multiple_statements")

    primary = statements[0]
    statement_type = classify_statement(primary)
    # キーワード/表参照の走査は文字列リテラルを除いた上で行う(誤検出防止)。
    scan_sql = strip_string_literals(primary)
    tokens = set(_tokens(scan_sql.upper()))

    if statement_type not in _READ_ONLY_LEADING:
        violations.append(f"non_select_statement:{statement_type}")

    # 書き込み / DDL / PL/SQL / 危険トークンの混入(WITH ... INSERT 等)を検出。
    if tokens & _WRITE_KEYWORDS:
        violations.append("write_keyword_present")
    if tokens & _DDL_KEYWORDS:
        violations.append("ddl_keyword_present")
    if tokens & _PLSQL_KEYWORDS:
        violations.append("plsql_keyword_present")
    if tokens & _DANGEROUS_TOKENS:
        violations.append("dangerous_token_present")
    if any(t.startswith(("DBMS_", "UTL_", "DBMS$", "OWA_")) for t in tokens):
        violations.append("package_call_present")
    # SELECT ... FOR UPDATE は行ロックを取るため read-only では不可。
    if "FOR" in tokens and "UPDATE" in tokens:
        violations.append("select_for_update")

    object_allowlist_checked = False
    if params.require_object_allowlist:
        if allowed_objects:
            allowed = frozenset(o.upper() for o in allowed_objects)
            refs = _extract_table_refs(scan_sql)
            unknown = sorted(r for r in refs if not _object_matches_allowlist(r, allowed))
            object_allowlist_checked = True
            if unknown:
                violations.append("object_not_in_allowlist:" + ",".join(unknown))
        else:
            violations.append("object_allowlist_unavailable")

    allowed_flag = not violations
    return GuardrailVerdict(
        allowed=allowed_flag,
        policy=params.policy,
        statement_type=statement_type,
        violations=tuple(violations),
        normalized_sql=normalized,
        semantic_verify_required=params.semantic_verify and allowed_flag,
        max_rows=params.max_rows if params.policy != "read_only" else None,
        run_role=(params.run_role or None) if params.policy == "sandboxed" else None,
        object_allowlist_checked=object_allowlist_checked,
    )
