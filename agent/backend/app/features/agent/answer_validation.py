"""回答の最終の検証（#1246・#1277）。

組み込み Runtime は回答を保存する前に（`agent_final_validation_enabled`。既定 on）、その Run で
RAG が返した根拠（`rag_search` / `rag_retrieve_evidence` の `document_id`・`chunk_id`）で、
RAG の MCP `rag_validate_answer` を呼ぶ。呼ぶのはモデルではなく Control Plane で、ツールの境界
（`tool_registry.invoke` のポリシー・監査、Run の利用者のサービストークン、Run の step）を通す。

- 根拠の id は返した RAG のものなので、根拠を返した MCP 接続ごとに、その接続の根拠で 1 回ずつ
  呼び、段落（`answer_quote`）ごとに判定をまとめる。
- 結果は Run の成果物（kind=`answer_validation`・「資料との照合」）に残す（接続ごとの結果は
  `connections`）。状態は `completed`（判定が出た）/ `unvalidated`（確かめられなかった）/
  `skipped`（確かめる対象が無い）。
- 接続ごとに、その接続の最も新しい `rag_search` の `requests`・`gaps`・`guide`（無ければ
  `rag_lookup_guides` の最上位の業務ガイド）も渡し、RAG の決定的な検査（要求の充足・手順の順序と
  分岐・影響範囲・承認。#1276）を動かす。その接続の `rag_search` の `trace_id`
  （`answer_trace_ids`）も渡し、RAG の回答の監査を通った説明と本文・根拠が同じ段落は、RAG が
  その判定を使ってモデルに渡さない（#1527。根拠は RAG が今の権限と版で読み直す。すべての段落が
  当たればモデルを呼ばない）。
- 判定が valid でなければ、根拠で確かめられなかった段落（裏付けが無い・矛盾など）を回答から外し、
  外した内容と理由を「資料で確認できず除いた内容」として足す（確かめていない操作の手順を公開しない。
  handoff §12）。根拠で確かめた段落と、主張ではない行（見出しなど）は残す。決定的な検査の error も
  同じく扱う: 手順・影響範囲の error は手順を確かめられないので本文を載せず、要求の error（答えて
  いない要求を示していない）は本文を残して不足を示す。warning は出さない。根拠で否定された段落
  （裏付けが無い・矛盾）が無いのに、出典を照合できない・確かめが終わらない段落を外すと本文が空に
  なるときは、検証が確かめきれなかったものとして本文を残し、確かめきれなかった点を示す（#1391）。
  番号つきの一覧の段落を外したら、同じ一覧のそれより後の操作の項目も、確かめられない前提に続く操作
  として外す（実行できる手順として単独で残さない。事実を述べる項目は残す。#2604）。外した操作を
  名前だけで示す番号つきの項目と、中身をすべて外した番号つきの見出しも外し、残った項目の番号を
  詰める（#2692）。
- 検証そのもの（呼び出し・接続・応答）が失敗したら `unvalidated` にし、回答は消さずに
  「この回答は資料との照合を終えられず、本質問の条件への該当は確認できていません。」を足す
  （基盤の障害で内容を落とさない）。
- RAG の根拠を使っていない Run は、RAG の根拠のツールを持つ Agent なら `unvalidated`
  （`no_rag_evidence`）にして「資料と照らし合わせて確かめていない」と足し、持たない Agent は
  `skipped`（回答はそのまま）。利用者への確認の質問だけの回答は、資料の主張を含まないので
  `skipped`（`clarification_only`）にして注記しない（#1306）。資料に無い・答えられないことだけを
  述べる回答（拒答と「ご了承ください」などの結びだけ）も同じく `skipped`（`absence_only`）にする
  （#1879）。
- 会話の続きで、今のターンがモデルの根拠・データのツールで結果を得ずに答えた（前の回答の整形・
  言い換え）ときは、前のターンの検証で回答の段落を裏付けた根拠（`carried_evidence`。#1837）だけで
  検証する（`carried_evidence_groups`。#1879）。前のターンの事実を言い直した段落は裏付けられ、
  新しい主張は確かめられない段落として外れる。
- 主張ではない段落（見出し・出典の行・表の区切りと見出しの行・利用者への質問・資料に記載が
  無いことを述べる文と拒答の文・「資料で確認できず除いた内容」の節）は、RAG の判定にかかわらず
  外さない
  （#1306・#1317。判定は `answer_passages`。成果物の段落には `non_claim` を付ける）。利用者に
  現場のデータ・記録の確認を求める段落には `environment_check` を付ける（#1317。主張のまま
  確かめて外す。回答の対応が「現場の確認が要る」の印に使う）。

ここは根拠の参照の集め方・判定のまとめ方・成果物の内容・回答の組み立てだけを持つ（呼び出しは
`builtin_runtime`）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import (
    environment_check_passages,
    is_heading,
    is_lookup_note,
    is_operation_step,
    is_structure,
    label_operation_end,
    list_heading_lines,
    list_item_label,
    non_claim_passages,
    passage_spans,
    table_header_lines,
)
from app.features.agent.evidence_match import (
    EvidenceText,
    answer_features,
    matched_features,
    order_weights,
)
from app.features.agent.support_task import (
    RAG_READ_DOCUMENT,
    RAG_READ_SECTION,
    RAG_READ_SOURCE,
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
)
from app.features.agent.tools import MCP_TOOL_SEPARATOR, mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

ANSWER_VALIDATION_KIND = "answer_validation"
ANSWER_VALIDATION_NAME = "資料との照合"
VALIDATE_ANSWER_TOOL = "rag_validate_answer"
# 検証に渡す根拠の参照の上限（`rag_validate_answer` の契約の maxItems）。
MAX_VALIDATION_EVIDENCE = 30
# 同じ会話の前のターンから引き継ぐ根拠の参照の上限（接続ごと。#1837）。
MAX_CARRIED_EVIDENCE = 10
# 契約の上限（質問は切り詰めて渡す。回答は切り詰めると別の文の検証になるため、超えたら検証しない）。
MAX_QUERY_CHARS = 8000
MAX_ANSWER_CHARS = 20000

# 判定が出た。
STATUS_COMPLETED = "completed"
# 確かめる対象が無い（回答が空・RAG の根拠のツールを持たない Agent の根拠の無い回答）。
STATUS_SKIPPED = "skipped"
# 確かめられなかった（検証の失敗・RAG の根拠を使わなかった回答）。回答に注記を足す。
STATUS_UNVALIDATED = "unvalidated"

# 決定的な検査の入力（`requests`・`gaps`・`guide.conditions`）の件数の上限（契約の maxItems）。
MAX_CHECK_ITEMS = 30
# 監査の判定を再利用する `rag_search` の回答の記録（`answer_trace_ids`）の上限と、
# trace_id の長さ（契約）。
MAX_ANSWER_TRACE_IDS = 10
MAX_TRACE_ID_CHARS = 64
RAG_LOOKUP_GUIDES = "rag_lookup_guides"
REQUEST_STATUSES = frozenset({"addressed", "partial", "missing", "unknown"})
# 回答に手順を書く業務ガイドの判断（手順・影響範囲を確かめる）。
GUIDE_DECISIONS = frozenset({"answer", "branch"})
# 本文を載せない決定的な検査の error（手順・影響範囲・業務ガイドの版。
# 確かめていない手順を出さない）。
WITHHOLDING_CHECKS = frozenset({"guide", "guide_steps", "impact"})

REASON_NO_RAG_EVIDENCE = "no_rag_evidence"
# 資料（RAG）の根拠ではなく、利用者が添付したファイルを読んで答えた（#2517）。資料と照らし合わせる
# 対象が無いので注記しない。
REASON_ATTACHMENT_EVIDENCE = "attachment_evidence"
REASON_VALIDATOR_UNAVAILABLE = "validator_unavailable"
REASON_EMPTY_ANSWER = "empty_answer"
# 利用者への確認の質問だけの回答（資料の主張を含まないので、根拠が無くても注記しない。#1306）。
REASON_CLARIFICATION_ONLY = "clarification_only"
# 利用者が「資料の検索は不要」と確認を取り消した Run（#1445。資料を使わずに答えるので注記しない）。
REASON_RAG_DECLINED = "rag_declined"
# 資料に無い・答えられないことだけを述べる回答（拒答。資料の主張を含まないので、根拠が無くても
# 注記しない。#1879）。
REASON_ABSENCE_ONLY = "absence_only"
REASON_ANSWER_TOO_LONG = "answer_too_long"
REASON_CONNECTION_NOT_FOUND = "connection_not_found"
REASON_UNUSABLE_RESULT = "unusable_result"
REASON_VALIDATION_ERROR = "validation_error"

# RAG の「参考情報」の節の説明（該当は確認できていない）と同じ言い方で、照合できなかったことを示す。
UNVERIFIED_NOTICE = "この回答は資料との照合を終えられず、本質問の条件への該当は確認できていません。"
NO_EVIDENCE_NOTICE = "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。"
UNVERIFIED_HEADING = "資料で確認できず除いた内容"
WITHHELD_INTRO = "根拠で確かめられなかった次の内容は、回答に載せていません。"
NOTHING_CONFIRMED = "根拠で確かめられた内容はありませんでした。"
# 根拠で否定された段落が無いのに、出典を照合できない段落を外すと本文が空になるとき（#1391）。
UNVERIFIED_KEPT_INTRO = (
    "次の内容は、根拠の出典と照らし合わせられず確かめきれませんでした。"
    "根拠と矛盾する・根拠に無いという判定ではないため、回答の本文は載せています。"
)
GUIDE_WITHHELD = (
    "業務ガイドの手順・影響範囲と照らして確かめられない点があるため、回答の本文は載せていません。"
)
EVIDENCE_TOOLS = frozenset({RAG_SEARCH, RAG_RETRIEVE_EVIDENCE})
# 本文を読むツール（#1330・#1332）。多段の質問では段の事実を読んで確かめるため、読んだ chunk も
# 最終の検証の根拠に入れる（#1345）。
READ_EVIDENCE_TOOLS = frozenset({RAG_READ_SOURCE, RAG_READ_DOCUMENT, RAG_READ_SECTION})
# 文書・章節をまとめて読むツール。回答に当たらない chunk は検索の根拠の後ろに置く（#1451）。
BULK_READ_TOOLS = frozenset({RAG_READ_DOCUMENT, RAG_READ_SECTION})
# 回答に載せない段落の判定（RAG の `is_valid` が検証に通さない判定と同じ）。
BLOCKING_CLAIM_STATUSES = frozenset({"unsupported", "contradicted", "citation_error", "unassessed"})
_CLAIM_LABELS = {
    "contradicted": "根拠と矛盾",
    "unsupported": "根拠で確かめられない",
    "citation_error": "出典を確かめられない",
    "unassessed": "確かめが終わっていない",
}
# RAG が監査の裏付け（supported）を採らなかった理由（`support_check`。#2659・#2653）ごとの、
# 節の項目の理由。語・話題が根拠と一致しても対象・表の行の対応が違う段落は、違いの種類を示す。
_SUPPORT_CHECK_LABELS = {
    "target_mismatch": "質問と別の対象についての記載",
    "value_unbound": "根拠の表の対応と違う",
}
# 外した段落に続く番号つきの手順の段落の、節の項目の理由（#2604。`dependent_steps`）。
DEPENDENT_STEP_LABEL = "確かめられない手順に続く手順"
# 根拠で否定した判定（根拠に無い・根拠と矛盾）。出典を照合できない（`citation_error`）・
# 確かめが終わらない（`unassessed`）は、根拠が主張を否定したのではなく、検証が確かめ
# きれなかったもの（#1391）。
REFUTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
# 判定として使える `rag_validate_answer` の結果の status（それ以外は検証の失敗として扱う）。
_USABLE_RESULT_STATUSES = ("completed", "no_evidence", "no_claims")
# 節の項目は 1 行に縮める（段落・指摘の全文は成果物の判定に残る）。項目の数は切らない（#2361。
# 画面の件数のバッジと節の項目の数を一致させ、同じ種類の項目を 2 か所に分けない）。
_MAX_QUOTE_CHARS = 80
_MAX_FINDING_CHARS = 120


def is_refuted_claim(claim: JsonObject) -> bool:
    """根拠で否定した判定か（`REFUTED_CLAIM_STATUSES`）。

    RAG が根拠の ID を照合できずに判定（`status`）を変えても、監査モデルが返した元の判定
    （`audit_status`）が否定なら否定として扱う（#2622。引用の照合の誤りで矛盾の判定が消え、
    矛盾した本文を「否定の判定が無い」として載せていた）。
    """
    return (
        claim.get("status") in REFUTED_CLAIM_STATUSES
        or claim.get("audit_status") in REFUTED_CLAIM_STATUSES
    )


def _is_contradicted(claim: JsonObject) -> bool:
    """根拠と矛盾する判定か（元の判定が矛盾のものを含む。#2622）。"""
    return "contradicted" in {claim.get("status"), claim.get("audit_status")}


