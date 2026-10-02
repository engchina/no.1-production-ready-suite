"""回答フローの回答品質をまとめて確かめる検証 CLI(rag_poc の検証スクリプトの移植)。

- ``answers``: QA を業務ビューで回答し、標準回答で 4 軸評価する
  (rag_poc の ``scripts/run_answer_eval.py``)。
- ``regression``: rag_poc の ``cases.json`` の質問に回答し、文字列の規則で判定する
  (rag_poc の ``scripts/regression/run_regression.py``)。
- ``crag-goldset``: CRAG goldset をオフラインで評価する
  (rag_poc の ``scripts/evaluate_crag_goldset.py``)。

``answers`` / ``regression`` は実行中の backend の API を呼ぶ(``evaluation_cli`` と同じ)。
1 件ずつ ``<out>/<id>.json`` へ保存し、既にあれば飛ばすので、中断後は同じコマンドで
残りだけ実行できる。
結果には質問と回答の本文が入るため、出力先は Git 管理外(既定は ``.runs/``)にする。

    uv run python -m app.rag.answer_verify_cli answers --qa qa.json --business-view <id> \\
        --out .runs/answers/<label>
    uv run python -m app.rag.answer_verify_cli regression --cases cases.json \\
        --business-view <id> --out .runs/regression/<label> --repeat 2
    uv run python -m app.rag.answer_verify_cli crag-goldset crag_goldset.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from pr_backend_core.internal_http import http_client_options

from app.rag.evaluation_cli import _request_headers

DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 600.0

# 回答本文の節見出し(rag_poc の run_regression と同じ)。括弧付きの見出しもある。
_SECTION_HEADERS = (
    "確認できる内容",
    "操作手順",
    "確認手順",
    "資料の記載（今回への適用は未確認）",
    "資料からは確認できない点",
)
# 適用扱いにしない節(原文のみ提示と gap)。
_NOT_APPLIED = {"資料の記載（今回への適用は未確認）", "資料からは確認できない点"}
_GAP = "資料からは確認できない点"


class VerifyError(RuntimeError):
    """1 件の実行に失敗した理由(記録して次の件へ進む)。"""


class RagApi:
    """検証で使う backend API(検索と、保存した回答の評価)。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        headers: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "application/json", **headers},
            transport=transport,
            **http_client_options(base_url),
        )

    def close(self) -> None:
        self._client.close()

    def search(self, question: str, business_view_id: str) -> dict[str, Any]:
        return self._post("/api/search", {"query": question, "business_view_id": business_view_id})

    def evaluate(self, trace_id: str, standard_answer: str) -> dict[str, Any]:
        return self._post(
            f"/api/search/answers/{trace_id}/evaluation", {"standard_answer": standard_answer}
        )

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.RequestError as exc:
            raise VerifyError(f"API に接続できませんでした: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            messages = body.get("error_messages") if isinstance(body, dict) else None
            detail = messages[0] if messages else f"HTTP {response.status_code}"
            raise VerifyError(f"{path} が失敗しました: {detail}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise VerifyError(f"{path} の応答に data がありません。")
        return data


# ---- answers ---------------------------------------------------------------------------------


def run_answers(
    api: RagApi,
    items: Sequence[Mapping[str, Any]],
    *,
    business_view_id: str,
    out: Path,
    log: Callable[[str], None] = print,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for item in items:
        path = out / f"{item['id']}.json"
        if path.exists():
            log(f"skip {path.name} (exists)")
            continue
        log(f"run  {item['id']} ({time.strftime('%H:%M:%S')})")
        started = time.time()
        record: dict[str, Any] = {"id": item["id"], "question": item["question"]}
        try:
            answer = api.search(str(item["question"]), business_view_id)
            record.update(trace_id=answer.get("trace_id"), answer=answer.get("answer", ""))
            # 回答の記録を残すのは根拠付き回答だけ(安全ポリシーで止めた質問は "blocked")。
            answer_path = (answer.get("diagnostics") or {}).get("retrieval_strategy_adapter")
            if answer_path != "grounded":
                raise VerifyError(
                    "回答の記録が無い回答(安全ポリシーで止めた質問など)のため、"
                    "標準回答で評価できません。"
                )
            detail = api.evaluate(str(answer["trace_id"]), str(item["standard_answer"]))
            record["evaluation"] = detail.get("evaluation") or {}
        except VerifyError as exc:
            record["error"] = str(exc)
        record["seconds"] = round(time.time() - started)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        evaluation = record.get("evaluation") or {}
        log(
            f"     {evaluation.get('status', 'error')} {_verdict(evaluation)} "
            f"{record.get('error', '')}".rstrip()
        )


def _verdict(evaluation: Mapping[str, Any]) -> str:
    passed = evaluation.get("passed")
    return "—" if passed is None else ("合格" if passed else "不合格")


def _failed_metrics(evaluation: Mapping[str, Any]) -> str:
    """閾値に届かなかった評価の基準の指標(#680)。"""
    names = [
        str(metric.get("name"))
        for metric in evaluation.get("metrics") or []
        if isinstance(metric, Mapping) and metric.get("passed") is False
    ]
    return ", ".join(names) or "—"


def summarize_answers(items: Sequence[Mapping[str, Any]], out: Path) -> str:
    rows = ["| id | 状態 | 合否 | 閾値に届かない指標 |", "|---|---|---|---|"]
    passed = completed = 0
    for item in items:
        path = out / f"{item['id']}.json"
        if not path.exists():
            rows.append(f"| {item['id']} | 未実行 | — | — |")
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        evaluation = record.get("evaluation") or {}
        status = "エラー" if record.get("error") else str(evaluation.get("status") or "—")
        if evaluation.get("status") == "completed":
            completed += 1
            passed += bool(evaluation.get("passed"))
        rows.append(
            f"| {item['id']} | {status} | {_verdict(evaluation)} | {_failed_metrics(evaluation)} |"
        )
    rate = f"{passed / completed:.0%}" if completed else "—"
    text = f"評価完了 {completed} / {len(items)} 件、合格率 {rate}\n\n" + "\n".join(rows) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


# ---- regression ------------------------------------------------------------------------------


def split_sections(answer_text: str) -> dict[str, str]:
    """回答本文を節見出しで分ける。先頭の結論文は "summary" に入れる(rag_poc と同じ)。"""
    sections: dict[str, list[str]] = {"summary": []}
    current = "summary"
    for line in (answer_text or "").splitlines():
        stripped = line.strip()
        header = next(
            (
                h
                for h in _SECTION_HEADERS
                if stripped == h or stripped.startswith(h + "（") or stripped.startswith(h + "(")
            ),
            None,
        )
        if header:
            current = header
            sections.setdefault(current, [])
            # 見出しの括弧内は機能名。どの画面の手順かは回答の一部なので、節の先頭に残す。
            title = stripped[len(header) :].strip("（）()")
            if title:
                sections[current].append(title)
            continue
        if stripped.startswith("根拠："):
            continue
        sections.setdefault(current, []).append(stripped)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def judge(answer_text: str, expect: Mapping[str, Any]) -> dict[str, Any]:
    """expect の規則をすべて満たせば pass。規則ごとの結果を返す(rag_poc と同じ)。"""
    sections = split_sections(answer_text)
    applied = _compact(
        "\n".join(text for name, text in sections.items() if name not in _NOT_APPLIED)
    )
    gap = _compact(sections.get(_GAP, ""))
    checks: list[dict[str, Any]] = []
    for group in expect.get("applied_all", []):
        missing = [term for term in group if _compact(term) not in applied]
        checks.append(
            {"rule": "applied_all", "terms": group, "ok": not missing, "missing": missing}
        )
    for term in expect.get("applied_excludes", []):
        checks.append(
            {"rule": "applied_excludes", "terms": [term], "ok": _compact(term) not in applied}
        )
    for term in expect.get("gap_contains", []):
        checks.append({"rule": "gap_contains", "terms": [term], "ok": _compact(term) in gap})
    return {"passed": all(check["ok"] for check in checks), "checks": checks}


def run_regression(
    api: RagApi,
    cases: Sequence[Mapping[str, Any]],
    *,
    business_view_id: str,
    out: Path,
    repeat: int,
    log: Callable[[str], None] = print,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        for n in range(1, repeat + 1):
            path = out / f"{case['id']}-{n}.json"
            if path.exists():
                log(f"skip {path.name} (exists)")
                continue
            log(f"run  {case['id']} #{n} ({time.strftime('%H:%M:%S')})")
            started = time.time()
            record: dict[str, Any]
            try:
                answer = api.search(str(case["question"]), business_view_id)
                details = (answer.get("diagnostics") or {}).get("answer") or {}
                record = {
                    "trace_id": answer.get("trace_id"),
                    "answer_text": answer.get("answer", ""),
                    "confidence": details.get("confidence", ""),
                    "needs_human_review": details.get("needs_human_review"),
                    "insufficient_reason": details.get("insufficient_reason", ""),
                }
            except VerifyError as exc:  # 1 問の失敗で回帰全体を止めない。再実行で再開する。
                record = {"error": str(exc)[:500], "answer_text": ""}
            record.update(
                id=case["id"],
                repeat=n,
                question=case["question"],
                seconds=round(time.time() - started),
                judge=judge(record.get("answer_text", ""), case.get("expect") or {}),
            )
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            verdict = "pass" if record["judge"]["passed"] else "FAIL"
            log(f"     {verdict} {record.get('confidence', '')} {record.get('error', '')}".rstrip())


def summarize_regression(cases: Sequence[Mapping[str, Any]], out: Path, repeat: int) -> str:
    header = (
        "| id | " + " | ".join(f"#{n}" for n in range(1, repeat + 1)) + " | 通過 | 失敗した規則 |"
    )
    lines = [header, "|---|" + "---|" * (repeat + 2)]
    for case in cases:
        cells: list[str] = []
        passed = 0
        failed_rules: list[str] = []
        for n in range(1, repeat + 1):
            path = out / f"{case['id']}-{n}.json"
            if not path.exists():
                cells.append("—")
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            ok = bool(record["judge"]["passed"])
            passed += ok
            suffix = " (error)" if record.get("error") else f" ({record.get('confidence', '')})"
            cells.append(("pass" if ok else "FAIL") + suffix)
            failed_rules += [
                f"{check['rule']}:{'/'.join(check['terms'])}"
                for check in record["judge"]["checks"]
                if not check["ok"]
            ]
        lines.append(
            f"| {case['id']} | "
            + " | ".join(cells)
            + f" | {passed}/{repeat} | "
            + "、".join(dict.fromkeys(failed_rules))
            + " |"
        )
    text = "\n".join(lines) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


# ---- crag-goldset ----------------------------------------------------------------------------


def run_crag_goldset(goldset: Path, *, llm_judge: bool, log: Callable[[str], None] = print) -> int:
    """CRAG goldset を評価し、rag_poc と同じ条件で終了コードを返す(CRAG が劣れば 1)。"""
    from rag_engine.evaluation.crag_eval import (
        evaluate_crag_goldset,
        format_crag_eval_summary,
        llm_crag_judge,
        load_crag_goldset,
    )

    judge_fn = None
    work = None
    if llm_judge:
        from app.config import get_settings
        from app.rag.answer_engine import build_engine_settings

        work = tempfile.TemporaryDirectory(prefix="rag-engine-crag-eval-")
        judge_fn = llm_crag_judge(build_engine_settings(get_settings(), output_dir=Path(work.name)))
    try:
        summary = evaluate_crag_goldset(load_crag_goldset(goldset), judge_fn)
    finally:
        if work is not None:
            work.cleanup()
    log(format_crag_eval_summary(summary))
    variants = summary["variants"]
    if variants["crag"]["retrieval_hit_rate"] < variants["standard_rag"]["retrieval_hit_rate"]:
        return 1
    if variants["crag_rerank"]["retrieval_hit_rate"] < variants["crag"]["retrieval_hit_rate"]:
        return 1
    if variants["crag"]["crag_score"] < variants["standard_rag"]["crag_score"]:
        return 1
    return 0


# ---- entrypoint ------------------------------------------------------------------------------


def _load_items(path: Path, key: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise SystemExit(f"{path} に {key} の配列がありません。")
    return [item for item in items if isinstance(item, dict)]


def _only(items: list[dict[str, Any]], only: str) -> list[dict[str, Any]]:
    wanted = {value.strip() for value in only.split(",") if value.strip()}
    return [item for item in items if str(item["id"]) in wanted] if wanted else items


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-answer-verify",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def api_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--api-base-url",
            default=os.getenv("RAG_EVALUATION_API_BASE_URL") or DEFAULT_API_BASE_URL,
        )
        sub.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
        sub.add_argument("--tenant-id", default=os.getenv("RAG_EVALUATION_TENANT_ID"))
        sub.add_argument("--user-id", default=os.getenv("RAG_EVALUATION_USER_ID"))
        sub.add_argument("--business-view", required=True, help="回答に使う業務ビューの ID")
        sub.add_argument("--out", type=Path, required=True, help="結果の保存先(再開に使う)")
        sub.add_argument("--only", default="", help="カンマ区切りの id。指定した件だけ実行")
        sub.add_argument("--summary-only", action="store_true", help="実行せず summary.md だけ作る")

    answers = subparsers.add_parser("answers", help="QA を回答し標準回答で 4 軸評価する")
    answers.add_argument("--qa", type=Path, required=True, help="id / question / standard_answer")
    api_options(answers)

    regression = subparsers.add_parser("regression", help="cases.json の質問を回答し規則で判定する")
    regression.add_argument("--cases", type=Path, required=True)
    regression.add_argument("--repeat", type=int, default=2)
    api_options(regression)

    goldset = subparsers.add_parser("crag-goldset", help="CRAG goldset をオフラインで評価する")
    goldset.add_argument("goldset", type=Path)
    goldset.add_argument(
        "--llm-judge", action="store_true", help="模範解答との比較を LLM で判定する"
    )
    return parser


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "crag-goldset":
        return run_crag_goldset(args.goldset, llm_judge=args.llm_judge)

    api = RagApi(
        args.api_base_url,
        timeout=args.timeout,
        headers=_request_headers(args.tenant_id, args.user_id),
        transport=transport,
    )
    try:
        if args.command == "answers":
            items = _only(_load_items(args.qa, "items"), args.only)
            if not args.summary_only:
                run_answers(api, items, business_view_id=args.business_view, out=args.out)
            print(summarize_answers(items, args.out))
        else:
            cases = _only(_load_items(args.cases, "cases"), args.only)
            if not args.summary_only:
                run_regression(
                    api,
                    cases,
                    business_view_id=args.business_view,
                    out=args.out,
                    repeat=args.repeat,
                )
            print(summarize_regression(cases, args.out, args.repeat))
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
