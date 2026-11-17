"""登録済みQ&A（質問と回答）の登録・候補の照合・semantic cache・feedback からの登録を扱う。

FAQ の項目は質問（QUESTION）と回答（ANSWER）だけ（#5107）。候補は質問と FAQ の質問の文字列の類似度と、
指定時は embedding の意味の類似度の高いほうで並べる。
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import re
import shutil
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path.cwd()
APPROVED_FAQ_FILE = PROJECT_ROOT / "knowledge_assets" / "approved_faq" / "approved_faq_golden_qa.json"
APPROVED_FAQ_SCHEMA_VERSION = 1
APPROVED_FAQ_APPROVED_STATUS = "approved"
APPROVED_FAQ_BACKUP_DIR_NAME = "approved_faq_backups"
APPROVED_FAQ_IMPORT_MODE_INSERT = "INSERT"
APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT = "DELETE_THEN_INSERT"
APPROVED_FAQ_IMPORT_MODES = (
    APPROVED_FAQ_IMPORT_MODE_INSERT,
    APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT,
)
# Excel 取込で読む列（ほかの列は読まない。#5107）。
APPROVED_FAQ_EXCEL_COLUMNS = ("QUESTION", "ANSWER")
DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT = 5
DEFAULT_APPROVED_FAQ_MIN_SCORE = 0.1
DEFAULT_APPROVED_FAQ_SEMANTIC_MIN_SCORE = 0.72
APPROVED_FAQ_FEEDBACK_SOURCE_FILE = "answer_feedback"
APPROVED_FAQ_SEMANTIC_CACHE_SCHEMA_VERSION = 1
MAX_APPROVED_FAQ_QUESTION_LENGTH = 1000
MAX_APPROVED_FAQ_ANSWER_LENGTH = 20000


@dataclass(frozen=True)
class ApprovedFaqRecord:
    """登録済みQ&A 1 件（質問と回答。status・source・timestamps は保存の管理の値）。"""

    id: str
    question: str
    approved_answer: str
    status: str = ""
    source: dict[str, Any] | None = None
    timestamps: dict[str, Any] | None = None


@dataclass(frozen=True)
class ApprovedFaqSuggestion:
    """質問に対する FAQ の候補と一致度（文字列と意味の高いほう）。"""

    record: ApprovedFaqRecord
    score: float
    text_score: float = 0.0
    semantic_score: float | None = None
    match_method: str = "text"


@dataclass(frozen=True)
class ApprovedFaqSemanticItem:
    """FAQ semantic index 内の 1 質問 embedding を保持します。"""

    record_id: str
    question: str
    question_hash: str
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class ApprovedFaqSemanticIndex:
    """FAQ semantic matching 用 cache の内容と署名を保持します。"""

    model: str
    dimensions: int
    records_signature: str
    items: tuple[ApprovedFaqSemanticItem, ...] = ()


@dataclass(frozen=True)
class ApprovedFaqImportRow:
    """取込む FAQ の質問と回答（Excel は読んだシートと行の番号も持つ）。"""

    question: str
    approved_answer: str
    source_file: str = ""
    sheet: str = ""
    row: int = 0


@dataclass(frozen=True)
class ApprovedFaqSaveResult:
    """FAQ JSON 保存時のパス、backup、件数を保持します。"""

    path: Path
    backup_path: Path | None
    record_count: int
    saved_at: datetime


@dataclass(frozen=True)
class ApprovedFaqMutationResult:
    """FAQ の追加・削除・取込操作の結果を UI 向けに保持します。"""

    path: Path
    backup_path: Path | None
    record_count: int
    inserted_count: int
    deleted_count: int
    skipped_count: int
    saved_at: datetime


def load_approved_faq_payload(path: str | Path = APPROVED_FAQ_FILE) -> dict[str, Any]:
    """Approved FAQ JSON の payload を安全に読み込みます。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _default_approved_faq_payload()
    if not isinstance(payload, dict) or payload.get("schema_version") != APPROVED_FAQ_SCHEMA_VERSION:
        return _default_approved_faq_payload()
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        payload = dict(payload)
        payload["records"] = []
    return payload


