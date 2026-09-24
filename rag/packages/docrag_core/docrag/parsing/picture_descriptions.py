"""Docling Picture と画像を含む Table の切り出し画像に Vision 説明を付与する。"""

from __future__ import annotations

import math
import time
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from docrag.parsing.decorative_pictures import classify_picture_record, mark_picture_record_role
from docrag.adapters.oci import describe_picture, _image_retrieval_template, _render_prompt, VISION_SYSTEM_PROMPT
from docrag.parsing.checkpoints import CheckpointError, checkpoint_lock, file_digest, digest_json
from docrag.parsing.rendering import pdf_text_lines_in_bbox
from docrag.parsing.vision_checkpoints import VisionCheckpoints
from docrag.models.layout import LayoutRecord, PageImage
from docrag.config import Settings, get_llm_provider
from docrag.parsing.vision_prompt_rules import (
    VISION_SENTENCE_GROUP_PREFIX,
    MAX_DIAGRAM_NODES,
    MAX_CHART_SERIES_VALUES,
    MAX_TABLE_ROWS,
    MAX_TABLE_COLUMNS,
    MAX_FORM_FIELDS,
    VISION_LIST_FIELD_LIMITS,
)

VISION_CROP_PADDING = 8
VISION_CONTEXT_MIN_PADDING = 96
VISION_CONTEXT_SEQ_WINDOW = 3
VISION_CONTEXT_MAX_SOURCE_REFS = 8
# 所属見出しに対してこの比率未満の高さ（文字サイズ）の後続見出しは、同じ節の小見出しとみなす。
VISION_SUBHEADING_HEIGHT_RATIO = 0.8
# 対象枠はマゼンタ。原文が手順の強調に使う赤枠と色で区別できるようにする (#784)。
VISION_TARGET_OUTLINE = (255, 0, 255)
VISION_TARGET_OUTLINE_WIDTH = 4
SECTION_CATEGORIES = {"Title", "Section-header"}
PROMPT_CONTEXT_EXCLUDED_CATEGORIES = {"Page-footer", "Page-header"}
PROMPT_NEARBY_HEADING_LIMIT = 4
TABLE_CELL_TEXT_HINT_LIMIT = 6
# 生成説明の本文。グループ見出し（`■ …`）と field ごとのラベル行の 2 階層にする (#773)。
# 1 行に複数 field を連結すると、どの語がボタンでどの語が項目かを人も回答生成も区別できない。
VISION_SENTENCE_GROUPS = (
    ("要点", (
        ("回答用本文", "retrieval_text"),
        ("主題", "main_topic"),
        ("視覚種別", "visual_kind"),
    )),
    ("可視情報", (
        ("画面/メニュー", "menu_route"),
        ("画面名", "visible_screen_names"),
        ("ボタン", "visible_buttons"),
        ("項目", "visible_fields"),
        ("値", "visible_values"),
        ("コード・エラー", "codes_and_errors"),
    )),
    ("構造", (
        ("図のタイトル", "diagram_title"),
        ("図のノード", "diagram_nodes"),
        ("図の接続", "diagram_edges"),
        ("図の要約", "diagram_summary"),
        ("グラフのタイトル", "chart_title"),
        ("グラフの種別", "chart_type"),
        ("グラフの軸", "chart_axes"),
        ("グラフの凡例", "chart_legends"),
        ("グラフの系列", "chart_series"),
        ("グラフの要約", "chart_summary"),
        ("表の見出し", "table_headers"),
        ("表の行", "table_rows"),
        ("表の要約", "table_summary"),
        ("フォームの項目", "form_fields"),
        ("フォームの配置", "form_layout"),
    )),
    ("関係", (
        ("操作", "operation_steps"),
        ("条件と結果", "condition_result_pairs"),
        ("注意", "exception_or_cautions"),
    )),
    ("補足", (
        ("周辺コンテキスト", "surrounding_context"),
        ("修正・補足", "correction_notes"),
    )),
    ("検索", (
        ("検索語", "search_keywords"),
        ("言い換え", "query_rewrites"),
        ("想定質問", "answerable_questions"),
    )),
)
# 要素ごとに改行する field。行・接続・項目・条件の対応が ` / ` 連結で失われるため (#768)。
# これ以外は語の列挙か文章なので 1 行にまとめる（未知の field と壊れた値も 1 行側に倒す）。
VISION_MULTILINE_FIELDS = frozenset({
    "table_headers", "table_rows", "diagram_edges", "chart_series", "form_fields",
    "condition_result_pairs", "operation_steps", "exception_or_cautions",
})
VISUAL_STRUCTURE_FIELDS = (
    "diagram_title",
    "diagram_nodes",
    "diagram_edges",
    "diagram_summary",
    "chart_title",
    "chart_type",
    "chart_axes",
    "chart_legends",
    "chart_series",
    "chart_summary",
    "table_headers",
    "table_rows",
    "table_summary",
    "form_fields",
    "form_layout",
)
VISUAL_KIND_ALIASES = {
    "architecture": "architecture_diagram",
    "architecture diagram": "architecture_diagram",
    "architecture-diagram": "architecture_diagram",
    "flow chart": "flowchart",
    "flow-chart": "flowchart",
    "graph": "chart",
    "plot": "chart",
    "scanned table": "table",
    "scanned-table": "table",
}



@dataclass(frozen=True)
class VisionDescriptionStats:
    """Picture/Table Vision 説明の対象数、成功数、失敗数を保持します。"""
    targets: int
    succeeded: int
    failed: int
    discovery_failed: bool = False


@dataclass(frozen=True)
class VisionContextCrop:
    """Vision 説明に添付する周辺 context 画像のパスと metadata を保持します。"""
    path: Path
    bbox: list[float]
    source_record_refs: tuple[dict[str, Any], ...]