def _short(value: object, limit: int) -> str:
    """1 行にして `limit` 文字まで（超えたら末尾を「…」にする）。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _connection_prefix(function_name: str) -> str | None:
    """`<接続>__<ツール>` の接続の部分（MCP 接続のツールでなければ None）。"""
    if MCP_TOOL_SEPARATOR not in function_name:
        return None
    return function_name[: -len(mcp_base_tool_name(function_name)) - len(MCP_TOOL_SEPARATOR)]


def locator_labels(item: JsonObject) -> list[object]:
    """根拠の見出し（章節の列・章節の名前・シート名）。回答と照らすのに使う（#1451）。"""
    labels: list[object] = []
    locator = item.get("locator")
    if isinstance(locator, dict):
        path = locator.get("section_path")
        labels.extend(path if isinstance(path, list) else [])
        labels.append(locator.get("sheet_name"))
    section = item.get("section")
    if isinstance(section, dict):
        labels.append(section.get("title"))
    path = item.get("section_path")
    labels.extend(path if isinstance(path, list) else [])
    # 読み取りのツールの chunk は表計算のシート名を chunk に持つ（#1471）。
    labels.append(item.get("sheet_name"))
    return labels


_EvidenceRef = tuple[tuple[str, str], object, list[object], list[object]]


def _evidence_refs(tool: str, output: JsonObject) -> list[_EvidenceRef]:
    """根拠を集める・読むツールの出力の（参照, 本文, 見出し, 文書のファイル名）（出てきた順）。"""
    items: list[tuple[object, object, list[object], list[object]]]
    if tool in EVIDENCE_TOOLS:
        evidence = output.get("evidence")
        items = [
            (item, item.get("excerpt"), locator_labels(item), [item.get("file_name")])
            for item in (evidence if isinstance(evidence, list) else [])
            if isinstance(item, dict)
        ]
    elif tool == RAG_READ_SOURCE:
        items = [(output, output.get("text"), locator_labels(output), [output.get("file_name")])]
    elif tool in {RAG_READ_DOCUMENT, RAG_READ_SECTION}:
        chunks = output.get("chunks")
        document_id = output.get("document_id")
        body = output.get("text")
        body = body if isinstance(body, str) else ""
        section_labels = locator_labels(output)
        names = [output.get("file_name")]
        items = []
        for chunk in chunks if isinstance(chunks, list) else []:
            if not isinstance(chunk, dict):
                continue
            start, end = chunk.get("start"), chunk.get("end")
            chunk_text = (
                body[start:end]
                if isinstance(start, int) and isinstance(end, int) and start < end
                else ""
            )
            ref = {"document_id": document_id, "chunk_id": chunk.get("chunk_id")}
            items.append((ref, chunk_text, [*locator_labels(chunk), *section_labels], names))
    else:
        return []
    refs: list[_EvidenceRef] = []
    for item, text, labels, file_names in items:
        if not isinstance(item, dict):
            continue
        document_id, chunk_id = item.get("document_id"), item.get("chunk_id")
        if isinstance(document_id, str) and isinstance(chunk_id, str) and document_id and chunk_id:
            refs.append(((document_id, chunk_id), text, labels, file_names))
    return refs


def _cited_in(answer: str, chunk_id: str) -> bool:
    """回答が根拠の id（MCP の `evidence_id` は `chunk_id` と同じ）を書いているか。

    `…:1` が `…:19` の先頭に一致しないよう、後ろが数字で続くものは数えない。
    """
    return bool(re.search(re.escape(chunk_id) + r"(?!\d)", answer))


# 回答が書いた chunk の id の形（「文書:chunk_set:番号」と、途中を「…」で省いた・先頭の文書の
# id を落とした・区切りを横線で書いた形）。RAG の最終の検証が段落の ID を根拠に結び付ける規則と
# 同じ形で数える。
_ID_DASHES = "‐‑‒–—―-"
_CITED_ID = re.compile(
    rf"(?<![0-9A-Za-z])[0-9A-Fa-f]{{6,}}(?:[0-9A-Fa-f:…‥{_ID_DASHES}]|\.{{2,}})*[:{_ID_DASHES}][0-9]+(?![0-9A-Za-z])"
)
_ID_ELLIPSIS = re.compile(r"…+|‥+|\.{2,}")
# 省いて書いた ID の残した先頭の最短の長さ（これより短いと別の根拠と区別しにくい）。
_MIN_ABBREVIATED_ID_CHARS = 7


