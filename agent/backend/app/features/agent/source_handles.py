"""モデル向けの根拠ハンドルと保存した実 ID を、実行内で双方向に対応させる。"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any, cast

from app.features.agent.answer_data import DataReferenceError
from app.features.agent.answer_references import evidence_refs

REGISTRY_KEY = "_source_reference_handles"
SOURCE_TOOLS = frozenset(
    {
        "rag_search",
        "rag_retrieve_evidence",
        "rag_outline",
        "rag_read_document",
        "rag_read_section",
        "rag_read_source",
        "rag_readable_documents",
    }
)
_FIELDS = {
    "document_id": "doc",
    "chunk_id": "chunk",
    "evidence_id": "chunk",
    "section_id": "section",
    "root_section_id": "section",
    "parent_section_id": "section",
}
# 本文・自由入力を識別子として走査しない。
_CONTENT = frozenset({"text", "excerpt", "answer", "parent_text", "profile_knowledge", "planning"})
_SCHEMES = ("doc://", "chunk://", "section://")
_HANDLE = re.compile(r"(?:doc|chunk|section)://[1-9][0-9]*\Z")
_CITATION = re.compile(r"\[\[cite:([1-9][0-9]*)\]\]")

SOURCE_INSTRUCTIONS = (
    "## Step 1: 取得した根拠を読む\n"
    "取得結果の文書・片段・章節の短いIDは、対応する読み取りツールの引数に使用してください。\n"
    "## Step 2: 回答に出典を付ける\n"
    "回答の根拠を示す位置には、取得結果のcitation参照を使用してください。"
    "原文の本文と出典の位置を保って回答してください。"
)


class SourceHandleError(DataReferenceError):
    pass


def namespace(tool_name: str) -> str:
    return tool_name.rpartition("__")[0]


def _entries(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = metadata.get(REGISTRY_KEY, [])
    if not isinstance(value, list):
        raise SourceHandleError("保存した根拠参照の対応表が不正です。")
    return value


def _find(
    metadata: Mapping[str, Any], connection: str, document: str, kind: str, value: str
) -> dict[str, Any] | None:
    return next(
        (
            entry
            for entry in _entries(metadata)
            if (entry["connection"], entry["document"], entry["kind"], entry["value"])
            == (connection, document, kind, value)
        ),
        None,
    )


def register_output(
    metadata: dict[str, Any], connection: str, output: Mapping[str, Any], *, tool: str
) -> None:
    """保存の lock 内だけで呼ぶ。既存の番号を変更・削除しない。"""

    def register(kind: str, value: str, document: str) -> None:
        if not value:
            return
        if value.startswith(_SCHEMES):
            raise SourceHandleError("取得結果の実IDに短い根拠参照を登録できません。")
        if _find(metadata, connection, document, kind, value) is not None:
            return
        entries = metadata.setdefault(REGISTRY_KEY, [])
        number = max((entry["number"] for entry in entries if entry["kind"] == kind), default=0) + 1
        entries.append(
            {
                "connection": connection,
                "document": document,
                "kind": kind,
                "value": value,
                "number": number,
            }
        )

    def visit(value: Any, document: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, document)
        elif isinstance(value, dict):
            document = value.get("document_id") or document
            if not isinstance(document, str):
                return
            if document:
                register("doc", document, document)
            for key, item in value.items():
                if key in _CONTENT:
                    continue
                kind = _FIELDS.get(key)
                if kind and isinstance(item, str) and document:
                    register(kind, item, document)
                elif key == "readable_document_ids" and isinstance(item, list):
                    for identifier in item:
                        if isinstance(identifier, str):
                            register("doc", identifier, identifier)
                elif isinstance(item, dict | list):
                    visit(item, document)

    visit(output)
    for (document, chunk), _text, _labels, _names in evidence_refs(tool, dict(output)):
        entry = _find(metadata, connection, document, "chunk", chunk)
        if entry is not None:
            entry["citable"] = True


def encode_output(
    metadata: Mapping[str, Any], connection: str, output: Mapping[str, Any]
) -> dict[str, Any]:
    """モデルに渡すコピーだけを書き換え、原文と永続化した結果は変えない。"""

    def encode(kind: str, value: str, document: str) -> str:
        entry = _find(metadata, connection, document, kind, value)
        return f"{kind}://{entry['number']}" if entry is not None else value

    def visit(value: Any, document: str = "") -> Any:
        if isinstance(value, list):
            return [visit(item, document) for item in value]
        if not isinstance(value, dict):
            return value
        document = value.get("document_id") or document
        if not isinstance(document, str):
            return dict(value)
        result = {}
        for key, item in value.items():
            kind = _FIELDS.get(key)
            if key in _CONTENT:
                result[key] = item
            elif kind and isinstance(item, str) and document:
                result[key] = encode(kind, item, document)
            elif key == "readable_document_ids" and isinstance(item, list):
                result[key] = [
                    encode("doc", identifier, identifier)
                    if isinstance(identifier, str)
                    else identifier
                    for identifier in item
                ]
            else:
                result[key] = visit(item, document)
        chunk = value.get("chunk_id") or value.get("evidence_id")
        entry = (
            _find(metadata, connection, document, "chunk", chunk)
            if isinstance(chunk, str)
            else None
        )
        if entry is not None and entry.get("citable"):
            result["citation"] = f"[[cite:{entry['number']}]]"
        return result

    return cast(dict[str, Any], visit(dict(output)))


def decode_arguments(
    metadata: Mapping[str, Any], connection: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """短参照を実IDへ戻した後に、通常の引数・権限の検証を通す。"""
    result = dict(arguments)
    if isinstance(result.get("document_ids"), list):
        result["document_ids"] = [
            decode_arguments(metadata, connection, {"document_id": item})["document_id"]
            for item in result["document_ids"]
        ]
    for key in ("document_id", "chunk_id", "section_id"):
        value = result.get(key)
        if not isinstance(value, str) or not value.startswith(_SCHEMES):
            continue
        if not _HANDLE.fullmatch(value):
            raise SourceHandleError("短い根拠IDの形式が不正です。取得結果のIDを使用してください。")
        kind = _FIELDS[key]
        entry = next(
            (
                entry
                for entry in _entries(metadata)
                if entry["connection"] == connection
                and entry["kind"] == kind
                and f"{kind}://{entry['number']}" == value
            ),
            None,
        )
        if entry is None:
            raise SourceHandleError(
                "取得済みの根拠に無い短いIDです。返されたIDを使用してください。"
            )
        if key != "document_id" and result.get("document_id") != entry["document"]:
            raise SourceHandleError(
                "文書と片段・章節の対応が違います。取得結果の組合せを使用してください。"
            )
        result[key] = entry["value"]
    return result


def render_citations(
    metadata: Mapping[str, Any],
    answer: str,
    *,
    available_sources: Collection[tuple[str, str]] | None = None,
) -> str:
    """引用の短い番号だけを取得済みの片段IDへ還元する。本文のIDを一括置換しない。"""
    if "[[cite:" in _CITATION.sub("", answer):
        raise SourceHandleError("根拠参照の形式が不正です。取得結果のcitationを使用してください。")

    def replace(match: re.Match[str]) -> str:
        entry = next(
            (
                entry
                for entry in _entries(metadata)
                if entry["kind"] == "chunk" and str(entry["number"]) == match[1]
            ),
            None,
        )
        if entry is None or not entry.get("citable"):
            raise SourceHandleError(
                "取得していない根拠の参照です。取得結果のcitationを使用してください。"
            )
        if (
            available_sources is not None
            and (entry["document"], entry["value"]) not in available_sources
        ):
            raise SourceHandleError(
                "保存した実行結果に対応する根拠がありません。取得済みの参照を使用してください。"
            )
        # 既存の表示・根拠照合で同じ片段IDを別出典と区別できなければ推測しない。
        same = [
            row
            for row in _entries(metadata)
            if row["kind"] == "chunk" and row["value"] == entry["value"] and row.get("citable")
        ]
        if len(same) != 1:
            raise SourceHandleError(
                "片段IDが複数の出典に対応するため、引用を一意に解決できません。"
            )
        return "【根拠: " + str(entry["value"]) + "】"

    return _CITATION.sub(replace, answer)
