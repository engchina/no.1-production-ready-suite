"""回答生成で使われた質問履歴の保存、集計、候補提示を扱う。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


QUERY_HISTORY_SCHEMA_VERSION = 1
QUERY_HISTORY_DIR_NAME = "query_history"
QUERY_HISTORY_FILE_NAME = "queries.jsonl"
QUERY_HISTORY_BLOCKLIST_FILE_NAME = "blocklist.txt"
QUERY_HISTORY_DEFAULT_RETENTION_DAYS = 90
QUERY_HISTORY_DEFAULT_MIN_COUNT = 3
QUERY_HISTORY_DEFAULT_MIN_UNIQUE_RUNS = 1
QUERY_HISTORY_DEFAULT_SUGGESTION_LIMIT = 5
QUERY_HISTORY_MAX_QUESTION_CHARS = 500


@dataclass(frozen=True)
class QueryHistoryRecord:
    """質問履歴 JSONL の 1 レコードを表します。"""
    query_id: str
    created_at: str
    question: str
    normalized_question: str
    run_id: str = ""
    answer_id: str = ""
    retrieval_scope: str = ""
    classification_filter: dict[str, Any] | None = None


@dataclass(frozen=True)
class QueryHistorySuggestion:
    """履歴頻度から提示する質問候補を保持します。"""
    question: str
    count: int
    unique_run_count: int
    last_seen_at: str
    score: float


def query_history_path(output_dir: str | Path) -> Path:
    """output_dir 配下の質問履歴 JSONL パスを返します。"""
    return Path(output_dir) / QUERY_HISTORY_DIR_NAME / QUERY_HISTORY_FILE_NAME


def query_history_blocklist_path(output_dir: str | Path) -> Path:
    """output_dir 配下の質問候補 blocklist パスを返します。"""
    return Path(output_dir) / QUERY_HISTORY_DIR_NAME / QUERY_HISTORY_BLOCKLIST_FILE_NAME


def append_query_history(
    *,
    output_dir: str | Path,
    question: str,
    enabled: bool,
    run_id: str = "",
    answer_id: str = "",
    retrieval_scope: str = "",
    classification_filter: dict[str, Any] | None = None,
    blocklist: Iterable[str] = (),
) -> Path | None:
    """回答生成で使った質問を履歴 JSONL に追記します。"""
    if not enabled:
        return None
    clean_question = _clean_question(question)
    if not clean_question or _matches_blocklist(clean_question, blocklist):
        return None

    created_at = _utc_now()
    record = {
        "schema_version": QUERY_HISTORY_SCHEMA_VERSION,
        "query_id": _create_query_id(clean_question, run_id, answer_id, created_at),
        "created_at": created_at,
        "question": clean_question,
        "normalized_question": _normalize_key(clean_question),
        "run_id": str(run_id or "").strip(),
        "answer_id": str(answer_id or "").strip(),
        "retrieval_scope": str(retrieval_scope or "").strip(),
        "classification_filter": dict(classification_filter or {}),
    }
    path = query_history_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return path


def load_query_history_records(
    output_dir: str | Path,
    *,
    retention_days: int = QUERY_HISTORY_DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[QueryHistoryRecord]:
    """保持期間内の質問履歴を読み込みます。"""
    path = query_history_path(output_dir)
    if not path.exists():
        return []
    cutoff = _retention_cutoff(retention_days, now or datetime.now(timezone.utc))
    records: list[QueryHistoryRecord] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            raw_record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        record = _query_history_record_from_raw(raw_record)
        if record is None:
            continue
        created_at = _parse_timestamp(record.created_at)
        if cutoff is not None and (created_at is None or created_at < cutoff):
            continue
        records.append(record)
    return records


def suggest_query_history_questions(
    question: str,
    records: Sequence[QueryHistoryRecord],
    *,
    classification_filter: Any = None,
    min_count: int = QUERY_HISTORY_DEFAULT_MIN_COUNT,
    min_unique_runs: int = QUERY_HISTORY_DEFAULT_MIN_UNIQUE_RUNS,
    limit: int = QUERY_HISTORY_DEFAULT_SUGGESTION_LIMIT,
    blocklist: Iterable[str] = (),
) -> list[QueryHistorySuggestion]:
    """履歴頻度と分類条件から再利用候補質問を提示します。"""
    groups = _group_history_records(records, classification_filter=classification_filter, blocklist=blocklist)
    suggestions: list[QueryHistorySuggestion] = []
    for group in groups.values():
        if group["count"] < max(1, int(min_count)):
            continue
        unique_run_count = len(group["run_ids"])
        if unique_run_count < max(1, int(min_unique_runs)):
            continue
        score = _query_suggestion_score(question, str(group["question"]))
        if question and score <= 0:
            continue
        suggestions.append(
            QueryHistorySuggestion(
                question=str(group["question"]),
                count=int(group["count"]),
                unique_run_count=unique_run_count,
                last_seen_at=str(group["last_seen_at"]),
                score=score,
            )
        )
    suggestions.sort(key=lambda item: (-item.score, -item.count, item.question))
    return suggestions[: max(0, int(limit))]


def load_query_suggestion_blocklist(
    output_dir: str | Path,
    *,
    blocklist_path: str | Path | None = None,
    extra_values: Iterable[str] = (),
) -> set[str]:
    """質問候補から除外する blocklist を読み込みます。"""
    values = {str(value).strip() for value in extra_values if str(value).strip()}
    path = Path(blocklist_path) if blocklist_path else query_history_blocklist_path(output_dir)
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if item and not item.startswith("#"):
                values.add(item)
    except OSError:
        pass
    return values


def query_history_suggestion_choices(suggestions: Sequence[QueryHistorySuggestion]) -> list[list[str]]:
    """履歴候補を Gradio choice value へ変換します。"""
    return [[suggestion.question] for suggestion in suggestions]


def query_history_suggestion_labels(suggestions: Sequence[QueryHistorySuggestion]) -> list[str]:
    """履歴候補を Gradio 表示ラベルへ変換します。"""
    return [f"{suggestion.question} / {suggestion.count}件" for suggestion in suggestions]


def select_query_history_suggestion(suggestion_value: Any) -> str:
    """選択された履歴候補 value から質問文字列を取り出します。"""
    if isinstance(suggestion_value, (list, tuple)):
        if not suggestion_value:
            return ""
        first = suggestion_value[0]
        if isinstance(first, (list, tuple)):
            return select_query_history_suggestion(first)
        return str(first or "").strip()
    return str(suggestion_value or "").strip()


def _query_history_record_from_raw(raw_record: Any) -> QueryHistoryRecord | None:
    if not isinstance(raw_record, dict) or raw_record.get("schema_version") != QUERY_HISTORY_SCHEMA_VERSION:
        return None
    question = _clean_question(raw_record.get("question"))
    if not question:
        return None
    normalized_question = str(raw_record.get("normalized_question") or _normalize_key(question)).strip()
    return QueryHistoryRecord(
        query_id=str(raw_record.get("query_id") or "").strip(),
        created_at=str(raw_record.get("created_at") or "").strip(),
        question=question,
        normalized_question=normalized_question,
        run_id=str(raw_record.get("run_id") or "").strip(),
        answer_id=str(raw_record.get("answer_id") or "").strip(),
        retrieval_scope=str(raw_record.get("retrieval_scope") or "").strip(),
        classification_filter=_dict_or_none(raw_record.get("classification_filter")),
    )


def _group_history_records(
    records: Sequence[QueryHistoryRecord],
    *,
    classification_filter: Any,
    blocklist: Iterable[str],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        if _matches_blocklist(record.question, blocklist):
            continue
        if not _classification_matches(record.classification_filter or {}, classification_filter):
            continue
        key = record.normalized_question or _normalize_key(record.question)
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                "question": record.question,
                "count": 0,
                "run_ids": set(),
                "last_seen_at": record.created_at,
            },
        )
        group["count"] += 1
        if record.run_id:
            group["run_ids"].add(record.run_id)
        group["last_seen_at"] = max(str(group["last_seen_at"]), record.created_at)
    return groups


def _classification_matches(record_filter: dict[str, Any], active_filter: Any) -> bool:
    filter_metadata = _filter_metadata(active_filter)
    if not filter_metadata:
        return True
    for key in ("large_category", "middle_category", "small_category"):
        expected = str(filter_metadata.get(key) or "").strip()
        if not expected:
            continue
        actual = str(record_filter.get(key) or "").strip()
        if actual != expected:
            return False
    return True


def _query_suggestion_score(question: str, candidate: str) -> float:
    query_key = _normalize_key(question)
    candidate_key = _normalize_key(candidate)
    if not query_key:
        return 1.0
    if not candidate_key:
        return 0.0
    if query_key == candidate_key:
        return 1.0
    if candidate_key.startswith(query_key):
        return 0.97
    if query_key in candidate_key:
        return 0.9

    query_units = _search_units(question)
    candidate_units = _search_units(candidate)
    if not query_units or not candidate_units:
        return 0.0
    overlap = len(query_units & candidate_units)
    if not overlap:
        return 0.0
    union = len(query_units | candidate_units)
    containment = overlap / len(query_units)
    jaccard = overlap / union if union else 0.0
    return max(jaccard, containment * 0.75)


def _search_units(value: str) -> set[str]:
    normalized = _normalize_text(value)
    compact = re.sub(r"\s+", "", normalized)
    units = {token for token in re.findall(r"[a-z0-9_.-]+", normalized) if token}
    if compact:
        units.add(compact)
    for width in (2, 3):
        if len(compact) >= width:
            units.update(compact[index : index + width] for index in range(len(compact) - width + 1))
    return units


def _matches_blocklist(question: str, blocklist: Iterable[str]) -> bool:
    normalized_question = _normalize_key(question)
    return any(_normalize_key(item) in normalized_question for item in blocklist if _normalize_key(item))


def _retention_cutoff(retention_days: int, now: datetime) -> datetime | None:
    if int(retention_days) <= 0:
        return None
    return now - timedelta(days=int(retention_days))


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _create_query_id(question: str, run_id: str, answer_id: str, created_at: str) -> str:
    seed = f"{question}\0{run_id}\0{answer_id}\0{created_at}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_question(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= QUERY_HISTORY_MAX_QUESTION_CHARS:
        return text
    return text[:QUERY_HISTORY_MAX_QUESTION_CHARS].rstrip()


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_text(value))


def _filter_metadata(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_metadata"):
        value = value.to_metadata()
    return dict(value) if isinstance(value, dict) else {}


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None