def _abbreviated_part(written: str, actual: str) -> bool:
    """ID の部分を、後ろを切って・途中を「…」で省いて書いたか。"""
    pieces = _ID_ELLIPSIS.split(written)
    if len(pieces[0]) < _MIN_ABBREVIATED_ID_CHARS:
        return False
    if len(pieces) == 1:
        return len(written) < len(actual) and actual.startswith(written)
    return re.fullmatch(".+".join(re.escape(piece) for piece in pieces), actual) is not None


def _written_id_matches(written: str, actual: str) -> bool:
    """回答が省いて書いた chunk の id（`written`）が `actual` を指すか（番号はそのまま一致）。"""
    # 区切りの横線（「78f40985‑…‑244」）は「:」にし、省略の印の直前の区切りは除く。
    written = re.sub(r":+(?=…|‥|\.{2,})", "", re.sub(f"[{_ID_DASHES}]", ":", written))
    written, actual = written.lower(), actual.lower()
    if written == actual or (_ID_ELLIPSIS.search(written) and _abbreviated_part(written, actual)):
        return True
    written_parts, actual_parts = written.split(":"), actual.split(":")
    if 2 <= len(written_parts) < len(actual_parts):
        actual_parts = actual_parts[-len(written_parts) :]
    if len(written_parts) != len(actual_parts) or written_parts[-1] != actual_parts[-1]:
        return False
    return all(
        w == a or _abbreviated_part(w, a)
        for w, a in zip(written_parts[:-1], actual_parts[:-1], strict=True)
    )


def cited_chunk_ids(answer: str, chunk_ids: Sequence[str]) -> set[str]:
    """回答が書いた根拠の id（そのままの形と、省いて書いて 1 つの根拠に決まる形）。"""
    cited = {chunk_id for chunk_id in chunk_ids if _cited_in(answer, chunk_id)}
    for match in dict.fromkeys(item.group(0) for item in _CITED_ID.finditer(answer)):
        matches = {chunk_id for chunk_id in chunk_ids if _written_id_matches(match, chunk_id)}
        if len(matches) == 1:
            cited |= matches
    return cited


@dataclass
class _GroupRef:
    """接続の中の根拠 1 件（並べ替えの材料）。"""

    ref: tuple[str, str]
    order: int
    evidence: EvidenceText = field(default_factory=EvidenceText)
    # 文書・章節をまとめて読んだ（rag_read_document / rag_read_section）だけの根拠か。
    bulk_read: bool = True
    # 同じ会話の前のターンの最終の検証で、回答の段落を裏付けた根拠か（#1837）。
    carried: bool = False


def _ordered(refs: list[_GroupRef], answer: str) -> list[tuple[str, str]]:
    """回答が使った根拠を先にした並び（#1364・#1451）。

    1. 回答が `chunk_id` を書いた根拠（省いて書いて 1 つに決まる形を含む。新しい呼び出しから順）
    2. 回答に固有の特徴（ID・コード・引用・見出し・同じ並び・「文書名＋第 N 章」の章節の参照）で
       当たった根拠（重みの大きい順）
    3. 同じ会話の前のターンで回答の段落を裏付けた根拠（#1837。新しいターンから順）
    4. 残りの検索・本文の読み取り（rag_read_source）の根拠（当たりの重み、新しい呼び出しの順）
    5. 残りの、文書・章節をまとめて読んだ chunk（全文を読んだ台帳の行が上限を埋めない）
    """
    if not answer:
        return [item.ref for item in refs]
    features = answer_features(answer)
    weights = order_weights([matched_features(features, item.evidence) for item in refs])
    cited = cited_chunk_ids(answer, [item.ref[1] for item in refs])

    def key(index: int) -> tuple[int, float, int]:
        item = refs[index]
        if item.ref[1] in cited:
            return (0, 0.0, item.order)
        distinct, weight = weights[index]
        if distinct:
            return (1, -weight, item.order)
        if item.carried:
            return (2, 0.0, item.order)
        return (4 if item.bulk_read else 3, -weight, item.order)

    return [refs[index].ref for index in sorted(range(len(refs)), key=key)]


def carried_evidence(validations: list[JsonObject]) -> dict[str, list[JsonObject]]:
    """同じ会話の前のターンの最終の検証で、回答の段落を裏付けた（`supported`）根拠の参照（#1837）。

    `validations` は前のターンの最終の検証の成果物の内容（古い順。`thread_answer_validations`）。
    返すのは MCP 接続（function tool の名前の接続の部分）ごとの参照で、新しいターンから順、接続の
    中で重複なし、最大 `MAX_CARRIED_EVIDENCE` 件。会話の続きでモデルが前のターンの事実を言い直すと、
    今のターンの根拠だけでは裏付けが無く外れるため、前のターンで実際に確かめた根拠も検証に渡す
    （本文は RAG が今の利用者の権限・データの範囲・版で読み直す。削除・改版されていれば
    `missing_evidence` / `stale_evidence` になる）。判定が出なかったターン・接続は使わない。
    """
    carried: dict[str, list[JsonObject]] = {}
    for content in reversed(validations):
        if content.get("status") != STATUS_COMPLETED:
            continue
        connections = content.get("connections")
        for connection in connections if isinstance(connections, list) else []:
            if not isinstance(connection, dict) or connection.get("status") != STATUS_COMPLETED:
                continue
            prefix = _connection_prefix(str(connection.get("tool_name") or ""))
            result = connection.get("result")
            evidence = connection.get("evidence")
            if prefix is None or not isinstance(result, dict) or not isinstance(evidence, list):
                continue
            # 判定の chunk_id は検証に渡した根拠のもの（文書の id は渡した参照から引く）。
            documents = {
                ref.get("chunk_id"): ref.get("document_id")
                for ref in evidence
                if isinstance(ref, dict)
            }
            refs = carried.setdefault(prefix, [])
            claims = result.get("claims")
            for claim in claims if isinstance(claims, list) else []:
                if not isinstance(claim, dict) or claim.get("status") != "supported":
                    continue
                chunk_id = claim.get("chunk_id")
                document_id = documents.get(chunk_id)
                if not isinstance(chunk_id, str) or not isinstance(document_id, str):
                    continue
                ref = {"document_id": document_id, "chunk_id": chunk_id}
                if ref not in refs and len(refs) < MAX_CARRIED_EVIDENCE:
                    refs.append(ref)
    return {prefix: refs for prefix, refs in carried.items() if refs}


def run_evidence_groups(
    steps: list[RunStep],
    answer: str = "",
    carried: dict[str, list[JsonObject]] | None = None,
) -> list[tuple[str, list[JsonObject]]]:
    """その Run で RAG が返した根拠の参照を、MCP 接続ごとにまとめる（#1277）。

    根拠の id は返した RAG のものなので、接続ごとに分けて検証する。返すのは、接続ごとの
    （その接続の最も新しい根拠のツールの function tool の名前〔接続を決める〕, 参照の一覧）で、
    最も新しく根拠を返した接続から順。参照は新しい呼び出しから順、接続の中で重複なし、最大 30 件。
    `rag_read_source`・`rag_read_document`・`rag_read_section`（#1438）で読んだ chunk も根拠に
    入れる（#1345。多段の質問は段の事実を読んで確かめる）。
    `answer` を渡すと、回答が使った根拠を接続の中で先にする（#1364・#1451。多段の質問は前の段の
    根拠が古い呼び出しにあり、上限で切ると回答が使った根拠が検証に入らず、正しい結論が「確かめ
    られない」になる）。回答が `chunk_id` を書いた根拠、回答の本文と当たった根拠（`evidence_match`）
    の順に先にし、文書・章節をまとめて読んだ chunk で回答に当たらないものは検索の根拠の後ろに置く。
    `carried`（`carried_evidence`。#1837）を渡すと、その Run で根拠を返した接続には、同じ会話の前の
    ターンで回答の段落を裏付けた根拠も入れる（回答に当たる根拠の後ろ、残りの検索の根拠の前）。
    その Run で根拠を返さなかった接続には入れない（前のターンの根拠だけで検証するのは、その Run が
    根拠・データのツールで結果を得なかったときの `carried_evidence_groups`。#1879）。
    """
    groups: dict[str, tuple[str, dict[tuple[str, str], _GroupRef]]] = {}
    for step in reversed(steps):
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        tool = mcp_base_tool_name(call.name)
        if tool not in EVIDENCE_TOOLS and tool not in READ_EVIDENCE_TOOLS:
            continue
        if not isinstance(result.output, dict):
            continue
        refs_found = _evidence_refs(tool, result.output)
        prefix = _connection_prefix(call.name)
        if prefix is None or not refs_found:
            continue
        _tool_name, refs = groups.setdefault(prefix, (call.name, {}))
        for ref, text, labels, file_names in refs_found:
            item = refs.get(ref)
            if item is None:
                item = refs[ref] = _GroupRef(ref=ref, order=len(refs))
            item.evidence.add(text, labels, file_names)
            if tool not in BULK_READ_TOOLS:
                item.bulk_read = False
    grouped: list[tuple[str, list[JsonObject]]] = []
    for prefix, (tool_name, refs) in groups.items():
        if not refs:
            continue
        for carried_ref in (carried or {}).get(prefix, []):
            key = (str(carried_ref["document_id"]), str(carried_ref["chunk_id"]))
            item = refs.get(key)
            if item is None:
                item = refs[key] = _GroupRef(ref=key, order=len(refs))
            item.carried = True
        ordered = _ordered(list(refs.values()), answer)[:MAX_VALIDATION_EVIDENCE]
        grouped.append(
            (tool_name, [{"document_id": ref[0], "chunk_id": ref[1]} for ref in ordered])
        )
    return grouped