def load_approved_faq_records(path: str | Path = APPROVED_FAQ_FILE) -> list[ApprovedFaqRecord]:
    """Approved FAQ JSON から有効な FAQ record を読み込みます。"""
    payload = load_approved_faq_payload(path)
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        return []

    records: list[ApprovedFaqRecord] = []
    seen_ids: set[str] = set()
    for index, raw_record in enumerate(raw_records, start=1):
        record = _approved_faq_record_from_raw(raw_record, index, seen_ids)
        if record is not None:
            records.append(record)
    return records


def load_approved_faq_excel_rows(path: str | Path) -> list[ApprovedFaqImportRow]:
    """Excel の先頭のシートの QUESTION / ANSWER 列から FAQ 取込行を読み込みます（ほかの列は読まない）。"""
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError("Excelファイルが見つかりません。")
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on deployment packaging
        raise ValueError("Excel の読み込みには pandas / openpyxl / xlrd が必要です。") from exc

    try:
        excel_file = pd.ExcelFile(file_path)
        sheet_name = excel_file.sheet_names[0] if excel_file.sheet_names else 0
        frame = excel_file.parse(sheet_name=sheet_name, dtype=object)
    except Exception as exc:
        raise ValueError(f"Excel の読み込みに失敗しました: {exc}") from exc

    if frame.empty:
        raise ValueError("Excelファイルにデータがありません。")

    columns = {_column_key(column): column for column in frame.columns}
    if any(name not in columns for name in APPROVED_FAQ_EXCEL_COLUMNS):
        raise ValueError("Excelには QUESTION と ANSWER 列が必要です。")

    question_column = columns["QUESTION"]
    answer_column = columns["ANSWER"]
    rows: list[ApprovedFaqImportRow] = []
    for index, row in frame.iterrows():
        question_value = "" if pd.isna(row.get(question_column)) else row.get(question_column)
        answer_value = "" if pd.isna(row.get(answer_column)) else row.get(answer_column)
        question = _clean_single_line_text(question_value, MAX_APPROVED_FAQ_QUESTION_LENGTH)
        approved_answer = _clean_multiline_text(answer_value, MAX_APPROVED_FAQ_ANSWER_LENGTH)
        if not question or not approved_answer:
            continue
        rows.append(
            ApprovedFaqImportRow(
                question=question,
                approved_answer=approved_answer,
                source_file=file_path.name,
                sheet=str(sheet_name),
                row=int(index) + 2,
            )
        )

    if not rows:
        raise ValueError("QUESTION と ANSWER が入力された行がありません。")
    return rows


def apply_approved_faq_import_rows(
    path: str | Path,
    rows: Sequence[ApprovedFaqImportRow],
    *,
    mode: str = APPROVED_FAQ_IMPORT_MODE_INSERT,
    now: datetime | None = None,
) -> ApprovedFaqMutationResult:
    """FAQ 行を insert（同じ質問はスキップ）または置換（同じ質問を消して足す）で保存します。"""
    normalized_mode = _normalize_import_mode(mode)
    with _approved_faq_write_lock(path):
        return _apply_approved_faq_import_rows_locked(path, rows, normalized_mode, now)


def _apply_approved_faq_import_rows_locked(
    path: str | Path,
    rows: Sequence[ApprovedFaqImportRow],
    normalized_mode: str,
    now: datetime | None,
) -> ApprovedFaqMutationResult:
    """apply_approved_faq_import_rows の本体。呼び出し元が _approved_faq_write_lock を保持していること。"""
    payload = load_approved_faq_payload(path)
    records = _raw_records(payload)
    saved_at = _utc_now(now)
    existing_ids = _record_ids(records)
    question_keys = _record_question_keys(records)
    inserted_count = 0
    deleted_count = 0
    skipped_count = 0

    for row in rows:
        question = _clean_single_line_text(row.question, MAX_APPROVED_FAQ_QUESTION_LENGTH)
        approved_answer = _clean_multiline_text(row.approved_answer, MAX_APPROVED_FAQ_ANSWER_LENGTH)
        question_key = _question_key(question)
        if not question_key or not approved_answer:
            skipped_count += 1
            continue

        if normalized_mode == APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT:
            records, removed = _remove_records_by_question_key(records, question_key)
            deleted_count += removed
            if removed:
                existing_ids = _record_ids(records)
                question_keys = _record_question_keys(records)
        elif question_key in question_keys:
            skipped_count += 1
            continue

        record = _new_approved_faq_record(
            ApprovedFaqImportRow(
                question=question,
                approved_answer=approved_answer,
                source_file=row.source_file,
                sheet=row.sheet,
                row=row.row,
            ),
            existing_ids,
            saved_at,
        )
        records.append(record)
        inserted_count += 1
        existing_ids.add(str(record.get("id") or ""))
        question_keys.add(question_key)

    backup_path: Path | None = None
    if inserted_count or deleted_count:
        payload = dict(payload)
        payload["records"] = records
        save_result = save_approved_faq_payload(payload, path=path, now=saved_at)
        backup_path = save_result.backup_path

    return ApprovedFaqMutationResult(
        path=Path(path),
        backup_path=backup_path,
        record_count=len(records),
        inserted_count=inserted_count,
        deleted_count=deleted_count,
        skipped_count=skipped_count,
        saved_at=saved_at,
    )