def _is_empty_vision_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _join_vision_values(value: Any) -> str:
    """浅い値と入れ子の Vision JSON 値を短い検索用 text に変換します。"""
    if _is_empty_vision_value(value):
        return ""
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            text = _join_vision_values(item)
            if text:
                pieces.append(text)
        return " / ".join(pieces)
    if isinstance(value, dict):
        pieces = []
        for part in value.values():
            text = _join_vision_values(part)
            if text:
                pieces.append(text)
        return " ".join(pieces)
    return re.sub(r"\s+", " ", str(value)).strip()


def _cells_line(value: Any, prefix: str) -> str:
    """表の 1 行を、セル境界が残る ` | ` 区切りの 1 行にします。"""
    cells = value if isinstance(value, list) else [value]
    return f"{prefix}{' | '.join(_join_vision_values(cell) for cell in cells)}"


def _edge_line(value: Any) -> str:
    """図の接続を `接続元 → 接続先（条件）` にし、向きと条件を残します。"""
    if not isinstance(value, dict):
        return _join_vision_values(value)
    source, target = _join_vision_values(value.get("source")), _join_vision_values(value.get("target"))
    if not source or not target:
        return _join_vision_values(value)
    label = _join_vision_values(value.get("label"))
    return f"{source} → {target}" + (f"（{label}）" if label else "")


def _series_line(value: Any) -> str:
    """グラフの系列を `系列名: 値 | 値（傾向）` にします。"""
    if not isinstance(value, dict):
        return _join_vision_values(value)
    name, trend = _join_vision_values(value.get("name")), _join_vision_values(value.get("trend"))
    values = value.get("values")
    joined = " | ".join(_join_vision_values(item) for item in values) if isinstance(values, list) else _join_vision_values(values)
    line = ": ".join(part for part in (name, joined) if part)
    return line + (f"（{trend}）" if trend else "")


def _form_field_line(value: Any) -> str:
    """Form の項目を `項目名 = 表示値 [状態]` にし、項目と値の対応を残します。"""
    if not isinstance(value, dict):
        return _join_vision_values(value)
    name, field_value = _join_vision_values(value.get("name")), _join_vision_values(value.get("value"))
    state = _join_vision_values(value.get("state"))
    line = " = ".join(part for part in (name, field_value) if part)
    return line + (f" [{state}]" if state else "")


def _condition_result_line(value: Any) -> str:
    """条件と結果を `条件 → 結果（根拠: 可視テキスト）` にし、対応が崩れないようにします。"""
    if not isinstance(value, dict):
        return _join_vision_values(value)
    condition, result = _join_vision_values(value.get("condition")), _join_vision_values(value.get("result"))
    if not condition or not result:
        return _join_vision_values(value)
    visible = _join_vision_values(value.get("visible_text"))
    return f"{condition} → {result}" + (f"（根拠: {visible}）" if visible else "")


def _vision_field_lines(field: str, value: Any) -> list[str]:
    """1 つの field を、構造の境界が残る 1 行以上の text にします。

    ` / ` で平坦化すると表の行境界や条件と結果の対応が失われ、人も後段の回答生成も
    どのセルがどの行かを復元できないため、要素ごとに 1 行へ分けます (#768)。
    """
    if _is_empty_vision_value(value):
        return []
    if field == "table_headers":
        return [_cells_line(value, "")]
    if field not in VISION_MULTILINE_FIELDS or not isinstance(value, list):
        return [text] if (text := _join_vision_values(value)) else []
    if field == "table_rows":
        return [_cells_line(row, f"{index}行目: ") for index, row in enumerate(value, start=1)
                if not _is_empty_vision_value(row)]
    renderer = {
        "diagram_edges": _edge_line,
        "chart_series": _series_line,
        "form_fields": _form_field_line,
        "condition_result_pairs": _condition_result_line,
    }.get(field, _join_vision_values)
    return [line for item in value if not _is_empty_vision_value(item) and (line := renderer(item))]


def vision_description_text(payload: dict[str, Any]) -> str:
    """対応する Vision field を text-only retrieval 向けのラベル付き text に整形します。

    `■ グループ` の見出しの下に `ラベル: 値` を並べ、要素が複数ある field は `ラベル:` の
    下へ `- ` の箇条書きにします。人が検証でき、回答生成が行と対応を読み取れる形を優先します。
    空の field と、行が 1 つも出ないグループは出力しません。
    """
    description = payload.get("description") if isinstance(payload.get("description"), dict) else payload
    if not isinstance(description, dict):
        return ""

    lines: list[str] = []
    for group, labeled_fields in VISION_SENTENCE_GROUPS:
        group_lines: list[str] = []
        for label, field in labeled_fields:
            field_lines = _vision_field_lines(field, description.get(field))
            if not field_lines:
                continue
            if len(field_lines) == 1:
                group_lines.append(f"{label}: {field_lines[0]}")
            else:
                group_lines.append(f"{label}:")
                group_lines.extend(f"- {line}" for line in field_lines)
        if group_lines:
            # 人が確認するときにグループの切れ目を追えるよう、2 つ目以降の見出しの前を 1 行空ける。
            if lines:
                lines.append("")
            lines.append(f"{VISION_SENTENCE_GROUP_PREFIX}{group}")
            lines.extend(group_lines)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _normalized_visual_kind(value: Any) -> str:
    """Vision の視覚種別を metadata で安定して扱える値へ正規化します。"""
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not normalized:
        return ""
    return VISUAL_KIND_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _visual_structure(description: dict[str, Any], visual_kind: str) -> dict[str, Any]:
    """図表・Form 固有の Vision 出力だけを再利用可能な構造 metadata にまとめます。"""
    structure: dict[str, Any] = {}
    if visual_kind:
        structure["visual_kind"] = visual_kind
    for key in VISUAL_STRUCTURE_FIELDS:
        value = description.get(key)
        if not _is_empty_vision_value(value):
            structure[key] = value
    return structure