def carried_evidence_groups(
    carried: dict[str, list[JsonObject]], answer: str = ""
) -> list[tuple[str, list[JsonObject]]]:
    """前のターンの検証で裏付けた根拠だけの、接続ごとの検証の入力（#1879）。

    会話の続きで、今のターンがツールで根拠を得ずに前の回答を整形・言い換えた（「前の回答を表にして」）
    ときに使う（呼ぶかは `builtin_runtime` が決める）。形は `run_evidence_groups` と同じで、接続を
    決める function tool の名前は、前のターンの検証の接続の部分（`carried_evidence` の key）に
    `rag_search` を付けたもの（`builtin_runtime._validator_connection` がその名前で接続を引く）。
    接続の並びは新しいターンの接続から順、参照は `carried_evidence` の順（新しいターンから順）。
    """
    grouped: list[tuple[str, list[JsonObject]]] = []
    for prefix, carried_refs in carried.items():
        refs: dict[tuple[str, str], _GroupRef] = {}
        for carried_ref in carried_refs:
            key = (str(carried_ref["document_id"]), str(carried_ref["chunk_id"]))
            if key not in refs:
                refs[key] = _GroupRef(ref=key, order=len(refs), carried=True)
        if not refs:
            continue
        ordered = _ordered(list(refs.values()), answer)[:MAX_VALIDATION_EVIDENCE]
        grouped.append(
            (
                f"{prefix}{MCP_TOOL_SEPARATOR}{RAG_SEARCH}",
                [{"document_id": ref[0], "chunk_id": ref[1]} for ref in ordered],
            )
        )
    return grouped


