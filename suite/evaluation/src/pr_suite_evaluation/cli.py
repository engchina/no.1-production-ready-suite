"""製品横断の評価の CLI（`suite-evaluation`。#3240）。

    export SUITE_EVALUATION_API_KEY=prak_...   # 使える製品に agent を含む API キー
    uv run suite-evaluation run ../../rag/evaluation/business-support/business-support.json \\
        --document-map ../../tmp/eval/document-map.json \\
        --agent-mcp-url http://127.0.0.1:8020/api/mcp --agent-id <公開済みの業務 Agent> \\
        --guides ../../rag/evaluation/business-support/support-guides.json \\
        --output ../../tmp/eval/d.json
    uv run suite-evaluation score ../../tmp/eval/d.json \\
        --dataset ../../rag/evaluation/business-support/business-support.json \\
        --document-map ../../tmp/eval/document-map.json --output ../../tmp/eval/d.rescored.json
    uv run suite-evaluation compare A=../../tmp/eval/a.json D=../../tmp/eval/d.json

- `run`: 評価セットを Target（今は業務 Agent の公開の MCP）に流し、観測を保存して採点する。
- `score`: 保存した観測を、モデル・製品を呼ばずに採点し直す（規則・期待を直したとき）。
- `compare`: 評価の結果（製品の評価の結果か、suite の結果）を比べる表を出す。

終了コード: 0 成功 / 2 入力・接続の失敗（ケースの失敗は結果に記録し、終了コードにしない）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from pr_suite_evaluation.dataset import (
    DatasetError,
    LoadedDataset,
    condition_labels,
    load_dataset,
    load_document_map,
)
from pr_suite_evaluation.experiment import (
    ExperimentRecord,
    ScorerConfig,
    rescore,
    run_experiment,
)
from pr_suite_evaluation.mcp_client import McpClient, TargetError
from pr_suite_evaluation.report import summarize_result, summary_markdown
from pr_suite_evaluation.targets.agent_mcp import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    MAX_WAIT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    AgentEvaluationError,
    AgentMcp,
    AgentMcpTarget,
    AgentTargetConfig,
)

DEFAULT_AGENT_MCP_URL = "http://localhost:8020/api/mcp"
# API キーを読む環境変数（キーを引数に書かない。シェルの履歴・プロセスの一覧に残さない）。
DEFAULT_API_KEY_ENV = "SUITE_EVALUATION_API_KEY"


class CliError(RuntimeError):
    """利用者へ返す失敗（exit code 2）。"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="suite-evaluation", description="製品横断の評価（Dataset / Target / Report）"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="評価セットを業務 Agent の公開の MCP に流して採点する")
    run.add_argument("dataset", type=Path, help="評価セット（JSON）")
    run.add_argument(
        "--document-map",
        type=Path,
        help=(
            "評価セットの file:<ファイル名> を配備先の文書の ID にする対応表"
            '（{"documents": {...}}）'
        ),
    )
    run.add_argument(
        "--agent-mcp-url", default=DEFAULT_AGENT_MCP_URL, help="Agent の MCP（POST /api/mcp）"
    )
    run.add_argument(
        "--agent-id", required=True, help="使う業務 Agent（公開済み。agent_list の id）"
    )
    run.add_argument(
        "--api-key-env",
        default=DEFAULT_API_KEY_ENV,
        help=f"MCP を呼ぶ API キーを読む環境変数（既定 {DEFAULT_API_KEY_ENV}）",
    )
    run.add_argument("--guides", type=Path, help="既知の条件の名前に使う業務ガイド（JSON）")
    run.add_argument("--case", action="append", default=[], help="流すケースの id（複数可）")
    run.add_argument("--split", choices=["dev", "holdout"], help="流す区分")
    run.add_argument("--run-timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS)
    run.add_argument(
        "--infer-missing-outcome",
        action="store_true",
        help="outcome の無い完了の Run を回答の文から推定する（旧形式の互換。既定は評価不能）",
    )
    run.add_argument("--output", type=Path, required=True, help="結果（JSON）の出力先")

    score = commands.add_parser("score", help="保存した観測を採点し直す（製品を呼ばない）")
    score.add_argument("record", type=Path, help="run の結果（JSON）")
    score.add_argument("--dataset", type=Path, required=True, help="採点に使う評価セット")
    score.add_argument("--document-map", type=Path, help="文書の対応表")
    score.add_argument("--case", action="append", default=[], help="採点するケースの id")
    score.add_argument("--infer-missing-outcome", action="store_true")
    score.add_argument("--output", type=Path, required=True, help="採点し直した結果の出力先")

    compare = commands.add_parser("compare", help="評価の結果を比べる表を出す")
    compare.add_argument("results", nargs="+", help="<名前>=<結果の JSON>（例: A=a.json）")
    compare.add_argument("--json", action="store_true", help="表ではなく JSON で出す")
    return parser


def _api_key(name: str) -> str:
    key = os.environ.get(name, "").strip()
    if not key:
        raise CliError(
            f"環境変数 {name} に MCP の API キー（使える製品に agent を含むキー）を"
            "設定してください。"
        )
    return key


def _dataset(path: Path, document_map: Path | None) -> LoadedDataset:
    documents = load_document_map(document_map) if document_map else None
    return load_dataset(path, documents=documents)


def _write(path: Path, record: ExperimentRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)


def _selected(loaded: LoadedDataset, case_ids: Sequence[str], split: str | None) -> LoadedDataset:
    if not case_ids and split is None:
        return loaded
    dataset = loaded.dataset.select(case_ids, split)
    return LoadedDataset(dataset=dataset, name=loaded.name, sha256=loaded.sha256)


def _run_command(args: argparse.Namespace) -> int:
    loaded = _selected(_dataset(args.dataset, args.document_map), args.case, args.split)
    labels = (
        condition_labels(json.loads(args.guides.read_text(encoding="utf-8"))) if args.guides else {}
    )
    api_key = _api_key(args.api_key_env)
    config = AgentTargetConfig(
        mcp_url=args.agent_mcp_url, agent_id=args.agent_id, run_timeout_seconds=args.run_timeout
    )
    scorer = ScorerConfig(
        infer_missing_outcome=args.infer_missing_outcome, run_timeout_seconds=args.run_timeout
    )
    # Run の待ちは MCP の wait_seconds（最大 300 秒）なので、HTTP の timeout はそれより長くする。
    with httpx.Client(
        timeout=MAX_WAIT_SECONDS + REQUEST_TIMEOUT_SECONDS, trust_env=False
    ) as client:
        mcp = McpClient(
            client,
            args.agent_mcp_url,
            api_key=api_key,
            label="Agent",
            error_class=AgentEvaluationError,
        )
        target = AgentMcpTarget(AgentMcp(mcp), config, labels=labels)
        record = run_experiment(
            loaded,
            target,
            target_config=config.provenance(),
            scorer=scorer,
            log=lambda line: print(line, file=sys.stderr),
        )
    _write(args.output, record)
    return 0


def _score_command(args: argparse.Namespace) -> int:
    try:
        record = ExperimentRecord.model_validate_json(args.record.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise CliError(f"結果のファイルの形が不正です: {error.error_count()} 件") from error
    loaded = _dataset(args.dataset, args.document_map)
    if args.case:
        record = record.model_copy(
            update={
                "observations": [item for item in record.observations if item.case_id in args.case]
            }
        )
    target = record.provenance.get("target") or {}
    timeout = target.get("run_timeout_seconds") if isinstance(target, Mapping) else None
    scorer = ScorerConfig(
        target_kind=str(target.get("kind") or "agent_mcp")
        if isinstance(target, Mapping)
        else "agent_mcp",
        infer_missing_outcome=args.infer_missing_outcome,
        run_timeout_seconds=float(timeout)
        if isinstance(timeout, int | float)
        else DEFAULT_RUN_TIMEOUT_SECONDS,
    )
    try:
        rescored = rescore(record, loaded, scorer)
    except ValueError as error:
        raise CliError(str(error)) from error
    _write(args.output, rescored)
    return 0


def load_result(path: Path) -> Mapping[str, Any]:
    """評価の結果（suite の結果の report、製品の評価の結果の data、またはその中身）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        for key in ("report", "data"):
            if isinstance(payload.get(key), Mapping):
                return payload[key]  # type: ignore[no-any-return]
        return payload
    raise CliError(f"評価の結果の形が読めません: {path.name}")


def _compare_command(args: argparse.Namespace) -> int:
    summaries: dict[str, Mapping[str, Any]] = {}
    for item in args.results:
        label, separator, path = item.partition("=")
        if not separator or not label or not path:
            raise CliError(f"<名前>=<結果の JSON> の形で渡してください: {item}")
        summaries[label] = summarize_result(load_result(Path(path)))
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        print(summary_markdown(summaries), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run_command(args)
        if args.command == "score":
            return _score_command(args)
        return _compare_command(args)
    except (
        CliError,
        DatasetError,
        TargetError,
        OSError,
        json.JSONDecodeError,
        httpx.HTTPError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
