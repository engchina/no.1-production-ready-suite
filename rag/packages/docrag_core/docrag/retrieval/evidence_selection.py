"""質問に関連する原文を、出典ごとの予算と操作文脈を保って選ぶ。"""
from __future__ import annotations

import re
import hashlib
import json
import unicodedata
from typing import Any, Sequence

from docrag.retrieval.task_contract import task_contract, current_request_text, goal_retrieval_queries
from docrag.retrieval.definition_evidence import definition_labels, definition_ranges, definition_windows


def _record_page_end(record: Any) -> int:
    """record の最終頁。持たない record では先頭頁を返す。"""
    try:
        value = getattr(record, "page_end", None)
        page = int(getattr(record, "page", 0) or 0)
        return max(page, int(value)) if value is not None else page
    except (TypeError, ValueError):
        return int(getattr(record, "page", 0) or 0)


def _terms(value: str) -> set[str]:
    text = unicodedata.normalize("NFKC", value).lower()
    terms = set(re.findall(r"[a-z][a-z0-9_-]{1,}", text))
    for word in re.findall(r"[一-龯々ァ-ヶー]{2,}", text):
        terms.add(word)
        terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return terms - {"確認", "情報", "場合", "対象", "方法", "表示"}


def evidence_relevance(question: str, text: str) -> float:
    """用語一致と操作ラベルから相対関連度を返す。事実の正しさは判定しない。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    matches = [term for term in _terms(current_request_text(question)) if term in normalized]
    score = sum(min(len(term), 8) for term in matches)
    # 試行した経路の語数が多くても、希望する結果を扱う原文を残す。
    goal_terms = _terms(' '.join(goal_retrieval_queries(question)))
    score += 2 * sum(min(len(term), 8) for term in goal_terms if term in normalized)
    # 列名の出現数ではなく、質問の各項目に対する定義本文を優先する。
    score += 100 * len({item['label'] for item in definition_ranges(text, definition_labels(question))})
    # 一致する操作説明を画面のサンプル値より優先する。無関係な操作には加点しない。
    if matches:
        score *= 1 + 0.18 * min(3, len(re.findall(r"操作:|条件と結果:|直接入力|パラメータ|手順", text)))
    if task_contract(question)["goal"] == "procedure" and matches:
        if re.search(r"(?m)^\s*[●■◆]", text):
            score += 80
        if re.match(r"\s*(?:画面/メニュー|検索語|主題|表構造):", text):
            score *= 0.2
    if re.search(r"いつ|初期|デフォルト|切替|default", question, re.I):
        score += 10 * len(set(re.findall(r"初期表示|パラメータ|設定|切替|既定", text)))
    if task_contract(question)["goal"] == "recipient_list":
        # 表名一致だけでは集計値の説明が名簿の操作を押し出す。原質問の出力対象で
        # 補正し、検索用ラベルの反復より一覧の選択・抽出・実行を残す。
        list_terms = set(re.findall(r"一覧|名簿|個人|対象者|抽出|ファイル出力|csv", normalized))
        score += 12 * len(list_terms)
        if list_terms and re.search(r"操作:|(?:選択|入力|出力).*(?:実行|ボタン)", text):
            score += 40
        if re.match(r"\s*(?:画面/メニュー|検索語|主題):", text):
            score *= 0.2
    return score


def evidence_excerpt(question: str, text: str, budget: int, *, required_ranges: Sequence[tuple[int, int]] = (),
                     required_only: bool = False) -> str:
    """関連窓と隣接文を原文順に返す。切断箇所は明示し、値や原文は改変しない。

    budgetは文字数。長いHTMLや改行なしのOCRも窓で探索するため、末尾の規則を
    先頭切詰めで失わない。短いテキストは全文を返す。
    """
    if budget <= 0:
        return ""
    if len(text) <= budget and not required_only:
        return text
    window = min(budget, 700, max(160, budget // 2))
    # 段落・操作行が収まる場合はその境界を守り、長いHTMLだけ窓へ分割する。
    spans = []
    # 項目見出しと続く操作・注意書きは一つの引用窓に保ち、操作だけが落ちるのを防ぐ。
    for match in re.finditer(r"[●■◆][^\n]*(?:\n(?!\n|[●■◆])[^\n]+)*\n?|[^\n]+(?:\n|$)", text):
        left, right = match.span()
        if match.group().startswith(('●', '■', '◆')):
            # 項目直前の短い上位見出しも原文順で保持し、タブと項目の階層を失わない。
            headings = list(re.finditer(r"(?m)^[^。:：\n●■◆・※]{2,24}\n(?=[※●■◆])", text[max(0,left-400):left]))
            if headings:
                left = max(0, left-400) + headings[-1].start()
        if right - left <= budget:
            spans.append((left, right))
        else:
            spans.extend((start, min(right, start + window)) for start in range(left, right, max(1, window // 2)))
    if not spans:
        return text[:budget]
    # 表の項目名だけに点数が集中しても、後続の番号付き完了操作を落とさない。
    # 長い表全体は必須にせず、実際の操作を含む原文行を保護する。
    steps = []
    if task_contract(question)['goal'] in {'procedure', 'recipient_list'}:
        for match in re.finditer(r'(?m)^[^\n]+', text):
            numbered = re.match(r'\s*(?:[0-9０-９]+\s*[.．、]|[①-⑳㉑-㉟])', match.group())
            conditional = re.search(r'新規追加|の場合', match.group())
            if (numbered or conditional) and re.search(r'入力|選択|押|実行|登録|完了|クリック|ボタン', match.group()):
                steps.append(match.span())
    # 短い節名の直後に箇条書きの条件が続く場合、節名と条件を同じ窓にする。
    # 片方の処理区分名だけが落ちると、同ページの別モードへ条件を誤配属する。
    if task_contract(question)['goal'] == 'procedure':
        lines = list(re.finditer(r'(?m)^[^\n]*(?:\n|$)', text))
        for index, line in enumerate(lines[:-1]):
            heading = line.group().strip()
            if (not re.fullmatch(r'[^。:：●■◆・※]{2,32}', heading)
                    or not lines[index + 1].group().lstrip().startswith('・')):
                continue
            end = lines[index + 1].end()
            for following in lines[index + 2:]:
                body = following.group().strip()
                if (not body or re.fullmatch(r'[^。:：●■◆・※]{2,32}', body)
                        or re.match(r'(?:[0-9０-９]+[.．、]|[●■◆]|[^:：]{1,20}[:：])', body)):
                    break
                end = following.end()
            steps.append((line.start(), end))
    protected = sorted(set([*required_ranges, *definition_windows(text, definition_labels(question)), *steps]))
    # 定義を一行ごとの点数で分断しない。収まらない完全定義の断片も候補から除く。
    spans = [(a, b) for a, b in spans if not any(a < y and b > x for x, y in protected)]
    if required_only:
        spans = []
    ranked = [*protected, *sorted(spans, key=lambda span: (-evidence_relevance(question, text[span[0]:span[1]]), span[0]))]
    selected: list[tuple[int, int]] = []
    for start, end in ranked:
        merged = sorted([*selected, (start, end)])
        union: list[tuple[int, int]] = []
        for left, right in merged:
            if union and left <= union[-1][1]:
                union[-1] = (union[-1][0], max(right, union[-1][1]))
            else:
                union.append((left, right))
        if sum(b - a for a, b in union) + 8 * (len(union) - 1) <= budget:
            selected = union
    return "\n[…]\n".join(text[a:b] for a, b in selected)[:budget]


def record_fingerprint(record: Any) -> str:
    """同じ文書・ページ・節・全文の重複だけを識別する。版の異なる本文は統合しない。"""
    metadata = getattr(record, "metadata", {}) or {}
    # 出典不明の本文は、同文でも別資料の可能性があるためIDを隔離キーにする。
    source = getattr(record, "source", "") or getattr(record, "chunk_uid", "") or record.id
    from docrag.retrieval.metadata_context import document_context_key
    value = [source, document_context_key(record), getattr(record, "page", 0),
             metadata.get("section_path"), str(getattr(record, "text", ""))]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# 回答生成に渡す原文の総予算（文字）。親 6,000 字の候補を複数の機能・文書から入れられる大きさ (#665)。
EVIDENCE_BUDGET_CHARS = 48000
# 後続候補のために取り置く短い原文範囲の上限（親ごと）。
NATIVE_RESERVE_CAP = 1200


def evidence_spans(question: str, records: Sequence[Any], *, budget: int = EVIDENCE_BUDGET_CHARS,
                   anchors: Sequence[Any] = (), definitions_only: bool = False,
                   scope_records: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """原文の文字予算内で安定IDを付け、照合済みanchorsの本文と頁を優先する。

    anchorsは同じ検索で取得したchildだけを渡す。文書・版・親子関係・位置を
    再検証し、照合できないchildは頁確定や原文保護に使わない。
    definitions_onlyは予算競合時の再切出し用で、定義以外の窓を追加しない。
    scope_recordsは取得済みchildの所属照合専用で、全文の予算優先には使わない。
    """
    unique: dict[str, Any] = {}
    aliases: dict[str, list[str]] = {}
    for record in records:
        key = record_fingerprint(record)
        unique.setdefault(key, record)
        aliases.setdefault(key, []).append(getattr(record, "chunk_uid", "") or record.id)
    labels = definition_labels(question)
    from docrag.retrieval.metadata_context import answer_metadata_context, document_context_key
    remaining = max(0, budget)
    context_remaining = max(0, budget // 10)
    seen_documents = set()
    result = []
    ordered = _same_unit_first(list(unique.items()), question)
    if labels:
        ordered.sort(key=lambda item: -len(definition_ranges(item[1].text, labels)))
    # 後続の候補の短い原文範囲（各親 NATIVE_RESERVE_CAP まで）の分は先に取り置き、先頭の候補の生成説明に
    # 予算を使い切らせない (#730)。21 親のうち 17 番目の親の ※ 注記（標準回答の操作）が予算外に落ちていた。
    # 取り置きは最終的な処理順（定義質問の並べ替え後）で数える。並べ替え前に数えると index と後続候補が
    # 対応せず、定義質問で取り置きが過不足になる (#744)。
    def native_need(record: Any) -> int:
        return min(NATIVE_RESERVE_CAP, sum(b - a for a, b in _native_ranges(record) if b - a <= 600))
    reserve_after = [0] * (len(ordered) + 1)
    for position in range(len(ordered) - 1, -1, -1):
        reserve_after[position] = reserve_after[position + 1] + native_need(ordered[position][1])
    for index, (key, record) in enumerate(ordered):
        text = str(getattr(record, "text", "") or "")
        document_key = document_context_key(record)
        prefix = answer_metadata_context(record, max_chars=min(1200, context_remaining, max(0, remaining // 10)),
                                         include_first_page=document_key not in seen_documents)
        context_remaining -= len(prefix)
        remaining -= len(prefix)
        required = definition_windows(text, labels)
        located = _child_ranges(record, anchors)
        scoped = [(a, b, page, child) for child in scope_records
                  for a, b, page in _child_ranges(record, [child])]
        image_ranges = _child_ranges(record, [a for a in anchors if any(
            ref.get('category') == 'Picture' for ref in getattr(a, 'source_record_refs', ()))])
        native_ranges = _native_ranges(record)
        # CRAGのchild本文を保護し、同じ出典の親本文中の位置と実頁を引き継ぐ。
        pinned = [] if definitions_only else [(a, b) for a, b, _ in located]
        # 原文（解析本文）の短い範囲は質問の種類・画像の有無に関係なく先に確保し、残りの予算で生成説明・OCR を
        # 入れる (#687)。親 6,000 字では画面の生成説明（1 枚 1,000〜3,000 字）が予算を食い、原文の手順行
        # （「③〇〇ボタンを押します。」）が抜粋から落ちて、引用照合やボタン名の検査の根拠を失っていた。
        # 全体の予算は既存の remaining で制限する。
        if not definitions_only:
            pinned.extend((a, b) for a, b in native_ranges if b - a <= 600)
        required = sorted(set([*required, *pinned]))
        needed = sum(b-a for a, b in required) + 8 * max(0, len(required)-1)
        allowance = min(remaining, max(remaining // max(1, len(ordered)-index), needed))
        if not labels and not anchors:
            allowance = min(remaining, max(1, budget // max(1, len(unique))))
        # 取り置き分を超えない。ただし自分の必須範囲（原文・定義・照合済み child）は確保する。
        allowance = min(allowance, max(needed, remaining - reserve_after[index + 1]))
        excerpt = evidence_excerpt(question, text, allowance, required_ranges=required, required_only=definitions_only)
        remaining -= len(excerpt)
        cursor = 0
        parts = []
        for part in excerpt.split("\n[…]\n"):
            offset = text.find(part, cursor)
            if offset < 0:
                continue
            boundaries = [*((a, b) for a, b, _ in located), *native_ranges,
                          *((a, b) for a, b, _, _ in scoped)]
            cuts = sorted({0, len(part), *[edge-offset for a, b in boundaries for edge in (a,b)
                                         if offset < edge < offset+len(part)]})
            parts.extend(part[a:b] for a,b in zip(cuts,cuts[1:]))
            cursor = offset + len(part)
        cursor = 0
        for part in parts:
            if not part:
                continue
            start = text.find(part, cursor)
            if start < 0:
                continue
            end = start + len(part)
            source_id = getattr(record, "chunk_uid", "") or record.id
            span_id = "E" + hashlib.sha256(f"{source_id}:{start}:{end}:{part}".encode()).hexdigest()[:20]
            pages = {page for a, b, page in located if a <= start and end <= b}
            # 親の前置きと連結した窓も、含まれるchildが単一頁の場合だけ頁を確定する。
            if not pages:
                pages = {page for a, b, page in located if start <= a and b <= end and text[start:a].strip() == '' and text[b:end].strip() == ''}
            result.append({"evidence_id": span_id, "source_id": source_id,
                "document_scope": list(document_context_key(record)),
                # 分類は v4 で document 配下 (#814)。
                "business_scope": str((((getattr(record, 'metadata', {}) or {}).get('document') or {}).get('classification') or {}).get('large_category') or ''),
                "source": getattr(record, "source", ""), "page": getattr(record, "page", 0),
                # 本文位置から頁を確定できない span は親の先頭頁を page に持つ。親は複数頁にまたがるため、
                # page だけを見ると span の出どころを実際より狭く見積もる。確定できた span は page_end == page。
                "page_end": _record_page_end(record),
                "section_path": (getattr(record, "metadata", {}) or {}).get("section_path", []),
                # 見出しの出所（v4）。無い旧データでは空で、function_key は全要素を Docling 由来として扱う。
                "section_path_sources": (getattr(record, "metadata", {}) or {}).get("section_path_sources", []),
                "start": start, "end": end, "text": part, "excerpt_only": len(excerpt) < len(text),
                "source_aliases": list(dict.fromkeys(aliases[key])),
                # 抽出方法とデータの用途は別軸。正確なOCRでも文書の表示例を
                # 今回の業務レコードの実値へ昇格させない。
                "value_context": "reference_document",
                "origin": _span_origin(record, start, end, image_ranges)})
            if prefix:
                result[-1]['answer_context'] = prefix
                prefix = ''
                seen_documents.add(document_key)
            if len(pages) == 1:
                result[-1]['page'] = result[-1]['page_end'] = next(iter(pages))
            # parentの章一覧には隣接する別機能が混在し得る。本文位置と文書・版・
            # 親子関係を照合した単一childだけで、引用そのものの所属を確定する。
            matching = [(a, child) for a, b, _, child in scoped if a <= start and end <= b]
            scopes = {(_local_child_path(child, text[a:start]), child.page) for a, child in matching}
            if len(scopes) == 1:
                path, page = next(iter(scopes))
                if path:
                    result[-1].update(section_path=list(path), page=page, page_end=page,
                                      scope_source_ids=sorted({c.chunk_uid or c.id for _, c in matching}))
            cursor = end
    return _merge_adjacent_spans(result)


def _merge_adjacent_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ出典で連続し、由来・頁・所属が同じ抜粋を1つに戻す。

    解析が1文を数字や句ごとの細かい原文範囲へ分けると、境界で切った抜粋が「10」「5」や
    改行だけの断片になり、文をまたぐ引用がどの根拠にも一致しなくなる。由来や頁が変わる
    境界は引用の分類に必要なので残す。空白だけの断片と、同じ所属の本文に挟まれた短い
    未分類の断片（原文範囲の隙間の助詞・単位など）は、前後をつなぐ部分として扱う。
    """
    # page_end も一致条件に入れる。頁を確定できた span（page_end == page）と、親の範囲のままの span を
    # つなぐと、確定していない側まで 1 頁に決まったように見えてしまう。
    same = ("source_id", "origin", "page", "page_end", "section_path", "excerpt_only", "value_context", "scope_source_ids")

    def joinable(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (left["end"] == right["start"] and "answer_context" not in right
                and all(left.get(key) == right.get(key) for key in same))

    def glue(index: int, previous: dict[str, Any]) -> bool:
        span = spans[index]
        if previous["end"] != span["start"] or previous["source_id"] != span["source_id"] or "answer_context" in span:
            return False
        if not span["text"].strip():
            return True
        following = spans[index + 1] if index + 1 < len(spans) else None
        return (span.get("origin") == "unclassified" and len(span["text"].strip()) <= 8 and following is not None
                and following["start"] == span["end"] and all(previous.get(k) == following.get(k) for k in same))

    merged: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        previous = merged[-1] if merged else None
        if previous is not None and (joinable(previous, span) or glue(index, previous)):
            text = previous["text"] + span["text"]
            previous.update(text=text, end=span["end"], evidence_id="E" + hashlib.sha256(
                f"{previous['source_id']}:{previous['start']}:{span['end']}:{text}".encode()).hexdigest()[:20])
        else:
            merged.append(dict(span))
    return [span for span in merged if span["text"].strip()]


def _local_child_path(child: Any, preceding: str) -> tuple[str, ...]:
    """child内の明示機能見出し以降だけ所属とする。隣接だけでは旧章を除かない。

    章切替直後のchildにも旧章metadataが残るため、本文中の独立した見出し行と
    metadataの完全一致を要求する。名称から操作入口の存在は推論しない。
    """
    from docrag.retrieval.operation_context import operation_labels
    from types import SimpleNamespace
    path = tuple((getattr(child, 'metadata', {}) or {}).get('section_path', []))
    def compact(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
    selected = None
    for line in preceding.splitlines():
        for index, label in enumerate(path):
            if (compact(line) == compact(label) and re.search(r'[-－—―ー]\([0-9A-Z]+\)', compact(label))
                    and len(operation_labels(
                    SimpleNamespace(metadata={'section_path': [label]}))) == 1):
                selected = index
    return path[selected:] if selected is not None else path


def _native_ranges(record: Any) -> list[tuple[int, int]]:
    """本文内の有効な原文位置だけを返し、旧metadataでは空にする。"""
    ranges = ((getattr(record, 'metadata', {}) or {}).get('layout') or {}).get('native_text_ranges') or []
    return sorted((r['start'], r['end']) for r in ranges if isinstance(r, dict)
                  and type(r.get('start')) is int and type(r.get('end')) is int
                  and 0 <= r['start'] < r['end'] <= len(record.text))


def _span_origin(record: Any, start: int, end: int,
                 image_ranges: Sequence[tuple[int, int, Any]]) -> str:
    """解析原文の範囲に完全に収まる抜粋だけを文書本文と分類する。"""
    cursor = start
    for a, b in _native_ranges(record):
        if b <= cursor or a >= end:
            continue
        if record.text[cursor:max(cursor, a)].strip():
            break
        cursor = max(cursor, min(end, b))
    if cursor > start and not record.text[cursor:end].strip():
        return 'document_text'
    if any(a <= start and end <= b for a, b, _ in image_ranges):
        return 'image_extraction'
    return 'unclassified'


def _child_ranges(parent: Any, anchors: Sequence[Any]) -> list[tuple[int, int, Any]]:
    """同文書・同版・親子関係と本文一致を検証してからchildの位置と頁を返す。"""
    found = []
    parent_key = getattr(parent, 'chunk_uid', '') or parent.id
    for child in anchors:
        key = getattr(child, 'parent_chunk_uid', '') or getattr(child, 'parent_chunk_id', '')
        if key not in {parent_key, getattr(parent, 'chunk_id', '')}:
            continue
        if (not getattr(parent, 'source', '') or getattr(child, 'source', '') != getattr(parent, 'source', '')
                or getattr(child, 'source_run_id', '') != getattr(parent, 'source_run_id', '')
                or (getattr(child, 'chunk_uid', '') and getattr(parent, 'chunk_uid', '')
                    and child.chunk_uid.partition(':')[0] != parent.chunk_uid.partition(':')[0])):
            continue
        body = child.text.split('Child text:\n', 1)[-1].strip()
        start = parent.text.find(body) if body else -1
        if start >= 0 and parent.text.find(body, start+1) < 0:
            found.append((start, start+len(body), getattr(child, 'page', 0)))
    return found


def _native_text(record: Any) -> str:
    """解析本文（native_text_ranges）だけをつないだ文字列。旧 metadata で範囲がなければ全文。"""
    ranges = _native_ranges(record)
    text = str(getattr(record, "text", "") or "")
    return "\n".join(text[a:b] for a, b in ranges) if ranges else text


def _mentions(term: str, corpus: str) -> bool:
    """corpus に term が完全一致か略称（文字が順に並ぶ term の 2 倍程度までの範囲）で現れるか (#700 と同じ)。"""
    needle = re.sub(r"\s+", "", unicodedata.normalize("NFKC", term))
    if not needle:
        return False
    if needle in corpus:
        return True
    pattern = ("." + "{0,%d}?" % (len(needle) + 2)).join(re.escape(c) for c in needle)
    return any(len(m.group()) <= len(needle) * 2 + 2 for m in re.finditer(pattern, corpus))


def _question_targets(question: str) -> list[str]:
    """同一ユニット優先の起点を選ぶための質問の対象（操作対象・帳票・画面）。"""
    contract = task_contract(question)
    return [t for t in dict.fromkeys([*contract.get("action_targets", ()), *contract.get("business_objects", ())]) if len(t) >= 2]


def _same_unit_first(ordered: list[tuple[str, Any]], question: str = "") -> list[tuple[str, Any]]:
    """最上位候補と同じ機能ユニットの候補を直後に寄せ、原文予算をまず同じ機能に使う (#665)。

    機能ユニットは section_path の番号付き見出しまで（`chunking._section_unit`）。1 機能が複数の親に
    割れているとき、検索順のままだと別文書・別機能の候補に予算が先に配られ、同じ機能の残りの手順が
    予算外へ押し出される。安定ソートなので、それ以外の順序は変えない。

    起点は、質問の対象（`action_targets` / `business_objects`）を**解析本文**（生成説明を除く）に含む最初の
    候補にする (#730)。画面の生成説明の画面例の値（「出荷基準年月=令和6年9月」）だけが質問の語に一致して
    最上位になった候補を、機能ユニットの起点にしない。該当する候補がなければ従来どおり先頭。
    """
    from docrag.chunking import _section_unit
    if not ordered:
        return ordered
    def unit(record: Any) -> tuple[str, ...]:
        path = (getattr(record, "metadata", {}) or {}).get("section_path") or []
        return (str(getattr(record, "source", "")), *_section_unit(path))
    targets = _question_targets(question) if question else []
    anchor = ordered[0][1]
    if targets:
        for _, record in ordered:
            corpus = re.sub(r"\s+", "", unicodedata.normalize("NFKC", _native_text(record)))
            if any(_mentions(t, corpus) for t in targets):
                anchor = record
                break
    top = unit(anchor)
    if len(top) <= 1:
        return ordered
    return sorted(ordered, key=lambda item: unit(item[1]) != top)


def evidence_packet(question: str, records: Sequence[Any], *, budget: int = EVIDENCE_BUDGET_CHARS) -> str:
    """操作単位の原文と安定IDを整形する。全文と版参照は呼出元へ保持する。"""
    return format_evidence_packet(evidence_spans(question, records, budget=budget))


def format_evidence_packet(spans: Sequence[dict[str, Any]]) -> str:
    """選択済みの原文を再切出しせず、計画・生成・監査へ同じIDで渡す。"""
    return "\n\n".join(
        f"Source ID: {span['source_id']}\nEvidence ID: {span['evidence_id']}\n"
        f"Document: {span['source']} / page {span['page']}\n"
        f"Section: {json.dumps(span['section_path'], ensure_ascii=False)}\n"
        f"Origin: {span.get('origin', 'unclassified')}\n"
        f"Value context: {span.get('value_context', 'reference_document')} (not verified as current case data)\n"
        f"Excerpt only: {str(span['excerpt_only']).lower()}\n"
        + (span.get('answer_context', '') + '\n' if span.get('answer_context') else '')
        + span['text']
        for span in spans)