def _text_value(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _completed_outputs(
    steps: list[RunStep], prefix: str, tool: str
) -> list[tuple[JsonObject, JsonObject]]:
    """接続 `prefix` の、成功した `tool` の呼び出しの（引数, 出力）（新しい順）。"""
    found: list[tuple[JsonObject, JsonObject]] = []
    for step in reversed(steps):
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if not isinstance(result.output, dict) or _connection_prefix(call.name) != prefix:
            continue
        if mcp_base_tool_name(call.name) == tool:
            found.append((call.arguments, result.output))
    return found


# 業務データの照会の結果（#1950）を最終の検証に渡す上限（RAG の契約の maxItems・maxLength）。
MAX_DATA_RESULTS = 10
_MAX_DATA_QUESTION_CHARS = 1000
_MAX_DATA_SQL_CHARS = 4000
_MAX_DATA_RESULT_CHARS = 4000
# 結果に入れる先頭の行の数（件数の大きい一覧は先頭だけ。全体の件数は total で示す）。
_MAX_DATA_ROWS = 50
_DATA_QUERY_TOOLS = frozenset({"nl2sql_query", "nl2sql_get_job"})


def _data_result_body(output: JsonObject) -> str | None:
    """照会の結果の文（行を返した結果・SQL を作れないと返した結果。それ以外は None）。"""
    status = output.get("status")
    if status == "done":
        rows = output.get("rows")
        if not isinstance(rows, list):
            return None
        payload: JsonObject = {
            "columns": output.get("columns") or [],
            "rows": rows[:_MAX_DATA_ROWS],
            "total": output.get("total"),
            "truncated": bool(output.get("truncated") or output.get("has_more")),
        }
    elif status == "unanswerable":
        requirement = output.get("requirement")
        message = output.get("error_message") or (
            requirement.get("message") if isinstance(requirement, dict) else None
        )
        if not isinstance(message, str) or not message.strip():
            return None
        payload = {"status": "unanswerable", "message": message.strip()}
    else:
        return None
    return json.dumps(payload, ensure_ascii=False, default=str)[:_MAX_DATA_RESULT_CHARS]


def validation_data_results(steps: list[RunStep]) -> list[JsonObject]:
    """この Run で成功した業務データ（NL2SQL）の照会の結果を、検証の `data_results` にする。

    回答の件数・金額・一覧の段落を、資料の根拠だけで「確かめられない」として外さないようにする（#1950）。
    SQL を実行して行を返したもの（`status=done`。結果は列・先頭の行・件数の JSON の文）と、この業務
    プロファイルでは SQL を作れないと返したもの（`status=unanswerable`。結果は状態と理由。「要る列が
    無いので集計できない」の結論の根拠。#2232）。実行の失敗（`error`）・まだ SQL を作っていない確認
    （`needs_clarification`）は入れない。Control Plane が自分で呼んだもの（確認の取り消しなど）は
    入れない。
    """
    results: list[JsonObject] = []
    seen: set[str] = set()
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if MCP_TOOL_SEPARATOR not in call.name:
            continue
        if mcp_base_tool_name(call.name) not in _DATA_QUERY_TOOLS:
            continue
        output = result.output
        if not isinstance(output, dict):
            continue
        body = _data_result_body(output)
        if body is None:
            continue
        sql = _text_value(
            output.get("executable_sql") or output.get("generated_sql"), _MAX_DATA_SQL_CHARS
        )
        key = f"{sql}\n{body}"
        if key in seen:
            continue
        seen.add(key)
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        results.append(
            {
                "source": "データ問い合わせ（NL2SQL）",
                "question": _text_value(arguments.get("question"), _MAX_DATA_QUESTION_CHARS) or "",
                "sql": sql or "",
                "result": body,
            }
        )
    return results[-MAX_DATA_RESULTS:]


def _guide_input(guide: object, profile_id: object, conditions: object) -> JsonObject | None:
    """`rag_validate_answer` の `guide`（手順を示す判断の業務ガイドで、プロファイルが分かるとき）。

    確かめる質問・担当への引き継ぎの判断（`clarify` / `handoff`）では回答に手順が無いので渡さない
    （影響範囲・承認の検査が、手順を書かない回答を誤って止めるため）。
    """
    if not isinstance(guide, dict) or guide.get("decision", "answer") not in GUIDE_DECISIONS:
        return None
    guide_id = _text_value(guide.get("guide_id"), 64)
    revision = guide.get("revision")
    profile = _text_value(profile_id, 128)
    if guide_id is None or profile is None or not isinstance(revision, int) or revision < 1:
        return None
    known: dict[str, str] = {}
    if isinstance(conditions, dict):
        known.update(
            {
                key: value
                for key, value in conditions.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        )
    for item in guide.get("known_conditions") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            value = item.get("value")
            if isinstance(value, str) and value:
                known[item["id"]] = value
    content: JsonObject = {
        "search_answer_profile_id": profile,
        "guide_id": guide_id,
        "revision": revision,
    }
    if known:
        content["conditions"] = dict(list(known.items())[:MAX_CHECK_ITEMS])
    return content


def connection_check_inputs(steps: list[RunStep], evidence_tool: str) -> JsonObject:
    """`rag_validate_answer` の決定的な検査の入力（`requests`・`gaps`・`guide`。#1276・#1277）と、
    監査の判定を再利用する回答の記録（`answer_trace_ids`。#1527）。

    根拠を返した接続の最も新しい `rag_search` の要求ごとの充足・不足・業務ガイドを渡す。
    `answer_trace_ids` はその接続の `rag_search` の `trace_id`（新しい順、最大 10 件）。
    `rag_search` が業務ガイドを返さなければ、同じ接続の最も新しい `rag_lookup_guides` の最上位の
    業務ガイドを使う。分からない項目は渡さない（その検査は RAG が行わない）。
    """
    prefix = _connection_prefix(evidence_tool)
    if prefix is None:
        return {}
    inputs: JsonObject = {}
    guide: JsonObject | None = None
    searches = _completed_outputs(steps, prefix, RAG_SEARCH)
    # RAG の回答の監査を通った説明と本文・根拠が同じ段落は、RAG がその判定を使いモデルに
    # 渡さない（#1527）。
    # 記録は RAG が Run の利用者の範囲で読み直す（ここは trace_id を渡すだけ）。
    trace_ids = [
        trace_id
        for _arguments, output in searches
        if (trace_id := _text_value(output.get("trace_id"), MAX_TRACE_ID_CHARS))
    ]
    if trace_ids:
        inputs["answer_trace_ids"] = list(dict.fromkeys(trace_ids))[:MAX_ANSWER_TRACE_IDS]
    if searches:
        arguments, output = searches[0]
        requests = [
            {
                "id": request_id,
                "text": _text_value(item.get("text"), 2000) or "",
                "status": item["status"],
            }
            for item in output.get("requests") or []
            if isinstance(item, dict)
            and (request_id := _text_value(item.get("id"), 64))
            and item.get("status") in REQUEST_STATUSES
        ]
        if requests:
            inputs["requests"] = requests[:MAX_CHECK_ITEMS]
        gaps = [gap for item in output.get("gaps") or [] if (gap := _text_value(item, 2000))]
        if gaps:
            inputs["gaps"] = gaps[:MAX_CHECK_ITEMS]
        provenance = output.get("provenance")
        profile = (
            provenance.get("search_answer_profile_id") if isinstance(provenance, dict) else None
        ) or arguments.get("search_answer_profile_id")
        guide = _guide_input(output.get("guide"), profile, arguments.get("conditions"))
    if guide is None:
        lookups = _completed_outputs(steps, prefix, RAG_LOOKUP_GUIDES)
        if lookups:
            arguments, output = lookups[0]
            guides = output.get("guides")
            if isinstance(guides, list) and guides:
                guide = _guide_input(
                    guides[0],
                    arguments.get("search_answer_profile_id"),
                    arguments.get("conditions"),
                )
    if guide is not None:
        inputs["guide"] = guide
    return inputs


def validation_content(
    status: str,
    *,
    reason: str | None = None,
    message: str | None = None,
    connection: str | None = None,
    tool_name: str | None = None,
    step_id: str | None = None,
    evidence: list[JsonObject] | None = None,
    result: JsonObject | None = None,
) -> JsonObject:
    """成果物「資料との照合」の内容（接続ごとの結果にも使う）。"""
    valid = result.get("valid") if isinstance(result, dict) else None
    return {
        "status": status,
        "reason": reason,
        "message": message,
        "valid": valid if isinstance(valid, bool) else None,
        "connection": connection,
        "tool_name": tool_name,
        "step_id": step_id,
        "evidence": list(evidence or []),
        "result": result,
    }


def usable_result(result: object) -> bool:
    """`rag_validate_answer` の結果が判定として使えるか（使えなければ検証の失敗として扱う）。"""
    return isinstance(result, dict) and result.get("status") in _USABLE_RESULT_STATUSES


def _refs_count(value: object) -> int:
    return len([item for item in value if isinstance(item, dict)]) if isinstance(value, list) else 0


def _claim_line(claim: JsonObject) -> str:
    """「- 根拠で確かめられない: 「段落」」の 1 行（段落は 80 文字まで）。

    理由は判定の種類の短い文（`_CLAIM_LABELS`。RAG が裏付けを採らなかった理由
    〔`support_check`〕があれば `_SUPPORT_CHECK_LABELS`）だけにし、検証のモデルが書いた理由
    （`reason`）は回答の本文に
    出さない（#1833。「監査」「クレーム」「evidence_items」など内部の語を含む）。理由は成果物の判定に残り、
    実行の詳細の成果物で見られる。
    """
    quote = _short(claim.get("answer_quote"), _MAX_QUOTE_CHARS)
    label = (
        _SUPPORT_CHECK_LABELS.get(str(claim.get("support_check") or ""))
        or _CLAIM_LABELS[str(claim.get("status"))]
    )
    return f"- {label}: 「{quote}」"


def _paragraph_claim(claims: list[JsonObject]) -> JsonObject:
    """1 つの接続の、同じ段落の判定（段落に複数の主張があれば最も厳しいもの）。"""
    for claim in claims:
        if _is_contradicted(claim):
            return claim
    for claim in claims:
        if claim.get("status") in BLOCKING_CLAIM_STATUSES:
            return claim
    return next((claim for claim in claims if claim.get("status") == "supported"), claims[0])


def merge_results(results: list[JsonObject]) -> JsonObject:
    """接続ごとの `rag_validate_answer` の結果（判定として使えるもの）を 1 つにまとめる（#1277）。

    どの接続も同じ回答を同じ規則で段落に分けて確かめるので、段落（`answer_quote`）ごとにまとめる。
    接続の中では段落の最も厳しい判定、接続をまたいでは、どれかの接続が矛盾と判定すれば矛盾、
    そうでなくどれかの接続が通せば（裏付けあり・確かめる必要が無い行）その判定、どの接続も
    通さなければ最初の接続の判定（ほかの接続の根拠に無いだけで外さない）。結果が 1 つならそのまま。
    """
    if len(results) == 1:
        return results[0]
    per_result: list[dict[str, JsonObject]] = []
    order: list[str] = []
    for result in results:
        grouped: dict[str, list[JsonObject]] = {}
        for claim in result.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            quote = str(claim.get("answer_quote") or "")
            if quote not in grouped and quote not in order:
                order.append(quote)
            grouped.setdefault(quote, []).append(claim)
        per_result.append({quote: _paragraph_claim(items) for quote, items in grouped.items()})
    claims: list[JsonObject] = []
    for quote in order:
        judged = [item[quote] for item in per_result if quote in item]
        chosen = (
            next((item for item in judged if _is_contradicted(item)), None)
            or next(
                (item for item in judged if item.get("status") not in BLOCKING_CLAIM_STATUSES),
                None,
            )
            or judged[0]
        )
        claims.append(dict(chosen))
    counts: dict[str, int] = {}
    for claim in claims:
        key = str(claim.get("status"))
        counts[key] = counts.get(key, 0) + 1
    stale = [item for result in results for item in result.get("stale_evidence") or []]
    missing = [item for result in results for item in result.get("missing_evidence") or []]
    statuses = {result.get("status") for result in results}
    status = next(item for item in _USABLE_RESULT_STATUSES if item in statuses)
    # 決定的な検査の指摘は接続ごとに同じ回答を確かめたものなので、重複を除いてすべて残す。
    findings: list[JsonObject] = []
    seen_findings: set[tuple[str, str, str]] = set()
    for result in results:
        for item in result.get("findings") or []:
            if not isinstance(item, dict):
                continue
            finding_key = (
                str(item.get("check")),
                str(item.get("code")),
                str(item.get("message")),
            )
            if finding_key not in seen_findings:
                seen_findings.add(finding_key)
                findings.append(item)
    checks = sorted({str(item) for result in results for item in result.get("checks") or []})
    valid = (
        status == "completed"
        and not stale
        and not missing
        and not any(claim.get("status") in BLOCKING_CLAIM_STATUSES for claim in claims)
        and not error_findings({"findings": findings})
        and counts.get("supported", 0) > 0
    )
    return {
        "checks": checks,
        "findings": findings,
        "valid": valid,
        # RAG の回答の監査の判定を使い、モデルに渡さなかった段落の数（#1527。接続ごとの和）。
        "reused_claims": sum(int(result.get("reused_claims") or 0) for result in results),
        "status": status,
        "counts": counts,
        "claims": claims,
        "missing_evidence": missing,
        "stale_evidence": stale,
        "evidence_truncated": any(bool(result.get("evidence_truncated")) for result in results),
    }


# 前の段落を受ける接続の語（#1833）。その前の段落を外すと受ける先が無くなる（「したがって、…」
# から本文が始まる）ので、外した段落の直後の段落では語を外す。意味を変える語（「ただし」
# 「なお」）は残す。
_DANGLING_CONNECTIVE = re.compile(
    r"(?:したがって|従って|そのため|このため|よって|ゆえに|以上から|以上より|以上のことから|このように"
    r"|つまり|すなわち|また|さらに|加えて)[、，,]\s*"
)


# 番号で一覧の項目を指す語（「手順 2 の後に」）。本文に残っていれば、項目を外しても番号を振り
# 直さない（指す先が変わるため。#2692）。
_STEP_REFERENCE = re.compile(r"(?:手順|ステップ|step)\s*[0-9０-９]{1,3}", re.IGNORECASE)
_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _with_number(line: str, number: int) -> str:
    """番号つきの項目の行（`_STEP_ITEM`）の番号を `number` にした行（番号の字の種類は保つ）。"""
    match = _STEP_ITEM.match(line)
    if match is None:
        return line
    name = next(key for key in ("dot", "paren", "circled") if match.group(key))
    old = match.group(name)
    if name == "circled":
        new = chr(ord("①") + number - 1) if 1 <= number <= 20 else old
    else:
        new = str(number)
        if any("０" <= char <= "９" for char in old):
            new = new.translate(_FULLWIDTH_DIGITS)
    return line[: match.start(name)] + new + line[match.end(name) :]


def _renumber(original: list[str], kept: dict[int, str]) -> None:
    """外した項目のある番号つきの一覧の、残った項目の番号を詰める（#2692）。

    `kept` は元の行の番号 → 残った行（書き換える）。

    項目の行が消えたか、番号の印ごと外れた（行の初めの段落を外した）項目を外した項目とする。同じ
    番号の繰り返しは保つ。本文に番号で項目を指す語（`_STEP_REFERENCE`）があれば振り直さない。
    """
    if any(_STEP_REFERENCE.search(line) for line in kept.values()):
        return
    for items in _step_ranges(original):
        numbers: list[tuple[int, int, bool]] = []
        for start, _end in items:
            item = _step_item(original[start])
            now = _step_item(kept[start]) if start in kept else None
            if item is not None:
                numbers.append((start, item[2], now is not None and now[:2] == item[:2]))
        if all(survived for _s, _n, survived in numbers):
            continue
        removed = {number for _s, number, survived in numbers if not survived} - {
            number for _s, number, survived in numbers if survived
        }
        for start, number, survived in numbers:
            shift = sum(1 for item in removed if item < number)
            if survived and shift:
                kept[start] = _with_number(kept[start], number - shift)


def withhold_paragraphs(
    answer: str, quotes: set[str], positions: frozenset[tuple[int, int]] = frozenset()
) -> str | None:
    """回答から `quotes` の段落を外した本文（位置を決められない段落があれば None）。

    `positions` は、原文ではなく位置（行の番号・行の中の開始の位置）で外す段落（`dependent_steps`。
    同じ原文の段落が手順の外にあっても、手順の中のものだけを外す。#2604。`withheld_labels` の
    項目の名前も位置で外す。#2692）。

    段落の位置は RAG と同じ規則で分けて決める（文字列の置換では、同じ文を含む別の段落まで
    削ってしまうため）。段落が無くなった行は消し、続く空行は 1 つにする。段落を外した行に見出し・
    出典だけが残ればその行も消し、中身をすべて外した節の見出しも消す（#1306。番号つきの見出しも
    消す: 中身の無い操作の名前を実行の順の 1 つとして残さない。#2692）。番号つきの一覧の項目を
    外したら、残った項目の番号を詰める（`_renumber`。#1898 の番号の飛びを残さない）。見出し・出典の
    ほかに何も残らなければ空文字を返す（本文を載せない）。外した段落の直後（見出し・出典を挟んでも）の
    段落が前の段落を受ける接続の語（「したがって、」など）で始まれば、その語を外す（#1833）。
    """
    found: set[str] = set()
    headers = table_header_lines(answer)
    # 次の行で決まる番号つき・箇条書きの見出しの行（#1481 / #1486）。
    listed = list_heading_lines(answer)

    def structure(text: str) -> bool:
        return is_structure(text) or text in headers or text in listed

    # （元の行の番号, 行, 見出しの行か, 中身のある行か）。消した見出しは None にする。
    kept: list[tuple[int, str, bool, bool] | None] = []
    heading: int | None = None
    section_content = False
    section_removed = False
    # 直前の（主張の）段落を外したか（次の段落の先頭の接続の語を外す。#1833）。
    dangling = False

    def close_section() -> None:
        if heading is not None and section_removed and not section_content:
            kept[heading] = None

    original = answer.split("\n")
    for index, line in enumerate(original):
        spans = passage_spans(line)

        def removed(span: tuple[int, int, str], index: int = index) -> bool:
            return span[2] in quotes or (index, span[0]) in positions

        withheld = [span for span in spans if removed(span)]
        if not withheld and len(spans) == 1 and (is_heading(spans[0][2]) or spans[0][2] in listed):
            close_section()
            heading, section_content, section_removed = len(kept), False, False
            kept.append((index, line, True, False))
            continue
        # 行の中の消す範囲（外す段落と、外した段落の直後の段落の先頭の接続の語）。
        cuts: list[tuple[int, int]] = []
        for start, end, text in spans:
            if removed((start, end, text)):
                cuts.append((start, end))
                dangling = True
            elif not structure(text):
                connective = _DANGLING_CONNECTIVE.match(text) if dangling else None
                if connective and connective.end() < len(text):
                    cuts.append((start, start + connective.end()))
                dangling = False
        for start, end in reversed(cuts):
            line = line[:start] + line[end:]
        if not withheld:
            content = any(not structure(text) for _s, _e, text in spans)
            section_content = section_content or content
            kept.append((index, line, False, content))
            continue
        found.update(span[2] for span in withheld)
        section_removed = True
        rest = [text for _s, _e, text in passage_spans(line)]
        if line.strip() and any(not structure(text) for text in rest):
            section_content = True
            kept.append((index, line.rstrip(), False, True))
    close_section()
    if quotes - found:
        return None
    lines = [item for item in kept if item is not None]
    if not any(content for _index, _line, _heading, content in lines):
        return ""
    texts = {index: line for index, line, _h, _c in lines}
    _renumber(original, texts)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(texts[index] for index, *_ in lines)).strip()


# 番号つきの一覧の項目の行（「1. 」「2）」「（3）」「④」。字下げ・強調の印を許す。#2604）。
# 「1.5 倍」のような数は項目にしないため、半角の「.」の後は空白を求める。
_STEP_ITEM = re.compile(
    r"(?P<indent>[ \t\u3000]*)(?:\*\*|__)?"
    r"(?:(?P<dot>[0-9０-９]{1,3})(?:\.\s|[)．）]\s*)|[（(](?P<paren>[0-9０-９]{1,3})[）)]\s*"
    r"|(?P<circled>[①-⑳])\s*)\S"
)
# 番号つきの一覧を切る行（Markdown の見出し）。
_SECTION_BREAK = re.compile(r"\s*#{1,6}\s")


@dataclass
class _StepRun:
    """番号つきの一覧の 1 つの並び（外側の一覧。字下げの深い番号は項目の中の行）。"""

    indent: int
    style: str
    last: int
    start: int
    # 最後の項目の行と、その項目が見出しの行（下の行に中身がある形）か。
    last_item: int
    heading_item: bool
    # 項目の行（並びの中の各項目の最初の行）。
    items: list[int]


def _step_item(line: str) -> tuple[int, str, int] | None:
    """番号つきの項目の行なら（字下げの幅・番号の種類・番号）。"""
    match = _STEP_ITEM.match(line)
    if not match:
        return None
    indent = len(match.group("indent").expandtabs(4))
    if match.group("circled"):
        return indent, "circled", ord(match.group("circled")) - ord("①") + 1
    style = "dot" if match.group("dot") else "paren"
    return indent, style, int(match.group("dot") or match.group("paren"))


def _step_ranges(lines: list[str]) -> list[list[tuple[int, int]]]:
    """回答の行のうち、外側の番号つきの一覧ごとの、項目の範囲（[開始の行, 終わりの行)）の並び。

    番号が 1 つずつ増える（同じ番号の繰り返しも許す）同じ字下げ・同じ種類の項目を 1 つの並びにし、
    項目と項目の間の行は前の項目の中身にする。最後の項目の中身は、字下げした行と、空行を挟まずに
    続く行（項目が見出しの行なら、次の見出しの行まで）。Markdown の見出しで並びを切る。
    """
    ranges: list[list[tuple[int, int]]] = []
    run: _StepRun | None = None

    def close(at: int) -> None:
        nonlocal run
        if run is None:
            return
        end = run.last_item + 1
        blank = False
        while end < at:
            line = lines[end]
            if not line.strip():
                blank = True
            elif run.heading_item:
                spans = passage_spans(line)
                if len(spans) == 1 and is_heading(spans[0][2]):
                    break
            elif blank and len(line) - len(line.lstrip()) <= run.indent:
                break
            end += 1
        while end > run.start and not lines[end - 1].strip():
            end -= 1
        bounds = [*run.items[1:], end]
        ranges.append(list(zip(run.items, bounds, strict=True)))
        run = None

    def heading_item(line: str) -> bool:
        spans = passage_spans(line)
        return len(spans) == 1 and (is_heading(spans[0][2]) or spans[0][2] in listed)

    listed = list_heading_lines("\n".join(lines))
    for index, line in enumerate(lines):
        if _SECTION_BREAK.match(line):
            close(index)
            continue
        item = _step_item(line)
        if item is None:
            continue
        indent, style, number = item
        if run is not None and indent > run.indent:
            continue
        if (
            run is not None
            and indent == run.indent
            and style == run.style
            and number in {run.last, run.last + 1}
        ):
            run.last, run.last_item, run.heading_item = number, index, heading_item(line)
            run.items.append(index)
            continue
        close(index)
        run = _StepRun(indent, style, number, index, index, heading_item(line), [index])
    close(len(lines))
    return ranges


def dependent_steps(answer: str, quotes: set[str]) -> list[tuple[int, int, str]]:
    """外す段落（`quotes`）に続く、番号つきの手順の段落（行の番号・行の中の開始の位置・原文。#2604）。

    番号つきの一覧で前の項目（手順の前提の確認など）の段落を外したら、同じ一覧のそれより後の、操作の
    項目（項目の行か下の行に、利用者に操作・作業を行わせる段落がある項目。`is_operation_step`）の段落は、
    確かめられない前提に続く操作になるので、実行できる手順として単独で残さない。その項目の番号の行
    （「2. **訂正**」のような見出しの形でも）も外す。事実を述べる項目（操作の段落の無い項目）は、
    番号つきでも前の項目に依存しないので残す（番号で事実を並べた一覧の 1 項目を外しても、後の事実は
    残る）。一覧の外の段落（前の説明・一覧の後の段落）、一覧の中の外した段落より前の段落、主張では
    ない段落（出典の行・利用者への質問・不足の文など。`non_claim_passages`）は残す。番号の無い
    箇条書きは順序を表さないので対象にしない。`quotes` に入っている段落は返さない。
    """
    lines = answer.split("\n")
    kinds = non_claim_passages(answer)
    structure = table_header_lines(answer) | list_heading_lines(answer)
    dependents: list[tuple[int, int, str]] = []
    for items in _step_ranges(lines):
        withheld = False
        for start, end in items:
            spans = [
                (index, begin, text)
                for index in range(start, end)
                for begin, _end, text in passage_spans(lines[index])
            ]
            operation = any(is_operation_step(text) for _index, _begin, text in spans)
            for index, begin, text in spans:
                if text in quotes:
                    withheld = True
                elif (
                    withheld
                    and operation
                    and (
                        _step_item(lines[index]) is not None
                        or not (kinds.get(text) or is_structure(text) or text.strip() in structure)
                    )
                ):
                    dependents.append((index, begin, text))
    return dependents


def withheld_labels(answer: str, quotes: set[str]) -> list[tuple[int, int, str]]:
    """外す操作の段落（`quotes`）と同じ操作を名前だけで示す、番号つきの一覧の項目（行の番号・行の
    中の開始の位置・原文。#2692）。

    「1. **対象 A の属性訂正**」のような名前だけの項目（下に中身の無い項目。`list_item_label`）は
    主張として根拠と矛盾しなくても、実行の順の 1 つとして読める。外す段落が操作
    （`is_operation_step`）で、その操作を名前が指すとき（`label_operation_end`）、項目も外す。
    1 つの段落を複数の名前が指すときは、段落の文末の述語に近い名前（主の操作）だけを外す。
    下に中身のある見出しの項目は、中身をすべて外したときに `withhold_paragraphs` が消す（中身の
    正しい内容は残す）。章題・説明の名前（段落と語の重ならない名前）は外さない。
    """
    operations = [quote for quote in quotes if is_operation_step(quote)]
    if not operations:
        return []
    lines = answer.split("\n")
    labels: list[tuple[int, int, str, str]] = []
    for items in _step_ranges(lines):
        for start, end in items:
            spans = passage_spans(lines[start])
            if len(spans) != 1 or any(lines[index].strip() for index in range(start + 1, end)):
                continue
            begin, _end, text = spans[0]
            label = list_item_label(text)
            if label is not None and text not in quotes:
                labels.append((start, begin, text, label))
    chosen: dict[tuple[int, int], str] = {}
    for quote in operations:
        ends = [
            (position, item)
            for item in labels
            if (position := label_operation_end(item[3], quote)) is not None
        ]
        if ends:
            last = max(position for position, _item in ends)
            chosen.update(
                {(item[0], item[1]): item[2] for position, item in ends if position == last}
            )
    return [(index, begin, text) for (index, begin), text in sorted(chosen.items())]


def without_lookup_notes(answer: str) -> str:
    """回答から、調べるのに使ったツール・プロファイル・内部の ID だけを述べる行を外す（#2328）。

    最終の検証の前（と、検証をしない設定でも）に組み込み Runtime が呼ぶ。資料の主張では
    ないので検証に渡さず、「資料で確認できず除いた内容」にも書かない（内部の ID を利用者に
    見せない）。外すと本文が残らない・位置を決められないときはそのまま返す。
    """
    quotes = {
        text
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
        if is_lookup_note(text)
    }
    if not quotes:
        return answer
    body = withhold_paragraphs(answer, quotes)
    return body or answer


def error_findings(result: JsonObject) -> list[JsonObject]:
    """決定的な検査の error の指摘（valid にしない。warning は確かめたい点なので出さない）。"""
    return [
        item
        for item in result.get("findings") or []
        if isinstance(item, dict) and item.get("severity") == "error"
    ]


def _notice_lines(result: JsonObject, claims: list[JsonObject]) -> list[str]:
    """節の項目（すべての段落・指摘を同じ書式の 1 行ずつ。「ほか N 件」で切らない。#2361）。"""
    lines = [_claim_line(item) for item in claims]
    # RAG の決定的な検査（要求の漏れ・手順の順序と分岐・影響範囲。#1276）の error。
    findings = [item for item in error_findings(result) if item.get("message")]
    lines += [f"- {_short(item['message'], _MAX_FINDING_CHARS)}" for item in findings]
    stale = _refs_count(result.get("stale_evidence"))
    if stale:
        lines.append(f"- 根拠の {stale} 件は文書の古い版です。最新の版で確かめ直してください。")
    missing = _refs_count(result.get("missing_evidence"))
    if missing:
        lines.append(f"- 根拠の {missing} 件は見つかりません（削除された・参照できない）。")
    if not lines:
        lines.append("- 根拠で裏付けられた主張がありません。")
    return lines


def _listed(lines: list[str]) -> JsonObject:
    """「資料で確認できず除いた内容」の節の記録（#2328・#2361）。

    除いた内容の一覧は節の 1 か所だけに出す。画面の「資料との照合」の件数のバッジは、節の項目の数
    （`items`。箇条書きの行）を使い、節と必ず一致させる。
    """
    return {
        "items": sum(1 for line in lines if line.startswith("- ")),
        # 節の見出し（画面が回答の本文と節を分ける）。
        "heading": UNVERIFIED_HEADING,
    }


def mark_non_claims(answer: str, result: JsonObject) -> JsonObject:
    """判定の段落のうち、主張ではない段落（見出し・出典・表の形・質問・不足の文・「資料で確認できず除いた内容」の節）に
    `non_claim`（判定の種類）を付けた結果の写しを返す（#1306。`answer_passages.non_claim_passages`）。

    RAG の判定（`status`）は変えずに残し、外すかどうかの判断と画面の「資料で確認できず除いた内容」
    だけが
    `non_claim` の段落を除く。利用者に現場のデータ・記録の確認を求める段落には、判定とは別に
    `environment_check: true` を付ける（#1317。主張のまま確かめ、回答の対応だけが使う）。
    """
    kinds = non_claim_passages(answer)
    checks = environment_check_passages(answer)
    claims: list[object] = []
    for item in result.get("claims") or []:
        if isinstance(item, dict):
            quote = str(item.get("answer_quote") or "")
            item = {
                key: value
                for key, value in item.items()
                if key not in {"non_claim", "environment_check"}
            }
            if kinds.get(quote):
                item["non_claim"] = kinds[quote]
            if quote in checks:
                item["environment_check"] = True
        claims.append(item)
    return {**result, "claims": claims}


def _dependent_lines(answer: str, texts: list[str]) -> list[str]:
    """外す段落に続く手順のうち、主張の段落の節の項目（見出し・出典・表の形などは本文から外すだけ）。"""
    if not texts:
        return []
    kinds = non_claim_passages(answer)
    headers = table_header_lines(answer)
    listed = list_heading_lines(answer)
    quotes = [
        text
        for text in dict.fromkeys(texts)
        if not kinds.get(text) and not is_structure(text) and text not in headers | listed
    ]
    return [f"- {DEPENDENT_STEP_LABEL}: 「{_short(text, _MAX_QUOTE_CHARS)}」" for text in quotes]


def _dependent_count(lines: list[str]) -> JsonObject:
    """記録の、外す段落に続く手順の段落の数（無ければ項目を足さない）。"""
    return {"dependent_steps": len(lines)} if lines else {}


def _publish(answer: str, result: JsonObject) -> tuple[str, JsonObject]:
    """判定（判定として使える結果）から、利用者に見せる回答と、外した内容の記録を返す。

    valid（または確かめる主張も error の指摘も無い）ならそのまま。主張ではない段落（見出し・
    出典の行・利用者への質問・資料に記載が無いことを述べる文・「資料で確認できず除いた内容」の節。
    #1306）は判定にかかわらず外さず、ほかに確かめられない段落・error・読めない根拠が無ければ（裏付けのある主張が
    あるか、主張ではない段落だけなら）回答をそのまま載せる。そうでなければ、根拠で確かめられ
    なかった段落を外し、外した段落と理由・決定的な検査の error・古い版や見つからない根拠を
    「資料で確認できず除いた内容」として足す。根拠を 1 件も読めなかった（`no_evidence`）とき、
    段落の位置を
    決められないとき、手順・影響範囲・業務ガイドの版の error があるとき（どの段落の手順が誤りかを
    決められず、確かめていない手順を出さない）は本文を載せない。要求の error（答えていない要求を
    示していない）は本文を残し、不足として示す。根拠で否定された段落（`unsupported` /
    `contradicted`）が無く、出典を照合できない（`citation_error`）・確かめが終わらない
    （`unassessed`）段落を外すと本文が空になるときは、本文を残して確かめきれなかった点を示す
    （記録の `withheld.unverified`。#1391）。否定の判定は、RAG が出典を照合できずに判定を
    変えた段落の元の判定（`audit_status`）も含めて数える（`is_refuted_claim`。#2622）。
    外した段落が番号つきの一覧の中にあれば、同じ一覧のそれより後の操作の項目も、確かめられない前提に
    続く操作として外し、節に「確かめられない手順に続く手順」として示す（`dependent_steps`。#2604。
    事実を述べる項目は残す。本文を残すときも示す）。記録の `dependent_steps` はその段落の数
    （無ければ項目を足さない）。
    """
    errors = error_findings(result)
    unchanged = {"claims": 0, "findings": 0, "all": False}
    if result.get("valid") is True or (result.get("status") == "no_claims" and not errors):
        return answer, unchanged
    claims = [item for item in mark_non_claims(answer, result)["claims"] if isinstance(item, dict)]
    blocking = [
        item
        for item in claims
        if item.get("status") in BLOCKING_CLAIM_STATUSES and not item.get("non_claim")
    ]
    unreadable = _refs_count(result.get("stale_evidence")) + _refs_count(
        result.get("missing_evidence")
    )
    if (
        result.get("status") == "completed"
        and not errors
        and not blocking
        and not unreadable
        and any(item.get("status") == "supported" or item.get("non_claim") for item in claims)
    ):
        return answer, unchanged
    guide_errors = any(item.get("check") in WITHHOLDING_CHECKS for item in errors)
    body: str | None
    # 外す段落に続く番号つきの手順（#2604）と、その中で節に書く段落（主張の段落）。
    dependents: list[tuple[int, int, str]] = []
    if result.get("status") == "no_evidence" or guide_errors:
        body = None
    elif blocking:
        quotes = {str(item.get("answer_quote") or "") for item in blocking}
        # 外す操作を名前だけで示す番号つきの項目（#2692）。外した項目として、続く手順にも数える。
        labels = withheld_labels(answer, quotes)
        dependents = dependent_steps(answer, quotes | {text for _i, _b, text in labels})
        body = withhold_paragraphs(
            answer,
            quotes,
            frozenset((index, begin) for index, begin, _text in [*dependents, *labels]),
        )
    else:
        body = answer.rstrip()
    dependent_lines = _dependent_lines(answer, [text for _i, _b, text in dependents])
    if (
        body == ""
        and blocking
        and not errors
        and not unreadable
        and not any(is_refuted_claim(item) for item in blocking)
    ):
        # 根拠で否定された段落が無いのに、出典を照合できない（`citation_error`）・確かめが終わらない
        # （`unassessed`）段落を外すと本文が空になる: 検証が確かめきれなかったので、本文は消さずに
        # 確かめきれなかった点を示す（検証の失敗で内容を落とさないのと同じ。#1391）。
        # 確かめきれなかった前提に続く手順も、本文に残したうえで、前提に続く手順だと示す（#2604）。
        lines = [UNVERIFIED_KEPT_INTRO, *_notice_lines(result, blocking), *dependent_lines]
        withheld = {
            "claims": 0,
            "findings": 0,
            "all": False,
            "unverified": len(blocking),
            **_dependent_count(dependent_lines),
            "listed": _listed(lines),
        }
        return f"{answer.rstrip()}\n\n**{UNVERIFIED_HEADING}**\n\n" + "\n".join(lines), withheld
    lines = [*_notice_lines(result, blocking), *dependent_lines]
    if blocking or (not body and not guide_errors):
        lines.insert(0, WITHHELD_INTRO)
    text = body or (GUIDE_WITHHELD if guide_errors else NOTHING_CONFIRMED)
    withheld = {
        "claims": len(blocking),
        "findings": len(errors),
        "all": not body,
        **_dependent_count(dependent_lines),
        "listed": _listed(lines),
    }
    return f"{text}\n\n**{UNVERIFIED_HEADING}**\n\n" + "\n".join(lines), withheld


def publish_answer(answer: str, result: JsonObject) -> tuple[str, JsonObject]:
    """`_publish` の、利用者に見せる回答と外した内容の記録（節に書いた項目の数を除く）。"""
    published, withheld = _publish(answer, result)
    withheld.pop("listed", None)
    return published, withheld


def combine_validations(answer: str, validations: list[JsonObject]) -> tuple[JsonObject, str]:
    """接続ごとの内容（`validation_content`）を 1 つにし、利用者に見せる回答を返す（#1277）。

    - どれかの接続を確かめられなかった（`unvalidated`）: 全体も `unvalidated` で、回答は消さずに
      照合を終えられなかったこと（`UNVERIFIED_NOTICE`）を足す（基盤の障害で内容を落とさない）。
    - すべての接続で判定が出た: 段落ごとにまとめた判定で回答を組み立てる（`publish_answer`）。
    """
    single = validations[0] if len(validations) == 1 else None
    common: JsonObject = {
        "connection": single["connection"] if single else None,
        "tool_name": single["tool_name"] if single else None,
        "step_id": single["step_id"] if single else None,
        "evidence": [ref for item in validations for ref in item["evidence"]],
    }
    failed = next((item for item in validations if item["status"] != STATUS_COMPLETED), None)
    if failed is not None:
        content = validation_content(
            STATUS_UNVALIDATED,
            reason=failed["reason"],
            message=failed["message"],
            result=single["result"] if single else None,
            **common,
        )
        published = with_unverified_notice(answer)
    else:
        merged = mark_non_claims(answer, merge_results([item["result"] for item in validations]))
        content = validation_content(STATUS_COMPLETED, result=merged, **common)
        published, withheld = _publish(answer, merged)
        listed = withheld.pop("listed", None)
        content["withheld"] = withheld
        if listed is not None:
            # 回答の本文の「資料で確認できず除いた内容」の節の見出しと項目の数
            # （#2328・#2361。画面の「資料との照合」は一覧を出し直さず、件数だけを出す）。
            content["notice"] = listed
    content["connections"] = validations
    return content, published


def with_unverified_notice(answer: str) -> str:
    return f"{answer.rstrip()}\n\n{UNVERIFIED_NOTICE}"


def with_no_evidence_notice(answer: str) -> str:
    """資料の根拠を使わずに作った回答の末尾に、確かめていないことを足す（#1277）。"""
    return f"{answer.rstrip()}\n\n{NO_EVIDENCE_NOTICE}"