def _bounded_vision_description(payload: dict[str, Any]) -> dict[str, Any]:
    """構造配列を共有上限へ縮め、省略を correction_notes に記録したコピーを返します。"""
    description = dict(payload)
    omissions = []
    for field, limit in VISION_LIST_FIELD_LIMITS.items():
        value = description.get(field)
        if isinstance(value, list):
            if len(value) > limit:
                omissions.append(f"{field}: {len(value)}件のうち先頭{limit}件を保持")
            description[field] = value[:limit]

    rows = description.get("table_rows")
    if isinstance(rows, list):
        for index, row in enumerate(rows, 1):
            if isinstance(row, list) and len(row) > MAX_TABLE_COLUMNS:
                omissions.append(f"table_rows[{index}]: {len(row)}セルのうち先頭{MAX_TABLE_COLUMNS}セルを保持")
        description["table_rows"] = [row[:MAX_TABLE_COLUMNS] if isinstance(row, list) else row for row in rows]

    series = description.get("chart_series")
    if isinstance(series, list):
        bounded_series = []
        for index, item in enumerate(series, 1):
            if not isinstance(item, dict):
                bounded_series.append(item)
                continue
            bounded_item = dict(item)
            values = bounded_item.get("values")
            if isinstance(values, list):
                if len(values) > MAX_CHART_SERIES_VALUES:
                    omissions.append(f"chart_series[{index}].values: {len(values)}件のうち先頭{MAX_CHART_SERIES_VALUES}件を保持")
                bounded_item["values"] = values[:MAX_CHART_SERIES_VALUES]
            bounded_series.append(bounded_item)
        description["chart_series"] = bounded_series
    if omissions:
        note = "保存時の構造上限による省略: " + " / ".join(omissions)
        description["correction_notes"] = "\n".join(filter(None, [str(description.get("correction_notes") or ""), note]))
    return description


def describe_docling_pictures(
    records: list[LayoutRecord], pages: list[PageImage], *, run_dir: Path,
    pdf_name: str, settings: Settings, pdf_path: Path | None = None,
) -> VisionDescriptionStats:
    """Picture/Table説明を逐項保存し、同一入力の応答を再利用する。

    recordsを変更し、run_dirへ応答・結果・進捗を保存する。外部APIを呼ぶ。
    保存不能・同じrunの同時処理はCheckpointError。個別API失敗はrecordへ記録する。
    """
    with checkpoint_lock(run_dir, '.vision.lock'):
        return _describe_docling_pictures(records, pages, run_dir=run_dir,
                                         pdf_name=pdf_name, settings=settings, pdf_path=pdf_path)