def add_approved_faq_record(
    path: str | Path,
    *,
    question: str,
    approved_answer: str,
    now: datetime | None = None,
) -> ApprovedFaqMutationResult:
    """手入力された QUESTION/ANSWER を Approved FAQ に追加します（同じ質問はスキップ）。"""
    row = ApprovedFaqImportRow(question=question, approved_answer=approved_answer, source_file="manual")
    return apply_approved_faq_import_rows(path, [row], mode=APPROVED_FAQ_IMPORT_MODE_INSERT, now=now)


def delete_approved_faq_records(
    path: str | Path,
    selectors: str | Sequence[str],
    *,
    now: datetime | None = None,
) -> ApprovedFaqMutationResult:
    """指定 ID と完全一致する Approved FAQ record を削除します。

    selectors は改行・カンマ区切りの ID、または ID の列です。前後の空白と
    重複は除去し、空入力や存在しない ID は無視します。QUESTION は照合しません。
    削除時のみ path の JSON をバックアップして保存し、件数と保存結果を返します。
    読込・保存エラーは呼び出し元へ伝播します。now は保存日時の指定に使います。
    """
    selector_values = parse_approved_faq_delete_text(selectors) if isinstance(selectors, str) else _clean_selectors(selectors)
    with _approved_faq_write_lock(path):
        return _delete_approved_faq_records_locked(path, selector_values, now)


def _delete_approved_faq_records_locked(
    path: str | Path,
    selector_values: Sequence[str],
    now: datetime | None,
) -> ApprovedFaqMutationResult:
    """delete_approved_faq_records の本体。呼び出し元が _approved_faq_write_lock を保持していること。"""
    saved_at = _utc_now(now)
    payload = load_approved_faq_payload(path)
    records = _raw_records(payload)
    if not selector_values:
        return ApprovedFaqMutationResult(
            path=Path(path),
            backup_path=None,
            record_count=len(records),
            inserted_count=0,
            deleted_count=0,
            skipped_count=0,
            saved_at=saved_at,
        )

    selector_ids = set(selector_values)
    remaining_records: list[dict[str, Any]] = []
    deleted_count = 0
    for record in records:
        record_id = str(record.get("id") or "").strip()
        if record_id in selector_ids:
            deleted_count += 1
            continue
        remaining_records.append(record)

    backup_path: Path | None = None
    if deleted_count:
        payload = dict(payload)
        payload["records"] = remaining_records
        save_result = save_approved_faq_payload(payload, path=path, now=saved_at)
        backup_path = save_result.backup_path

    return ApprovedFaqMutationResult(
        path=Path(path),
        backup_path=backup_path,
        record_count=len(remaining_records),
        inserted_count=0,
        deleted_count=deleted_count,
        skipped_count=max(0, len(selector_values) - deleted_count),
        saved_at=saved_at,
    )


