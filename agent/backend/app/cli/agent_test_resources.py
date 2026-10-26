"""合成資料の業務 Agent を作成・更新して公開する管理の命令（#3545）。

    uv run python -m app.cli.agent_test_resources --rag-state <RAG の命令の state>
    uv run python -m app.cli.agent_test_resources --profile A=<ID> --profile B=<ID> \
        [--namespace <区分>]

合成資料の検索・回答を確かめる業務 Agent（定義は `agent/examples/test-pdfs-ja/agents.json`）を、
RAG の命令（`app.rag.test_resources_cli`）が作った検索・回答プロファイルの ID で作る。RAG は
Agent を呼ばないので、業務 Agent の段は Agent の側のこの命令が持つ（手順は
rag/examples/test-pdfs-ja/README.md）。

- プロファイルの ID は、RAG の命令の `--state`（`complete: true` のもの。名前の区分も読む）か、
  `--profile <グループ>=<ID>` で渡す。RAG の API は呼ばない。
- 業務 Agent は Agent の保存先（共通 `.env` の `PLATFORM_ORACLE_*`）に repository で書く（画面の
  API・Cookie を使わない）。Oracle の保存先だけを受け付ける（memory・file は動いている backend と
  共有できない）。
- 名前で既存の業務 Agent を探し、無ければ作る。あれば定義（版の項目と有効 / 無効）を下書きに合わせ、
  公開していない変更があるときだけ公開する。同じ命令を何度実行してもよい（変更が無ければ書かない）。
  同名が複数ある・組み込み・個人の業務 Agent・移行が要るときは変更せずに止める。
- 公開は画面と同じ公開前の評価の門（`publish_gate`）を通す。断られたら下書きを残して止める。
- 書いたときは、動いている Agent の backend のメモリが古いまま（次に自分で保存するまで、この命令の
  変更を読まない。#1916）なので、出力の `restart_required` のプロセスを再起動する。

結果（業務 Agent の ID と公開中の版）は 1 行の JSON で標準出力に出し、`--state` を渡せばそこにも
保存する。認証情報・接続先は出さない。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pr_backend_core.logging import configure_cli_logging
from pr_backend_core.oracle_dsn import enable_cli_connect_mode
from pydantic import ValidationError

from app.features.agent.data_scope import RAG_CONNECTION_ID, AgentDataScope
from app.features.agent.publish_gate import publish_evaluation_refusal
from app.features.agent.runtime import (
    AGENT_VERSIONED_FIELDS,
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeRepositoryContract,
    AgentRuntimeStorageUnavailableError,
)

# 定義の項目（版の項目と有効 / 無効）。データの範囲は定義に書かず、プロファイルの ID から作る。
DEFINITION_FIELDS: tuple[str, ...] = (*AGENT_VERSIONED_FIELDS, "enabled")
INPUT_FIELDS = frozenset(DEFINITION_FIELDS) - {"data_scopes"}
DEFAULT_DEFINITIONS = Path(__file__).resolve().parents[3] / "examples/test-pdfs-ja/agents.json"
PUBLISH_NOTE = "合成資料の検索・回答の確認用"
# グループ・名前の区分（RAG の命令と同じ形）。
KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
NAME_PREFIX = "テスト_"


class ProvisionError(RuntimeError):
    """値や保存先の中身を漏らさず、止めた理由を説明する。"""


def read_definitions(path: Path) -> dict[str, dict[str, Any]]:
    """業務 Agent の定義（グループ → 項目）。形が違えば保存先に触る前に止める。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProvisionError("業務 Agent の定義の版・形式を確認してください。")
    agents = data.get("agents")
    if data.get("version") != 1 or not isinstance(agents, dict) or not agents:
        raise ProvisionError("業務 Agent の定義の版・形式を確認してください。")
    for key, definition in agents.items():
        if not KEY_PATTERN.fullmatch(key) or not isinstance(definition, dict):
            raise ProvisionError("業務 Agent の定義のグループを確認してください。")
        unknown = sorted(set(definition) - INPUT_FIELDS)
        if unknown:
            raise ProvisionError(
                f"業務 Agent の定義に使えない項目があります: {', '.join(unknown)}"
                "（データの範囲はプロファイルの ID から作ります）。"
            )
        try:
            AgentProfile.model_validate(definition)
        except ValidationError as exc:
            raise ProvisionError(
                f"業務 Agent の定義（グループ {key}）を確認してください。"
            ) from exc
    return agents