def _describe_docling_pictures(
    records: list[LayoutRecord],
    pages: list[PageImage],
    *,
    run_dir: Path,
    pdf_name: str,
    settings: Settings,
    pdf_path: Path | None = None,
) -> VisionDescriptionStats:
    """Picture と未抽出画像を含む Table に Vision 説明を追加します。

    pdf_path 指定時は PDF 画像 object を調べ、Picture で覆われない表内画像も
    対象にします。Table の HTML は保持し、補足を raw.table_vision_text へ保存します。
    個別の Vision 失敗は raw.vision_error に記録し、既存の解析 text を維持します。
    """
    source_hash = (file_digest(pdf_path) if pdf_path is not None and pdf_path.is_file()
                   else digest_json({'name': pdf_name, 'pages': [
                       [page.page, file_digest(Path(page.image_path)) if Path(page.image_path).is_file() else None]
                       for page in pages]}))
    ledger = VisionCheckpoints(run_dir / "docling" / "vision", source_hash)
    vlm_targets: list[LayoutRecord] = []
    for record in records:
        if not (
            record.engine == "docling"
            and record.category == "Picture"
            and record.raw_type == "picture"
            and not record.text.strip()
        ):
            continue
        record.raw["visual_processing_version"] = 2
        visual_role = classify_picture_record(record, records)
        mark_picture_record_role(record, visual_role)
        if visual_role.skip_vlm:
            ledger.register([record], state="skipped")
            continue
        vlm_targets.append(record)

    ledger.register(vlm_targets)
    ledger.update()

    def pending_targets():
        """Picture の成功・失敗が確定してから、残った Table の不足画像を検出します。"""
        yield from sorted(vlm_targets, key=lambda item: (item.page, item.seq_no, item.bbox[1], item.bbox[0]))
        ledger.phase = "discovering_tables"
        ledger.current = None
        ledger.update()
        table_targets = []
        discovery_failed = False
        if pdf_path is not None:
            from docrag.parsing.table_vision import missing_table_image_regions

            try:
                table_regions = missing_table_image_regions(records, pages, pdf_path)
            except Exception as exc:
                discovery_failed = True
                table_regions = {}
                for record in records:
                    if record.engine == "docling" and record.category == "Table":
                        record.raw["table_image_detection_status"] = "failed"
                        record.raw["table_vision_detection_error"] = f"{type(exc).__name__}: {exc}"[:500]
            for record in records:
                if record.id in table_regions and (not record.raw.get("table_vision_text") or record.raw.get("vision_error")):
                    record.raw["table_missing_picture_regions"] = table_regions[record.id]
                    table_targets.append(record)
        ledger.register(table_targets)
        ledger.discovery_complete = not discovery_failed
        ledger.phase = "tables"
        ledger.update()
        yield from table_targets

    from PIL import Image

    page_lookup = {page.page: page for page in pages}
    # 対象ごとに読むと、解析中の prompt 保存で同じ文書の説明が新旧の方針で混ざる。
    prompt_template = _image_retrieval_template()
    crop_dir = run_dir / "docling" / "vision"
    crop_dir.mkdir(parents=True, exist_ok=True)
    page_images: dict[int, Image.Image] = {}
    succeeded = 0
    failed = 0
    target_count = 0
    try:
        for record in pending_targets():
            target_count += 1
            record.raw["visual_processing_version"] = 2
            item_started = time.monotonic()
            key = None
            response = None
            reused = False
            reusable = False
            checkpoint_failed = False
            record.raw["vision_status"] = "running"
            ledger.update(record, "running")
            try:
                provider = get_llm_provider(settings, settings.default_vision_llm)
                page = page_lookup.get(record.page)
                if page is None:
                    raise RuntimeError(f"{record.page} ページ目の画像がありません。")
                page_picture = page_images.get(record.page)
                if page_picture is None:
                    with Image.open(page.image_path) as opened:
                        page_picture = opened.convert("RGB")
                    page_images[record.page] = page_picture
                crop_path = _existing_crop_path(record, run_dir) or _save_crop(page_picture, record, crop_dir)
                context_crop = _save_context_crop(page_picture, record, records, crop_dir, pdf_path=pdf_path)
                relative_crop_path = crop_path.relative_to(run_dir).as_posix()
                relative_context_crop_path = context_crop.path.relative_to(run_dir).as_posix()
                metadata = _vision_metadata(
                    pdf_name=pdf_name,
                    record=record,
                    target_crop_path=relative_crop_path,
                    context_crop_path=relative_context_crop_path,
                    context_crop=context_crop,
                    records=records,
                    pdf_path=pdf_path,
                )
                if record.category == "Table":
                    metadata["existing_table_html"] = record.text
                    metadata["missing_picture_regions"] = record.raw["table_missing_picture_regions"]
                record.raw["crop_path"] = relative_crop_path
                record.raw["vision_crop"] = relative_crop_path
                record.raw["vision_context_crop"] = relative_context_crop_path
                record.raw["vision_context_bbox"] = context_crop.bbox
                record.raw["vision_context_record_refs"] = list(context_crop.source_record_refs)
                record.raw["vision_input_mode"] = "target_crop_with_context"
                record.raw["vision_input_images"] = [
                    {
                        "role": "target_picture_crop",
                        "path": relative_crop_path,
                        "bbox": [float(value) for value in record.bbox],
                    },
                    {
                        "role": "page_context_crop",
                        "path": relative_context_crop_path,
                        "bbox": context_crop.bbox,
                        "target_highlight": "magenta_rectangle",
                    },
                ]
                kind = "table" if record.category == "Table" else "picture"
                prompt = _render_prompt(metadata, target_kind=kind, template=prompt_template)
                key = ledger.key(crop=crop_path, context=context_crop.path, prompt=prompt,
                                 system_prompt=VISION_SYSTEM_PROMPT, provider=provider,
                                 max_tokens=max(1, int(settings.answer_max_tokens)), kind=kind,
                                 api_mode=settings.llm_api_mode)
                response = ledger.load(key)
                reused = response is not None
                if response is None:
                    response = describe_picture(
                        crop_path, metadata, settings, context_image_paths=[context_crop.path],
                        rendered_prompt=prompt, **({"target_kind": "table"} if kind == "table" else {}),
                    )
                    # 応答受信直後に保存し、後続の分類・整表処理が落ちても再送を避ける。
                    ledger.save(key, response, record, state="received")
                reusable = isinstance(response, dict)
                description = _bounded_vision_description(response)
                record.raw["vision_description"] = description
                visual_kind = _normalized_visual_kind(description.get("visual_kind"))
                structure = _visual_structure(description, visual_kind)
                if visual_kind:
                    record.raw["picture_kind"] = visual_kind
                    record.raw["visual_kind"] = visual_kind
                    record.raw["visual_artifact_type"] = visual_kind
                if structure:
                    record.raw["visual_structure"] = structure
                record.raw["vision_provider"] = provider.provider_id
                record.raw["vision_model"] = provider.model
                post_vision_role = classify_picture_record(record, records) if record.category == "Picture" else None
                if post_vision_role is not None and post_vision_role.exclude_from_rag:
                    mark_picture_record_role(record, post_vision_role)
                    record.raw["vision_skipped"] = False
                    record.raw["vision_excluded_after_classification"] = True
                    record.raw["vision_status"] = "succeeded"
                    record.raw.pop("vision_error", None)
                    record.text = ""
                    reusable = True
                    succeeded += 1
                    continue
                if post_vision_role is not None:
                    mark_picture_record_role(record, post_vision_role)
                sentence = vision_description_text(description)
                if not sentence:
                    reusable = False
                    raise RuntimeError("Vision 応答に利用可能な説明フィールドがありません。")
                reusable = True
                if record.category == "Table":
                    from docrag.parsing.table_visual_rows import enrich_table_visual_rows
                    record.raw["table_vision_text"] = sentence
                    enrich_table_visual_rows(record, page, description, run_dir)
                    record.raw["visual_artifact_type"] = "table"
                else:
                    record.text = sentence
                record.raw["vision_status"] = "succeeded"
                record.raw["vision_skipped"] = False
                record.raw.pop("vision_error", None)
                succeeded += 1
            except CheckpointError:
                checkpoint_failed = True
                raise
            except Exception as exc:
                record.raw["vision_status"] = "failed"
                record.raw["vision_skipped"] = False
                record.raw["vision_error"] = f"{type(exc).__name__}: {exc}"[:500]
                failed += 1
            finally:
                if not checkpoint_failed:
                    # KeyboardInterrupt等では直前のreceived応答を残し、失敗結果で上書きしない。
                    state = record.raw.get("vision_status")
                    if state in {"succeeded", "failed"}:
                        record.raw["vision_elapsed_seconds"] = round(time.monotonic() - item_started, 3)
                        failure_key = digest_json({"source": source_hash, "record": record.id, "stage": "prepare"})
                        ledger.save(key or failure_key, response, record, state=state, reusable=reusable)
                        ledger.update(record, state, reused=reused)
        ledger.current = None
        ledger.phase = "completed" if not failed and ledger.discovery_complete else "completed_with_errors"
        ledger.update()
    finally:
        for page_picture in page_images.values():
            page_picture.close()
    return VisionDescriptionStats(target_count, succeeded, failed, not ledger.discovery_complete)


