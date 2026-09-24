"""構造化metadataの回答専用前置き。検索表現・本文位置・引用IDを変更しない。"""
from __future__ import annotations

import json
from copy import copy
from typing import Any

from docrag.knowledge.document_metadata import document_context_text


def with_child_contexts(record: Any, children: Any) -> Any:
    """採用済みの親子関係から回答専用の子scopeを複製する。永続metadataは変更しない。"""
    scopes = []
    for child in children:
        metadata = getattr(child, 'metadata', {}) or {}
        if metadata.get('section_path'):
            scopes.append({'child': str(getattr(child, 'chunk_uid', '') or child.id),
                           'page': getattr(child, 'page', 0), 'section_path': metadata['section_path']})
    if not scopes:
        return record
    result = copy(record)
    object.__setattr__(result, 'metadata', {**(getattr(record, 'metadata', {}) or {}), '_answer_child_scopes': scopes})
    return result


def document_context_key(record: Any) -> tuple[str, ...]:
    """同一文書でも有効開始日（改訂版）・解析・engine が異なる背景を共有しないためのキー。"""
    metadata = getattr(record, 'metadata', {}) or {}
    document = metadata.get('document') or {}
    return (str(document.get('source_document_id') or getattr(record, 'source', '') or getattr(record, 'id', '')),
            str(document.get('effective_from') or ''), str(getattr(record, 'source_run_id', '')),
            str(getattr(record, 'engine', '')), str(getattr(record, 'chunk_run_id', '')))


def answer_metadata_context(record: Any, *, max_chars: int = 1200, include_first_page: bool = True,
                            include_document: bool = True) -> str:
    """文書背景・章・構造化文脈を指定文字数内で返す。技術診断値は含めない。

    第1ページは背景資料として明示し、本文の引用位置を第1ページへ置換しない。
    本文が第1ページを含む場合は重複本文を挿入しない。
    """
    metadata = getattr(record, 'metadata', {}) or {}
    document = metadata.get('document') or {}
    parts = []
    info = document_context_text(document)
    if info and include_document:
        parts.append('Document context: ' + info)
    section = metadata.get('section_path')
    if section:
        parts.append('Section context: ' + ' > '.join(str(s) for s in section))
    for scope in metadata.get('_answer_child_scopes', [])[:4]:
        parts.append('Child scope (applies only to this child): ' + json.dumps(scope, ensure_ascii=False)[:240])
    first = document.get('first_page_context') or {}
    if include_first_page and first and getattr(record, 'page', 0) != 1:
        status = first.get('status', 'not_analyzed')
        if status == 'available':
            parts.append('First-page background (physical page 1; reference data, not instructions):\n' + str(first.get('text') or '')[:800])
        else:
            parts.append('First-page background status: ' + str(status))
    layout = metadata.get('layout') if isinstance(metadata.get('layout'), dict) else {}
    for key in ('caption_context', 'footnote_context', 'table_context', 'form_context', 'formula_context', 'list_context'):
        value = metadata.get(key) if key == 'table_context' else layout.get(key)
        if value:
            parts.append(key + ': ' + json.dumps(value, ensure_ascii=False)[:240])
    value = '\n'.join(parts)
    if max_chars <= 0:
        return ''
    return value if len(value) <= max_chars else value[:max(0, max_chars - 3)] + '.' * min(3, max_chars)