def _scope(profile_ids: object, default_profile_id: object) -> AgentDataScope:
    if (
        not isinstance(profile_ids, list)
        or not profile_ids
        or not all(isinstance(item, str) and item.strip() for item in profile_ids)
        or not isinstance(default_profile_id, str)
    ):
        raise ProvisionError("検索・回答プロファイルの ID を確認してください。")
    try:
        return AgentDataScope(profile_ids=profile_ids, default_profile_id=default_profile_id)
    except ValidationError as exc:
        raise ProvisionError("検索・回答プロファイルの ID と既定を確認してください。") from exc


def read_rag_state(path: Path) -> tuple[dict[str, AgentDataScope], str]:
    """RAG の命令の state から、グループごとのプロファイルの ID と名前の区分を読む。"""
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("complete") is not True:
        raise ProvisionError(
            "RAG の再構築が完了していません（state の complete が true ではありません）。"
            "先に RAG の命令を完了してください。"
        )
    groups = state.get("groups")
    if not isinstance(groups, dict) or not groups:
        raise ProvisionError("RAG の state にグループがありません。")
    scopes = {
        str(key): _scope(row.get("profile_ids"), row.get("default_profile_id"))
        for key, row in groups.items()
        if isinstance(row, dict)
    }
    if len(scopes) != len(groups):
        raise ProvisionError("RAG の state のグループを確認してください。")
    namespace = state.get("namespace", "")
    if not isinstance(namespace, str):
        raise ProvisionError("RAG の state の名前の区分を確認してください。")
    return scopes, namespace


def parse_profiles(values: Sequence[str]) -> dict[str, AgentDataScope]:
    """`<グループ>=<プロファイルの ID>` の並び（グループごとに 1 件。既定も同じ）。"""
    scopes: dict[str, AgentDataScope] = {}
    for value in values:
        key, separator, profile_id = value.partition("=")
        if not separator or not KEY_PATTERN.fullmatch(key) or key in scopes:
            raise ProvisionError(
                "--profile は <グループ>=<ID> の形で、グループごとに 1 回にしてください。"
            )
        scopes[key] = _scope([profile_id.strip()], profile_id.strip())
    return scopes


def desired_agent(
    definition: Mapping[str, Any], scope: AgentDataScope, namespace: str
) -> AgentProfile:
    """定義とデータの範囲から、目標の業務 Agent（検証済みの型）を作る。"""
    name = str(definition["name"])
    if namespace:
        name = f"{NAME_PREFIX}{namespace}_{name.removeprefix(NAME_PREFIX)}"
    return AgentProfile.model_validate(
        {**definition, "name": name, "data_scopes": {RAG_CONNECTION_ID: scope.model_dump()}}
    )


def _differences(agent: AgentProfile, target: AgentProfile) -> list[str]:
    return [field for field in DEFINITION_FIELDS if getattr(agent, field) != getattr(target, field)]