def _save_crop(page_picture, record: LayoutRecord, crop_dir: Path) -> Path:
    x1, y1, x2, y2 = _validated_bbox(record.bbox, label="Picture bbox")
    left = max(0, math.floor(min(x1, x2)) - VISION_CROP_PADDING)
    top = max(0, math.floor(min(y1, y2)) - VISION_CROP_PADDING)
    right = min(page_picture.width, math.ceil(max(x1, x2)) + VISION_CROP_PADDING)
    bottom = min(page_picture.height, math.ceil(max(y1, y2)) + VISION_CROP_PADDING)
    if right <= left or bottom <= top:
        raise RuntimeError(f"Picture bbox が空です: {record.bbox}")
    crop_path = crop_dir / f"{record.id}.png"
    page_picture.crop((left, top, right, bottom)).save(crop_path)
    return crop_path


def _existing_crop_path(record: LayoutRecord, run_dir: Path) -> Path | None:
    """解析直後に保存した crop が現在の run にあれば Vision 入力へ再利用します。"""
    value = str(record.raw.get("crop_path") or "").strip()
    if not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else run_dir / path
    try:
        candidate.resolve().relative_to(run_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _save_context_crop(
    page_picture,
    record: LayoutRecord,
    records: list[LayoutRecord],
    crop_dir: Path,
    pdf_path: Path | None = None,
) -> VisionContextCrop:
    from PIL import ImageDraw

    target_bbox = _validated_bbox(record.bbox, label="Picture bbox")
    context_records = _context_records(record, records, target_bbox, pdf_path=pdf_path)
    # 周辺文脈画像は対象図を含むページ全体にする (#638)。以前は隣接 record の和集合を窓と節見出しで切る
    # 規則だったが、ページ構成によって写る範囲が変わり、画面経路の行や※注記が欠けることがあった。
    # 対象はマゼンタの枠で示し、prompt に渡す周辺 record（nearby_headings 等）の選び方は変えない。
    left, top, right, bottom = 0, 0, page_picture.width, page_picture.height
    crop = page_picture.crop((left, top, right, bottom))
    draw = ImageDraw.Draw(crop)
    draw.rectangle(
        (
            max(0, target_bbox[0] - left),
            max(0, target_bbox[1] - top),
            min(crop.width - 1, target_bbox[2] - left),
            min(crop.height - 1, target_bbox[3] - top),
        ),
        outline=VISION_TARGET_OUTLINE,
        width=min(VISION_TARGET_OUTLINE_WIDTH, max(1, crop.width), max(1, crop.height)),
    )
    context_path = crop_dir / f"{record.id}.context.png"
    crop.save(context_path)
    return VisionContextCrop(
        path=context_path,
        bbox=[float(left), float(top), float(right), float(bottom)],
        source_record_refs=tuple(
            _visible_context_ref(item, (left, top, right, bottom))
            for item in [record, *context_records]
            if _boxes_intersect(tuple(item.bbox), (left, top, right, bottom))
        ),
    )


def _vision_metadata(
    *,
    pdf_name: str,
    record: LayoutRecord,
    target_crop_path: str,
    context_crop_path: str,
    context_crop: VisionContextCrop,
    records: list[LayoutRecord],
    pdf_path: Path | None = None,
) -> dict[str, Any]:
    paired_ocr_text = _paired_picture_ocr_text(record, records)
    nearby_headings = _nearby_headings(record, records)
    previous_record = _neighbor_record(record, records, direction=-1)
    next_record = _neighbor_record(record, records, direction=1)
    if next_record and str(next_record.get("category") or "") in SECTION_CATEGORIES:
        boundary_ids = {other.id for other in _boundary_sections(record, records, pdf_path=pdf_path)}
        next_record["section_role"] = ("section_boundary" if next_record.get("record_id") in boundary_ids
                                       else "subheading_in_owning_section")
    containing_table = _containing_table_metadata(record, records)
    metadata: dict[str, Any] = {
        "file_name": pdf_name,
        "page": record.page,
        "bbox": [float(value) for value in record.bbox],
        "engine": record.engine,
        "record_id": record.id,
        "input_strategy": "target_crop_plus_context_crop",
        "target_highlight": "magenta_rectangle_on_context_image",
        "context_bbox": context_crop.bbox,
        "context_source_record_refs": list(context_crop.source_record_refs),
        "nearby_headings": nearby_headings,
        "owning_section": nearby_headings[-1] if nearby_headings else {},
        "context_constraint": ("所属章は owning_section。next_record の見出しは section_role が section_boundary なら次節の境界であり所属章ではありません。"
                               "subheading_in_owning_section なら同じ節の小見出しで、その本文は主対象の説明として使えます。"),
        "previous_record": previous_record,
        "next_record": next_record,
        "containing_table": containing_table,
        "paired_ocr_text": paired_ocr_text,
        "image_inputs": [
            {
                "index": 1,
                "role": "target_picture_crop",
                "description": "対象領域だけを切り出した高精細確認用画像。細部の読み取りはこの画像を優先する。",
                "path": target_crop_path,
                "bbox": [float(value) for value in record.bbox],
            },
            {
                "index": 2,
                "role": "page_context_crop",
                "description": "同じページの周辺文脈を含む画像。マゼンタ（明るい紫）の矩形がシステムの付けた対象枠で、画像1と同じ内容を囲む。原文に元からある赤枠や強調色は文書の内容であり対象の指定ではない。表・見出し・近接説明との関係確認に使う。",
                "path": context_crop_path,
                "bbox": context_crop.bbox,
            },
        ],
    }
    record.raw["vision_nearby_headings"] = nearby_headings
    record.raw["vision_previous_record"] = previous_record
    record.raw["vision_next_record"] = next_record
    record.raw["vision_containing_table"] = containing_table
    return metadata


def _context_records(
    record: LayoutRecord,
    records: list[LayoutRecord],
    target_bbox: tuple[float, float, float, float],
    pdf_path: Path | None = None,
) -> list[LayoutRecord]:
    search_bbox = _context_window(target_bbox)
    boundary = min((_record_order_key(other) for other in _boundary_sections(record, records, pdf_path=pdf_path)),
                   default=None)
    candidates: list[LayoutRecord] = []
    for other in records:
        if other is record or other.engine != record.engine or other.page != record.page:
            continue
        bbox = _coerce_bbox(other.bbox)
        if bbox is None:
            continue
        if boundary is not None and _record_order_key(other) >= boundary:
            continue
        directly_related = (
            _boxes_intersect(target_bbox, bbox)
            or _box_contains(bbox, target_bbox)
            or _box_contains(target_bbox, bbox)
        )
        nearby_related = (
            abs(int(other.seq_no or 0) - int(record.seq_no or 0)) <= VISION_CONTEXT_SEQ_WINDOW
            and _boxes_intersect(search_bbox, bbox)
        )
        if not directly_related and not nearby_related:
            continue
        if str(other.category or "") in PROMPT_CONTEXT_EXCLUDED_CATEGORIES and not directly_related:
            continue
        candidates.append(other)
    candidates.sort(key=lambda item: _context_record_sort_key(item, record, target_bbox))
    return candidates[:VISION_CONTEXT_MAX_SOURCE_REFS]


def _boundary_sections(
    record: LayoutRecord, records: list[LayoutRecord], pdf_path: Path | None = None,
) -> list[LayoutRecord]:
    """対象より後にある見出しのうち、次節の境界とみなすものを返します。

    マニュアルでは画面画像の直下に「画面説明」「操作説明」のような小見出しが続き、その本文が
    画像の説明そのものになる。Docling は見出しの階層を返さない（`level` は常に 1）ため、
    1 行あたりの高さ（文字サイズ）を所属見出しと比べ、明らかに小さい見出しは同じ節の小見出し
    として境界にしない。所属見出しがないページでは、従来どおり後続の見出しをすべて境界にする。
    pdf_path があれば text layer の行数で折り返しを補正し、なければ bbox の高さで比べる。
    """
    # ponytail: 階層は文字サイズ比だけで判定する。所属見出しと同じ大きさの小見出しは
    # 区別できず境界扱いのまま（採番パターンや太字の比較は実例が出てから）。
    target_key = _record_order_key(record)
    sections = [other for other in records if other is not record and other.engine == record.engine
                and other.page == record.page and str(other.category or "") in SECTION_CATEGORIES
                and _coerce_bbox(other.bbox) is not None]
    following = [other for other in sections if _record_order_key(other) > target_key]
    owning = max((other for other in sections if _record_order_key(other) <= target_key),
                 key=_record_order_key, default=None)
    if owning is None:
        return following
    limit = _heading_line_height(owning, pdf_path) * VISION_SUBHEADING_HEIGHT_RATIO
    return [other for other in following if _heading_line_height(other, pdf_path) >= limit]


def _heading_line_height(record: LayoutRecord, pdf_path: Path | None) -> float:
    """見出しの 1 行あたりの高さ（描画 px）を返します。

    折り返した見出しは bbox が高くなるため、PDF text layer の行数で割って文字サイズに近づける。
    Docling の prov bbox（PDF point）で text layer を読み、pdf_path がない・text layer がない・
    読み取りに失敗した場合は 1 行として扱う（大きめに見積もり、境界側へ倒す）。
    """
    height = _bbox_height(_coerce_bbox(record.bbox) or (0.0, 0.0, 0.0, 0.0))
    provenance = (record.raw or {}).get("prov") if isinstance(record.raw, dict) else None
    bbox = provenance[0].get("bbox") if isinstance(provenance, list) and provenance and isinstance(provenance[0], dict) else None
    if pdf_path is None or not isinstance(bbox, dict) or not all(key in bbox for key in ("l", "t", "r", "b")):
        return height
    lines = _heading_line_count(str(pdf_path), int(record.page or 0),
                                float(bbox["l"]), float(bbox["t"]), float(bbox["r"]), float(bbox["b"]),
                                str(bbox.get("coord_origin") or ""))
    return height / max(1, lines)


@lru_cache(maxsize=4096)
def _heading_line_count(pdf_path: str, page: int, left: float, top: float, right: float, bottom: float, origin: str) -> int:
    """見出し bbox 内の text layer の行数。同じ見出しは Picture ごとに再読しないよう cache する。"""
    try:
        return len(pdf_text_lines_in_bbox(pdf_path, page, {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": origin}))
    except Exception:
        return 0


def _context_window(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """prompt に渡す周辺レコードの探索範囲を返します。余白の単位は描画画像の pixel です。

    周辺文脈画像はページ全体を使うため (#638)、この範囲は crop の大きさには影響しません。
    """
    return _expand_bbox(bbox, max(VISION_CONTEXT_MIN_PADDING, _bbox_width(bbox) * 0.5),
                        max(VISION_CONTEXT_MIN_PADDING, _bbox_height(bbox) * 0.75))


def _visible_context_ref(record: LayoutRecord, crop_bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """文脈画像に実際に写る範囲を明示し、部分表示の全文を画像の根拠と誤認させません。"""
    ref = _context_record_ref(record)
    box = record.bbox
    visible = [max(box[0], crop_bbox[0]), max(box[1], crop_bbox[1]),
               min(box[2], crop_bbox[2]), min(box[3], crop_bbox[3])]
    ref["visible_bbox"] = visible
    ref["partially_visible"] = visible != list(box)
    if ref["partially_visible"]:
        ref["text_preview"] = ""
    return ref


def _context_record_sort_key(
    record: LayoutRecord,
    target: LayoutRecord,
    target_bbox: tuple[float, float, float, float],
) -> tuple[int, int, float, float]:
    bbox = _coerce_bbox(record.bbox) or (0.0, 0.0, 0.0, 0.0)
    spatial_rank = 0 if _boxes_intersect(target_bbox, bbox) or _box_contains(bbox, target_bbox) else 1
    return (
        spatial_rank,
        abs(int(record.seq_no or 0) - int(target.seq_no or 0)),
        bbox[1],
        bbox[0],
    )


def _context_record_ref(record: LayoutRecord) -> dict[str, Any]:
    return {
        "record_id": record.id,
        "page": record.page,
        "seq_no": record.seq_no,
        "category": record.category,
        "raw_type": record.raw_type,
        "bbox": [float(value) for value in record.bbox],
        "text_preview": _text_preview(record.text, 160),
    }


def _nearby_headings(record: LayoutRecord, records: list[LayoutRecord]) -> list[dict[str, Any]]:
    target_key = _record_order_key(record)
    candidates = [
        other
        for other in records
        if other.engine == record.engine
        and other.page == record.page
        and other is not record
        and _record_order_key(other) <= target_key
        and str(other.category or "") in SECTION_CATEGORIES
        and str(other.text or "").strip()
    ]
    candidates.sort(
        key=lambda other: (
            0 if _record_order_key(other) <= target_key else 1,
            abs(int(other.seq_no or 0) - int(record.seq_no or 0)),
            abs(
                (_bbox_center_y(_coerce_bbox(other.bbox)) or 0.0)
                - (_bbox_center_y(_coerce_bbox(record.bbox)) or 0.0)
            ),
        )
    )
    selected = candidates[:PROMPT_NEARBY_HEADING_LIMIT]
    selected.sort(key=_record_order_key)
    return [_prompt_record_ref(item, text_limit=240) for item in selected]


def _neighbor_record(
    record: LayoutRecord,
    records: list[LayoutRecord],
    *,
    direction: int,
) -> dict[str, Any]:
    ordered = sorted(
        (
            other
            for other in records
            if other.engine == record.engine
            and other.page == record.page
            and _is_prompt_context_record(other)
        ),
        key=_record_order_key,
    )
    target_key = _record_order_key(record)
    if direction < 0:
        candidates = [other for other in ordered if _record_order_key(other) < target_key]
        return _prompt_record_ref(candidates[-1], text_limit=320) if candidates else {}
    candidates = [other for other in ordered if _record_order_key(other) > target_key]
    return _prompt_record_ref(candidates[0], text_limit=320) if candidates else {}


def _containing_table_metadata(record: LayoutRecord, records: list[LayoutRecord]) -> dict[str, Any]:
    target_bbox = _coerce_bbox(record.bbox)
    if target_bbox is None:
        return {}
    tables = []
    for other in records:
        if other is record or other.engine != record.engine or other.page != record.page:
            continue
        if str(other.category or "") != "Table":
            continue
        table_bbox = _coerce_bbox(other.bbox)
        if table_bbox is None:
            continue
        contains = _box_contains(table_bbox, target_bbox)
        intersects = _boxes_intersect(table_bbox, target_bbox)
        if contains or intersects:
            tables.append((0 if contains else 1, _bbox_area(table_bbox), other, table_bbox))
    if not tables:
        return {}
    _, _, table, table_bbox = sorted(
        tables,
        key=lambda item: (item[0], item[1], _record_order_key(item[2])),
    )[0]
    payload = _prompt_record_ref(table, text_limit=600)
    payload["relationship"] = (
        "contains_target_picture"
        if _box_contains(table_bbox, target_bbox)
        else "intersects_target_picture"
    )
    payload["cell_hint"] = _table_cell_hint(table, table_bbox, record, target_bbox, records)
    return payload


def _table_cell_hint(
    table: LayoutRecord,
    table_bbox: tuple[float, float, float, float],
    record: LayoutRecord,
    target_bbox: tuple[float, float, float, float],
    records: list[LayoutRecord],
) -> dict[str, Any]:
    target_center_x = (target_bbox[0] + target_bbox[2]) / 2.0
    target_center_y = (target_bbox[1] + target_bbox[3]) / 2.0
    x_ratio = _ratio_within(target_center_x, table_bbox[0], table_bbox[2])
    y_ratio = _ratio_within(target_center_y, table_bbox[1], table_bbox[3])
    y_padding = max(8.0, _bbox_height(target_bbox) * 0.35)
    row_band = (
        table_bbox[0],
        target_bbox[1] - y_padding,
        table_bbox[2],
        target_bbox[3] + y_padding,
    )
    nearby_text_records = []
    for other in records:
        if other is record or other is table or other.engine != record.engine or other.page != record.page:
            continue
        if not _is_prompt_context_record(other) or str(other.category or "") == "Table":
            continue
        other_bbox = _coerce_bbox(other.bbox)
        if other_bbox is None or not _boxes_intersect(table_bbox, other_bbox):
            continue
        if not (_boxes_intersect(row_band, other_bbox) or _boxes_intersect(target_bbox, other_bbox)):
            continue
        nearby_text_records.append(other)
    nearby_text_records.sort(
        key=lambda other: (
            _bbox_center_distance(target_bbox, _coerce_bbox(other.bbox)),
            _record_order_key(other),
        )
    )
    return {
        "strategy": "table_bbox_position_and_nearby_text",
        "confidence": "medium" if nearby_text_records else "low",
        "target_position": {
            "x_ratio": round(x_ratio, 3),
            "y_ratio": round(y_ratio, 3),
            "horizontal_region": _region_label(x_ratio, "left", "middle", "right"),
            "vertical_region": _region_label(y_ratio, "upper", "middle", "lower"),
        },
        "nearby_cell_text_records": [
            _prompt_record_ref(item, text_limit=240) for item in nearby_text_records[:TABLE_CELL_TEXT_HINT_LIMIT]
        ],
    }


def _is_prompt_context_record(record: LayoutRecord) -> bool:
    if str(record.category or "") in PROMPT_CONTEXT_EXCLUDED_CATEGORIES:
        return False
    if str(record.raw_type or "") == "picture_ocr_text":
        return False
    if str(record.category or "") == "Picture" and not str(record.text or "").strip():
        return False
    return _coerce_bbox(record.bbox) is not None


def _prompt_record_ref(record: LayoutRecord, *, text_limit: int) -> dict[str, Any]:
    return {
        "record_id": record.id,
        "page": record.page,
        "seq_no": record.seq_no,
        "category": record.category,
        "raw_type": record.raw_type,
        "bbox": [float(value) for value in record.bbox],
        "text_preview": _text_preview(record.text, text_limit),
    }


def _paired_picture_ocr_text(record: LayoutRecord, records: list[LayoutRecord]) -> str:
    target_bbox = _coerce_bbox(record.bbox)
    if target_bbox is None:
        return ""
    for other in records:
        if other is record or other.engine != record.engine or other.page != record.page:
            continue
        if str(other.raw_type or "") != "picture_ocr_text":
            continue
        if _same_bbox(target_bbox, _coerce_bbox(other.bbox)):
            return _text_preview(other.text, 2000)
    return ""


def _validated_bbox(values: Any, *, label: str = "bbox") -> tuple[float, float, float, float]:
    bbox = _coerce_bbox(values)
    if bbox is None:
        raise RuntimeError(f"{label} が不正です: {values}")
    return bbox


def _coerce_bbox(values: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(values, list) or len(values) < 4:
        return None
    try:
        x1, y1, x2, y2 = (float(value) for value in values[:4])
    except (TypeError, ValueError):
        return None
    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _same_bbox(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
    *,
    tolerance: float = 1.0,
) -> bool:
    if left is None or right is None:
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _union_bboxes(bboxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(bbox[0] for bbox in bboxes),
        min(bbox[1] for bbox in bboxes),
        max(bbox[2] for bbox in bboxes),
        max(bbox[3] for bbox in bboxes),
    )


def _pad_bbox(
    bbox: tuple[float, float, float, float],
    page_width: int,
    page_height: int,
) -> tuple[float, float, float, float]:
    x_padding = max(VISION_CONTEXT_MIN_PADDING, _bbox_width(bbox) * 0.18)
    y_padding = max(VISION_CONTEXT_MIN_PADDING, _bbox_height(bbox) * 0.32)
    expanded = _expand_bbox(bbox, x_padding, y_padding)
    return (
        max(0.0, expanded[0]),
        max(0.0, expanded[1]),
        min(float(page_width), expanded[2]),
        min(float(page_height), expanded[3]),
    )


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    x_padding: float,
    y_padding: float,
) -> tuple[float, float, float, float]:
    return (
        bbox[0] - x_padding,
        bbox[1] - y_padding,
        bbox[2] + x_padding,
        bbox[3] + y_padding,
    )


def _boxes_intersect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _box_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return _bbox_width(bbox) * _bbox_height(bbox)


def _bbox_center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float] | None,
) -> float:
    if right is None:
        return float("inf")
    left_x = (left[0] + left[2]) / 2.0
    left_y = (left[1] + left[3]) / 2.0
    right_x = (right[0] + right[2]) / 2.0
    right_y = (right[1] + right[3]) / 2.0
    return math.hypot(left_x - right_x, left_y - right_y)


def _bbox_center_y(bbox: tuple[float, float, float, float] | None) -> float | None:
    if bbox is None:
        return None
    return (bbox[1] + bbox[3]) / 2.0


def _bbox_width(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0])


def _bbox_height(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[3] - bbox[1])


def _ratio_within(value: float, start: float, end: float) -> float:
    width = max(0.0001, end - start)
    return min(1.0, max(0.0, (value - start) / width))


def _region_label(ratio: float, first: str, middle: str, last: str) -> str:
    if ratio < 0.33:
        return first
    if ratio > 0.67:
        return last
    return middle


def _record_order_key(record: LayoutRecord) -> tuple[int, int, float, float]:
    bbox = _coerce_bbox(record.bbox) or (0.0, 0.0, 0.0, 0.0)
    return int(record.page or 0), int(record.seq_no or 0), bbox[1], bbox[0]


def _text_preview(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