@contextlib.contextmanager
def _approved_faq_write_lock(path: str | Path):
    """FAQ JSON の「読込 → 変更 → 保存」を、スレッド間・プロセス間で直列化します。

    ロックなしでは、同時操作が同じ内容を読み込み、後から保存した側が先の更新を消します。
    flock は open ごとに独立するため同一プロセスのスレッド同士も排他されます。再入は
    できないので、ロック保持中に apply/delete を呼び出さないでください。
    """
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.with_name(target_path.name + ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_approved_faq_payload(
    payload: dict[str, Any],
    *,
    path: str | Path = APPROVED_FAQ_FILE,
    now: datetime | None = None,
) -> ApprovedFaqSaveResult:
    """Approved FAQ payload を backup 作成後に原子的に保存します。"""
    saved_at = _utc_now(now)
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    next_payload = dict(payload)
    next_payload["schema_version"] = APPROVED_FAQ_SCHEMA_VERSION
    next_payload["records"] = _raw_records(next_payload)

    backup_path: Path | None = None
    if target_path.exists():
        backup_dir = target_path.parent / APPROVED_FAQ_BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = _unique_backup_path(target_path, backup_dir, saved_at)
        shutil.copy2(target_path, backup_path)

    # timestamp は秒精度なので、ロック外から直接呼ばれた同一秒の保存でも一時ファイルを共有しない。
    temp_path = target_path.with_name(f".{target_path.name}.{_timestamp(saved_at)}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(target_path)
    except Exception:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise
    return ApprovedFaqSaveResult(
        path=target_path,
        backup_path=backup_path,
        record_count=len(next_payload["records"]),
        saved_at=saved_at,
    )


def parse_approved_faq_delete_text(value: str) -> list[str]:
    """削除入力欄の改行・カンマ区切り ID を重複と空白を除いた列へ分割します。"""
    return _clean_selectors(str(value or "").replace("，", "\n").replace(",", "\n").splitlines())


def suggest_approved_faq_questions(
    question: str,
    records: Sequence[ApprovedFaqRecord] | None = None,
    *,
    semantic_index: ApprovedFaqSemanticIndex | None = None,
    semantic_query_embedding: Sequence[float] | None = None,
    semantic_min_score: float = DEFAULT_APPROVED_FAQ_SEMANTIC_MIN_SCORE,
    limit: int = DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT,
    min_score: float = DEFAULT_APPROVED_FAQ_MIN_SCORE,
) -> list[ApprovedFaqSuggestion]:
    """質問に近い登録済みQ&Aを一致度の高い順に返します（min_score 未満は含めない）。

    一致度は FAQ の質問との文字列の類似度と、semantic index を渡したときは embedding の
    類似度（semantic_min_score 以上のとき）の高いほうです。
    """
    normalized_question = str(question or "").strip()
    if not normalized_question:
        return []

    suggestions: list[ApprovedFaqSuggestion] = []
    for record in records if records is not None else load_approved_faq_records():
        if record.status != APPROVED_FAQ_APPROVED_STATUS:
            continue
        text_score = _similarity_score(normalized_question, record.question)
        semantic_score = _semantic_question_score(record, semantic_index, semantic_query_embedding)
        if semantic_score is not None and semantic_score >= semantic_min_score and semantic_score > text_score:
            score = semantic_score
            match_method = "semantic"
        else:
            score = text_score
            match_method = "text"
        if score >= min_score:
            suggestions.append(
                ApprovedFaqSuggestion(
                    record=record,
                    score=score,
                    text_score=text_score,
                    semantic_score=semantic_score,
                    match_method=match_method,
                )
            )

    suggestions.sort(key=lambda item: (-item.score, item.record.id))
    return suggestions[: max(0, int(limit))]


def load_approved_faq_semantic_index(
    cache_path: str | Path,
    *,
    model: str,
    dimensions: int,
    records_signature: str,
) -> ApprovedFaqSemanticIndex | None:
    """署名と model が一致する FAQ semantic index cache を読み込みます。"""
    path = Path(cache_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("schema_version") or 0) != APPROVED_FAQ_SEMANTIC_CACHE_SCHEMA_VERSION:
        return None
    if str(payload.get("model") or "") != str(model or ""):
        return None
    if int(payload.get("dimensions") or 0) != int(dimensions or 0):
        return None
    if str(payload.get("records_signature") or "") != str(records_signature or ""):
        return None

    items: list[ApprovedFaqSemanticItem] = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        record_id = str(raw_item.get("record_id") or "").strip()
        question = str(raw_item.get("question") or "").strip()
        question_hash = str(raw_item.get("question_hash") or "").strip()
        embedding = _embedding_tuple(raw_item.get("embedding"), dimensions)
        if record_id and question and question_hash and embedding is not None:
            items.append(
                ApprovedFaqSemanticItem(
                    record_id=record_id,
                    question=question,
                    question_hash=question_hash,
                    embedding=embedding,
                )
            )
    return ApprovedFaqSemanticIndex(
        model=str(model or ""),
        dimensions=int(dimensions or 0),
        records_signature=str(records_signature or ""),
        items=tuple(items),
    )


def build_approved_faq_semantic_index(
    cache_path: str | Path,
    records: Sequence[ApprovedFaqRecord],
    *,
    model: str,
    dimensions: int,
    embedder: Any,
    settings: Any,
    records_signature: str | None = None,
    now: datetime | None = None,
) -> ApprovedFaqSemanticIndex:
    """FAQ の質問の embedding index を作成します。"""
    normalized_dimensions = max(1, int(dimensions or 1))
    question_items = approved_faq_semantic_questions(records)
    questions = [question for _, question in question_items]
    embeddings = embedder(questions, settings) if questions else []
    items: list[ApprovedFaqSemanticItem] = []
    for (record_id, question), embedding in zip(question_items, embeddings, strict=False):
        embedding_tuple = _embedding_tuple(embedding, normalized_dimensions)
        if embedding_tuple is None:
            continue
        items.append(
            ApprovedFaqSemanticItem(
                record_id=record_id,
                question=question,
                question_hash=_text_hash(question),
                embedding=embedding_tuple,
            )
        )
    index = ApprovedFaqSemanticIndex(
        model=str(model or ""),
        dimensions=normalized_dimensions,
        records_signature=records_signature or approved_faq_records_signature(records),
        items=tuple(items),
    )
    save_approved_faq_semantic_index(cache_path, index, now=now)
    return index


def save_approved_faq_semantic_index(
    cache_path: str | Path,
    index: ApprovedFaqSemanticIndex,
    *,
    now: datetime | None = None,
) -> None:
    """FAQ semantic index を timestamp 付き payload として保存します。"""
    saved_at = _utc_now(now)
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": APPROVED_FAQ_SEMANTIC_CACHE_SCHEMA_VERSION,
        "model": index.model,
        "dimensions": index.dimensions,
        "records_signature": index.records_signature,
        "created_at": saved_at.isoformat(),
        "items": [
            {
                "record_id": item.record_id,
                "question": item.question,
                "question_hash": item.question_hash,
                "embedding": list(item.embedding),
            }
            for item in index.items
        ],
    }
    # save_approved_faq_payload と同じく一時ファイル → replace で原子的に書く。書込中の中断で壊れた cache が残ると、
    # 読込側は None 扱いで再構築するため embedding の再計算（外部 API 呼出）が走る (#828)。
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def approved_faq_records_signature(records: Sequence[ApprovedFaqRecord]) -> str:
    """FAQ record 集合の内容変更検知に使う署名を作ります（semantic index の作り直しの判定）。"""
    payload = [{"id": record.id, "question": record.question, "status": record.status} for record in records]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approved_faq_semantic_questions(records: Sequence[ApprovedFaqRecord]) -> list[tuple[str, str]]:
    """semantic index に入れる (record id, 質問) の列（空と同じ record の重複を除く）。"""
    questions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in records:
        question = str(record.question or "").strip()
        if question and record.id not in seen:
            questions.append((record.id, question))
            seen.add(record.id)
    return questions


def approved_faq_feedback_skip_reason(record: dict[str, Any]) -> str:
    """回答 feedback を FAQ へ昇格しない理由を返します。昇格できる場合は空文字です。"""
    answer_trace = _dict_or_none(record.get("answer_trace")) or {}
    question = _clean_single_line_text(
        record.get("question") or answer_trace.get("question"),
        MAX_APPROVED_FAQ_QUESTION_LENGTH,
    )
    if str(record.get("feedback_type") or "").strip() != "correct":
        has_answer = _clean_multiline_text(record.get("corrected_answer"), MAX_APPROVED_FAQ_ANSWER_LENGTH)
        return "" if question and has_answer else "FAQへ反映する回答がありません。"
    # ponytail: 「回答できない」の判定は insufficient_reason と confidence=low の組で近似する。
    # 一部だけ不足する回答も insufficient_reason を持つため、これだけでは除外しない。
    if str(answer_trace.get("insufficient_reason") or "").strip() and str(answer_trace.get("confidence") or "").strip() == "low":
        return "根拠不足の回答は FAQ 化しません。登録する内容がある場合は修正回答を入力してください。"
    if not question or not _clean_multiline_text(answer_trace.get("answer_text"), MAX_APPROVED_FAQ_ANSWER_LENGTH):
        return "FAQへ反映する回答がありません。"
    return ""


def approved_faq_import_row_from_answer_feedback(record: dict[str, Any]) -> ApprovedFaqImportRow | None:
    """回答 feedback を FAQ 行（質問と回答だけ）へ変換します。

    「正しい」評価は保存した回答、それ以外は修正した回答を使います。
    approved_faq_feedback_skip_reason が理由を返す feedback は None を返します。
    """
    if approved_faq_feedback_skip_reason(record):
        return None
    answer_trace = _dict_or_none(record.get("answer_trace")) or {}
    question = _clean_single_line_text(
        record.get("question") or answer_trace.get("question"),
        MAX_APPROVED_FAQ_QUESTION_LENGTH,
    )
    if str(record.get("feedback_type") or "").strip() == "correct":
        approved_answer = _clean_multiline_text(answer_trace.get("answer_text"), MAX_APPROVED_FAQ_ANSWER_LENGTH)
    else:
        approved_answer = _clean_multiline_text(record.get("corrected_answer"), MAX_APPROVED_FAQ_ANSWER_LENGTH)
    return ApprovedFaqImportRow(
        question=question,
        approved_answer=approved_answer,
        source_file=APPROVED_FAQ_FEEDBACK_SOURCE_FILE,
    )


def promote_answer_feedback_to_approved_faq(
    path: str | Path,
    record: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ApprovedFaqMutationResult | None:
    """回答 feedback を Approved FAQ に登録します（同じ質問は置き換え）。対象でなければ None。"""
    row = approved_faq_import_row_from_answer_feedback(record)
    if row is None:
        return None
    return apply_approved_faq_import_rows(path, [row], mode=APPROVED_FAQ_IMPORT_MODE_DELETE_THEN_INSERT, now=now)


def _approved_faq_record_from_raw(
    raw_record: Any,
    index: int,
    seen_ids: set[str],
) -> ApprovedFaqRecord | None:
    if not isinstance(raw_record, dict):
        return None
    question = str(raw_record.get("question") or "").strip()
    approved_answer = str(raw_record.get("approved_answer") or "").strip()
    if not question or not approved_answer:
        return None

    record_id = str(raw_record.get("id") or f"approved-faq-{index}").strip() or f"approved-faq-{index}"
    if record_id in seen_ids:
        record_id = f"{record_id}-{index}"
    seen_ids.add(record_id)
    return ApprovedFaqRecord(
        id=record_id,
        question=question,
        approved_answer=approved_answer,
        status=str(raw_record.get("status") or "").strip(),
        source=_dict_or_none(raw_record.get("source")),
        timestamps=_dict_or_none(raw_record.get("timestamps")),
    )


def _similarity_score(left: str, right: str) -> float:
    left_key = _same_question_key(left)
    right_key = _same_question_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        # 一律 0.92 にすると候補の下限（既定 0.92）と同値になり、「エラー」のような短い語や、FAQ の質問に条件を
        # 足した質問まで高い一致になる。長さが近いときだけ 0.92 とし、それ以外は割り引く。
        ratio = min(len(left_key), len(right_key)) / max(len(left_key), len(right_key))
        return 0.92 if ratio >= _CONTAINMENT_SAME_QUESTION_MIN_RATIO else 0.5 + 0.4 * ratio

    left_units = _search_units(left)
    right_units = _search_units(right)
    if not left_units or not right_units:
        return 0.0
    overlap = len(left_units & right_units)
    union = len(left_units | right_units)
    jaccard = overlap / union if union else 0.0
    containment = overlap / len(left_units)
    return max(jaccard, containment * 0.75)


# 包含でも「実質同じ質問」とみなす、短い側 / 長い側の長さの比の下限。
_CONTAINMENT_SAME_QUESTION_MIN_RATIO = 0.8


def _same_question_key(value: Any) -> str:
    # 空白・全角半角・大文字小文字と、末尾の句読点だけの違い（「。」と「？」）は同じ質問として扱う。
    return _compact_text(str(value or "")).rstrip("。.?？!！")


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


def _semantic_question_score(
    record: ApprovedFaqRecord,
    semantic_index: ApprovedFaqSemanticIndex | None,
    semantic_query_embedding: Sequence[float] | None,
) -> float | None:
    if semantic_index is None or semantic_query_embedding is None:
        return None
    query_embedding = _embedding_tuple(semantic_query_embedding, semantic_index.dimensions)
    if query_embedding is None:
        return None
    question_hash = _text_hash(record.question)
    scores = [
        _cosine_similarity(query_embedding, item.embedding)
        for item in semantic_index.items
        if item.record_id == record.id and item.question_hash == question_hash
    ]
    return max(scores) if scores else None


def _embedding_tuple(value: Any, dimensions: int) -> tuple[float, ...] | None:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return None
    try:
        embedding = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if int(dimensions or 0) > 0 and len(embedding) != int(dimensions):
        return None
    if not embedding or not all(math.isfinite(item) for item in embedding):
        return None
    return embedding


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    score = sum(left_item * right_item for left_item, right_item in zip(left, right, strict=False)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, score))


def _text_hash(value: Any) -> str:
    return hashlib.sha256(_compact_text(str(value or "")).encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", text).strip()


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_text(value))


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _default_approved_faq_payload() -> dict[str, Any]:
    return {"schema_version": APPROVED_FAQ_SCHEMA_VERSION, "records": []}


def _raw_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        return []
    return [dict(record) for record in raw_records if isinstance(record, dict)]


def _record_ids(records: Sequence[dict[str, Any]]) -> set[str]:
    return {str(record.get("id") or "").strip() for record in records if str(record.get("id") or "").strip()}


def _record_question_keys(records: Sequence[dict[str, Any]]) -> set[str]:
    return {_question_key(record.get("question")) for record in records if _question_key(record.get("question"))}


def _remove_records_by_question_key(
    records: Sequence[dict[str, Any]],
    question_key: str,
) -> tuple[list[dict[str, Any]], int]:
    remaining = [record for record in records if _question_key(record.get("question")) != question_key]
    return remaining, len(records) - len(remaining)


def _new_approved_faq_record(
    row: ApprovedFaqImportRow,
    existing_ids: set[str],
    saved_at: datetime,
) -> dict[str, Any]:
    """保存する FAQ record（質問・回答と、管理の id・状態・取込元・時刻）。"""
    timestamp = saved_at.isoformat()
    source: dict[str, Any] = {}
    if row.source_file:
        source["source_file"] = row.source_file
    if row.sheet:
        source["sheet"] = row.sheet
    if row.row:
        source["row"] = row.row
    return {
        "id": _unique_approved_faq_id(row.question, existing_ids),
        "question": row.question,
        "approved_answer": row.approved_answer,
        "status": APPROVED_FAQ_APPROVED_STATUS,
        "source": source,
        "timestamps": {"created_at": timestamp, "updated_at": timestamp},
    }


def _unique_approved_faq_id(question: str, existing_ids: set[str]) -> str:
    digest = hashlib.sha1(_question_key(question).encode("utf-8")).hexdigest()[:12]
    base_id = f"approved-faq-{digest}"
    if base_id not in existing_ids:
        return base_id
    for index in range(2, 1000):
        candidate = f"{base_id}-{index}"
        if candidate not in existing_ids:
            return candidate
    raise RuntimeError("Approved FAQ ID を採番できませんでした。")


def _normalize_import_mode(mode: str) -> str:
    normalized = str(mode or "").strip().upper()
    if normalized not in APPROVED_FAQ_IMPORT_MODES:
        raise ValueError("取込モードは INSERT または DELETE_THEN_INSERT を選択してください。")
    return normalized


def _clean_selectors(values: Iterable[Any]) -> list[str]:
    selectors: list[str] = []
    seen: set[str] = set()
    for value in values:
        selector = _clean_single_line_text(value, MAX_APPROVED_FAQ_QUESTION_LENGTH)
        key = selector.casefold()
        if not selector or key in seen:
            continue
        selectors.append(selector)
        seen.add(key)
    return selectors


def _clean_single_line_text(value: Any, max_length: int) -> str:
    text = _clean_multiline_text(value, max_length)
    return re.sub(r"\s+", " ", text).strip()


def _clean_multiline_text(value: Any, max_length: int) -> str:
    normalized = unicodedata.normalize("NFC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if len(normalized) > max_length:
        normalized = normalized[:max_length].rstrip()
    return normalized


def _question_key(value: Any) -> str:
    return _compact_text(_clean_single_line_text(value, MAX_APPROVED_FAQ_QUESTION_LENGTH))


def _column_key(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).upper()


def _unique_backup_path(path: Path, backup_dir: Path, saved_at: datetime) -> Path:
    timestamp = _timestamp(saved_at)
    candidate = backup_dir / f"{path.stem}.{timestamp}{path.suffix}.bak"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = backup_dir / f"{path.stem}.{timestamp}.{index}{path.suffix}.bak"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate backup path for {path}")


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")