def provision(
    repository: AgentRuntimeRepositoryContract,
    definitions: Mapping[str, Mapping[str, Any]],
    scopes: Mapping[str, AgentDataScope],
    *,
    namespace: str = "",
    publish_refusal: Callable[[AgentProfile], str | None],
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """業務 Agent を作成・更新して公開する（冪等）。戻り値の `changed` は保存先に書いたか。"""
    if namespace and not KEY_PATTERN.fullmatch(namespace):
        raise ProvisionError("名前の区分は英数字・_・- の 32 字以内にしてください。")
    missing = sorted(set(definitions) - set(scopes))
    if missing:
        raise ProvisionError(
            f"検索・回答プロファイルの ID が無いグループがあります: {', '.join(missing)}"
        )
    result: dict[str, Any] = {"namespace": namespace, "groups": {}, "changed": False}
    for key in sorted(definitions):
        target = desired_agent(definitions[key], scopes[key], namespace)
        found = [agent for agent in repository.list_agents() if agent.name == target.name]
        if len(found) > 1:
            raise ProvisionError(
                f"同じ名前の業務 Agent が複数あります（グループ {key}）。自動では選びません。"
            )
        try:
            agent = _ensure_agent(repository, key, target, found, result, log)
            if agent.has_unpublished_changes():
                refusal = publish_refusal(agent)
                if refusal is not None:
                    raise ProvisionError(
                        f"公開前の評価の門が公開を断りました（グループ {key}）。下書きは保存済み"
                        f"です。{refusal}"
                    )
                agent = repository.publish_agent(agent.id, note=PUBLISH_NOTE)
                result["changed"] = True
                log(f"業務 Agent を公開しました（グループ {key}・v{agent.published_version}）。")
        except (KeyError, ValueError) as exc:
            # repository の検証（スキルが無いなど）の文は画面の 400 と同じで、値を含まない。
            detail = str(exc) if isinstance(exc, ValueError) else "業務 Agent が見つかりません。"
            raise ProvisionError(f"業務 Agent（グループ {key}）を保存できません。{detail}") from exc
        _verify_published(key, agent, target)
        result["groups"][key] = {
            "agent_id": agent.id,
            "published_version": agent.published_version,
        }
    return result


def _ensure_agent(
    repository: AgentRuntimeRepositoryContract,
    key: str,
    target: AgentProfile,
    found: list[AgentProfile],
    result: dict[str, Any],
    log: Callable[[str], None],
) -> AgentProfile:
    """無ければ作り、あれば下書きを定義に合わせる（同じなら書かない）。"""
    if not found:
        # 画面の作成と同じく下書きから始める組織の業務 Agent（ID は `agent_<uuid>`）。
        agent = repository.create_agent(
            target.model_copy(
                update={
                    "versioned": True,
                    "versions": [],
                    "published_version": None,
                    "source": "runtime",
                    "owner_user_uuid": None,
                    "share_request": None,
                }
            )
        )
        result["changed"] = True
        log(f"業務 Agent を作成しました（グループ {key}）。")
        return agent
    agent = found[0]
    if agent.source != "runtime" or agent.owner_user_uuid or agent.migration_required:
        raise ProvisionError(
            f"同名の業務 Agent（グループ {key}）が組み込み・個人の業務 Agent か、移行が要る"
            "状態です。変更せずに停止します。"
        )
    changes = _differences(agent, target)
    if not changes:
        return agent
    agent = repository.patch_agent(
        agent.id, AgentProfilePatch(**{field: getattr(target, field) for field in changes})
    )
    result["changed"] = True
    log(f"業務 Agent の下書きを定義に合わせました（グループ {key}: {', '.join(changes)}）。")
    return agent


def _verify_published(key: str, agent: AgentProfile, target: AgentProfile) -> None:
    """公開中の版と有効 / 無効が定義のとおりか（書いた後にも確かめる）。"""
    published = agent.published()
    if (
        published is None
        or _differences(agent, target)
        or any(
            getattr(published, field) != getattr(target, field) for field in AGENT_VERSIONED_FIELDS
        )
    ):
        raise ProvisionError(
            f"業務 Agent（グループ {key}）の公開中の版が定義と異なります。公開の状態を確認して"
            "ください。"
        )


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _save_state(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _restart_required() -> dict[str, object]:
    """動いている backend（と dispatcher）は、この命令の変更をメモリに持たない（#1916）。"""
    from app.features.agent.storage_bootstrap import BACKEND_PROCESS, DISPATCHER_PROCESS
    from app.settings import get_settings

    processes = [BACKEND_PROCESS]
    if get_settings().agent_runtime_dispatch_mode.strip().lower() != "in_process":
        processes.append(DISPATCHER_PROCESS)
    return {
        "message": (
            "業務 Agent を保存しました。動いているプロセスは、自分で次に保存するまでこの変更を"
            "メモリに読み込みません（一覧・チャットに出ません）。実行中の Run が無いときに"
            "再起動してください。"
        ),
        "processes": processes,
    }


def _open_repository() -> AgentRuntimeRepositoryContract:
    """動いている backend と同じ Oracle の保存先（定義の復元を含む。中断した Run は触らない）。"""
    from app.features.agent import control_plane_store, runtime, storage_backend

    backend = storage_backend.resolved_backend()
    if backend not in storage_backend.ORACLE_BACKENDS:
        raise ProvisionError(
            f"Agent の保存先が Oracle ではありません（{backend}）。動いている backend と共有"
            "できないため、この命令では作りません。画面で作成・公開してください。"
        )
    # 画面で入れたスキル（業務 Agent の検証・公開の版の固定）と評価の履歴（公開前の評価の門）を
    # 読み込む。backend の起動と違い、中断した Run の失敗（#1894）はしない。
    control_plane_store.restore_control_plane()
    return runtime.get_runtime_repository()


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging("agent")
    # DB の停止中・届かないときに、接続記述子の TNS の再試行で長く待たずに失敗を返す（#1779）。
    enable_cli_connect_mode()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--definitions",
        type=Path,
        default=DEFAULT_DEFINITIONS,
        help="業務 Agent の定義（既定は agent/examples/test-pdfs-ja/agents.json）",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rag-state", type=Path, help="RAG の命令の --state のファイル")
    source.add_argument(
        "--profile",
        action="append",
        metavar="GROUP=ID",
        help="グループの検索・回答プロファイルの ID（グループごとに 1 回）",
    )
    parser.add_argument(
        "--namespace", default="", help="名前の区分（--profile のときだけ。state は state の区分）"
    )
    parser.add_argument("--state", type=Path, help="結果（業務 Agent の ID と公開中の版）の保存先")
    args = parser.parse_args(argv)
    try:
        definitions = read_definitions(args.definitions)
        if args.rag_state is not None:
            if args.namespace:
                raise ProvisionError("--rag-state のときは state の名前の区分を使います。")
            scopes, namespace = read_rag_state(args.rag_state)
        else:
            scopes, namespace = parse_profiles(args.profile), args.namespace
        repository = _open_repository()
        result = provision(
            repository,
            definitions,
            scopes,
            namespace=namespace,
            publish_refusal=publish_evaluation_refusal,
            log=lambda message: print(message, file=sys.stderr),  # noqa: T201
        )
        repository.flush_writes()
        data: dict[str, Any] = {
            "groups": result["groups"],
            "namespace": result["namespace"],
            "restart_required": _restart_required() if result["changed"] else None,
        }
        if args.state is not None:
            _save_state(args.state, data)
    except ProvisionError as exc:
        print(_stable_json({"error": {"message": str(exc)}, "ok": False}))  # noqa: T201
        return 1
    except AgentRuntimeStorageUnavailableError as exc:
        # 利用者に返せる文（接続先・資格情報を含まない）。
        print(_stable_json({"error": {"message": str(exc)}, "ok": False}))  # noqa: T201
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        message = f"定義・state のファイルか保存先を確認してください（{type(exc).__name__}）。"
        print(_stable_json({"error": {"message": message}, "ok": False}))  # noqa: T201
        return 1
    except Exception as exc:  # 接続・SQL の失敗は SQL や資格情報を出さずに非 0 で終わる
        message = f"業務 Agent の作成・公開に失敗しました（{type(exc).__name__}）。"
        print(_stable_json({"error": {"message": message}, "ok": False}))  # noqa: T201
        return 1
    print(_stable_json({"data": data, "ok": True}))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
