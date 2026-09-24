"""原質問の明示的な目的と条件を固定し、検索語の意味変化を記録する。"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
import unicodedata
from typing import Any, Sequence
from docrag.retrieval.definition_evidence import definition_labels



# 質問や item が名指しする業務上の対象（画面・帳票・文書）。接尾辞は文書の一般的な種別語だけで、業務固有の名称は含めない。
OBJECT_SUFFIX = r"(?:画面|台帳|名簿|帳票|情報|サービス|一覧表|集計表|一覧|リスト|[一-龯]{1,3}(?:書|表|票|証|券))"
OBJECT_PATTERN = re.compile(rf"[一-龯々ァ-ヶーA-Za-z0-9]{{1,20}}{OBJECT_SUFFIX}")
# 「〈画面A〉に〈対象B〉がある」の B。利用者が B を見た場所が A で、尋ねているのは B の方なので、
# B を対象の先頭に置く。B は「補足欄」のように種別語で終わらないことが多く OBJECT_PATTERN では
# 拾えず、検索語（`target_text_queries`）に 1 つも入らないまま A の資料だけが検索されていた (#1068)。
_EXISTENTIAL_PATTERN = re.compile(
    r"(?:には|に)([一-龯々ァ-ヶーA-Za-z0-9]{2,12})(?:が|も)(?:ある|あり|ござ|表示|出て|入っ)"
)

# 問い合わせ元を表す一般語。自治体・業種固有の語（都道府県・県・市など）はコードに置かず profile の requester_terms で足す (#849)。
_DEFAULT_REQUESTER_TERMS = ("顧客", "利用者", "担当者", "取引先")


def _requester_terms_pattern() -> str:
    from docrag.resources.runtime import current_profile
    terms = [*_DEFAULT_REQUESTER_TERMS, *current_profile().requester_terms]
    return "|".join(re.escape(term) for term in dict.fromkeys(term for term in terms if term))

def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def current_request_text(question: str) -> str:
    """明示された過去の操作経験に続く今回の要求を返す。原質問は別途保持する。

    「以前…が、今回…」のように境界がある場合だけ補助的に絞る。
    後半に記された方法制約は保持し、不明な時制や対象は推測しない。
    """
    match = re.match(r'\s*(?:以前|過去に|前回).+?(?:が[、,]|[。\n])\s*(.+)', question, re.S)
    return match.group(1).strip() if match else question


def changed_field_conditions(text: str) -> list[str]:
    """明示された「項目が変更になった／項目変更時」の項目名だけを返す。

    文境界直後の名詞に限定し、曖昧な言い換えや単なる変更希望は推測しない。
    """
    pattern = r'(?:^|[、。\s「『（(])([一-龯々ァ-ヶーA-Za-z0-9_]{2,32})(?:が|の)?変更(?:時|の場合|にな|された|$)'
    return list(dict.fromkeys(re.findall(pattern, unicodedata.normalize('NFKC', text))))


def question_intent_parts(question: str) -> dict[str, list[str]]:
    """明示的な希望・試行・観測・仮説を原文の節として返す。

    検索補助用の保守的な抽出であり、推測した解決策や省略された対象を補わない。
    分類は排他的ではなく、検出できない節も原質問側に保持する。
    """
    clauses = [s.strip() for s in re.split(
        r'[。、，,！？?\n]|(?<=(?:した|った|いた|たい))が', question
    ) if s.strip()]
    hypothesis = r'と思|ではないか|かもしれ|可能性|しかない'
    wishes = r'(?:したい|したかった|させたい|させたかった|変えたい|増やしたい|減らしたい)'
    # 同一文で撤回された希望を、接続助詞の分割で復活させない。
    withdrawn = [s for s in re.split(r'[。！？?\n]', question)
                 if re.search(r'不要|必要ない|やめた|諦め', s)]
    return {
        'desired_outcomes': [s for s in clauses if re.search(wishes, s)
                             and not re.search(hypothesis, s)
                             and not any(s in sentence for sentence in withdrawn)],
        # 「取消済み伝票」のように名詞を修飾する「〜済」はデータの状態であり、利用者の試行ではない。
        # 文末や助詞が続く「取消済みです」だけを試行操作とする。
        'tried_actions': [s for s in clauses if re.search(
            r'試した|行った|おこなった|押した|押下した|取り消した|(?:取消|実行)済み?(?![一-龯々ァ-ヶーみ])', s
        ) and not re.search(r'場合|なら|とき', s)],
        'observed_states': [s for s in clauses if re.search(
            r'[一-龯々ァ-ヶー]{2,}され(?:て(?:いた|いる|います)|ており)|[＊*]がある|有効(?:な|で|です)|残っていた|存在しない|一致しない|変わらなかった|増えなかった|できなかった|登録済', s
        ) and not re.search(hypothesis + r'|場合|なら|とき|かどうか', s)],
        'hypothesized_means': [s for s in clauses if re.search(hypothesis, s)],
    }


def goal_retrieval_queries(question: str) -> tuple[str, ...]:
    """経緯や仮説が混在する場合、明示目的を最大2件の補助検索文として返す。

    原質問を置き換えず、固有の業務名・操作名・値は追加しない。
    """
    parts = question_intent_parts(question)
    if not any(parts[key] for key in ('tried_actions', 'observed_states', 'hypothesized_means')):
        return ()
    return tuple(dict.fromkeys(parts['desired_outcomes']))[:2]


def task_contract(question: str) -> dict[str, Any]:
    """質問契約を返す。解析は質問ごとに1回だけ行い、呼出側が書き換えても共有されない写しを返す。"""
    return copy.deepcopy(_task_contract(question))


@lru_cache(maxsize=256)
def _task_contract(question: str) -> dict[str, Any]:
    """外部I/Oなしで明示的な目的・形式・条件・仮説を抽出する。

    日本語の代表的な目的を認識する補助規則であり、未知表現の意味理解を保証しない。
    原文も保持し、検出できなかった値を推測で補わない。
    """
    current = current_request_text(question)
    text = _text(current)
    clauses = [p.strip() for p in re.split(r"[。\n]", question) if p.strip()]
    recipient_list = bool(re.search(r"対象者.*(?:確認|出力|抽出|一覧)|(?:名簿|送付先|対象者リスト)|一覧.{0,8}(?:出力|抽出)", text))
    missing_choice = bool(re.search(r"(?:該当|選択肢|候補).*(?:ない|無い|なし|無く)", text))
    requester = re.findall(r"(?:" + _requester_terms_pattern() + r")(?:側|方)?(?:から|より|で).{0,25}?(?:依頼|要求|希望|要望|確認|問い合わせ|照会|連絡|相談)", question)
    wants_count = bool(re.search(r"何人|何件|人数を(?:知|確認)|件数を(?:知|確認)|数を教", text))
    wants_members = bool(re.search(r'誰が|どの(?:人|対象者)|(?:計上|集計)されている(?:対象者|明細)|構成明細|取引明細|個人(?:別|明細)', text))
    wants_basis = bool(re.search(r'算定根拠|計算根拠|集計条件|計算方法', text))
    recipient_list = recipient_list or wants_members
    # 「変更履歴」のような説明要求と、値を変えたい操作要求を区別する。
    change_request = bool(re.search(r"(?:変更|修正|訂正|編集|追加|削除|延長|取消)(?:し|する|でき|の方法|の手順)|(?:変え|直し|直す|置き換え)たい|表示させたい", text))
    definitions = definition_targets(question)
    intent_parts = question_intent_parts(question)
    # 明示目的の操作を先に記録する。経緯中の「登録」だけを要求操作とすると、
    # 追加・削除等の本来の目的が契約から脱落する。原質問の他の要求も保持する。
    action_text = '\n'.join([*intent_parts['desired_outcomes'], current])
    goal = "rule" if definitions and not change_request and not re.search(r'方法|手順|どうしたら', text) else "recipient_list" if recipient_list and not wants_count else "count" if wants_count else "procedure" if change_request or re.search(r"方法|手順|どうしたら|できない|登録|入力|印字", text) else "rule"
    return {
        "original_question": question,
        "current_request": current,
        "historical_context": question[:question.rfind(current)].strip() if current != question else "",
        "intent_parts": intent_parts,
        "request_units": request_units(question),
        "definition_targets": definitions,
        "goal": goal,
        "asks_cause": asks_cause(question),
        "requested_granularity": [kind for kind, wanted in [('count', wants_count), ('members', wants_members), ('calculation_basis', wants_basis)] if wanted],
        # 存在構文の対象を先に置く。`target_text_queries` は上限 6 件で、画面名の部分語だけで
        # 埋まると質問の対象が検索語に入らない (#1068)。
        "business_objects": list(dict.fromkeys(
            [*_EXISTENTIAL_PATTERN.findall(question), *OBJECT_PATTERN.findall(question)]
        )),
        "error_messages": error_messages(question),
        "action_targets": action_targets(question),
        # 「取消済み」「登録済み」の操作語は状態の一部で、要求された操作ではない（直後の「済」で除く）。
        "requested_actions": list(dict.fromkeys(re.findall(r"(?:確認|出力|抽出|入力|登録|更新|印字|切替|変更|修正|訂正|編集|追加|削除|延長|取消)(?!済)", action_text))),
        "user_assertions": [c for c in clauses if not re.search(r"と思|ではないか|かもしれ|可能性", c)],
        "requester_mentions": requester,
        "output_formats": [v for v in ("csv", "excel", "pdf") if v in text],
        "changed_fields": changed_field_conditions(current),
        "explicit_conditions": [c for c in clauses if re.search(r"関係なく|含む|除外|場合|のみ|以外|期限|され(?:て(?:いる|いた)|ており)", c)],
        "hypotheses": [c for c in clauses if re.search(r"と思|ではないか|かもしれ|可能性", c)],
        "missing_choice": missing_choice,
        "required_aspects": ["business_object", "requested_result", "applicability"] + (["procedure"] if goal in {"procedure", "recipient_list"} or missing_choice else [])
            + (["member_details", "population_membership"] if wants_members and re.search(r'集計|計上|算入', text) else []),
    }


# 原因・理由を尋ねる標識。「どうしてか」「なぜ」「原因は」「理由は」。
_CAUSE_MARKER = re.compile(r"どうして|なぜ|何故|原因|理由|どういう(?:場合|とき|時)")


def asks_cause(question: str) -> bool:
    """今回の要求文が原因・理由を尋ねているか (#713)。原因の推測を summary で列挙させないための材料。"""
    return bool(_CAUSE_MARKER.search(current_request_text(question)))


# 総称語だけの操作対象。「データを削除」「情報を変更」は資料の具体名と照合できないので対象にしない (#696, #700)。
GENERIC_TARGET_WORDS = frozenset({"データ", "情報", "内容", "項目", "対象", "もの", "これ", "それ", "レコード", "行", "件", "分"})
# 要求文の操作対象「X を〔一括で〕<操作語>(する|したい|できる|方法|手順|は)」の X。「請求書を発行したところ」のような
# 経緯の過去形は受けない。X は数字で始まらない 2〜20 文字の名詞句（「1枠を変更」は対象にしない）。
_ACTION_TARGET = re.compile(
    r"([一-龯々ァ-ヶーA-Za-z][一-龯々ァ-ヶーA-Za-z0-9]{1,19})を(?:一括で|個別に|一括|個別)?"
    r"(?:追加|登録|変更|修正|訂正|削除|消去|出力|設定|入力|再発行|発行|印刷|付与|取込|取り込|更新|取消|切替)"
    r"(?:する|し(?:たい|ます)|でき|の方法|の手順|方法|手順|は)")


def action_targets(question: str) -> list[str]:
    """質問が要求する操作の対象（「社印を変更するには」の「社印」）を原文の語で返す。

    種別語（画面・帳票…）で終わらない対象も拾い、根拠のどこにもない対象への強行回答（別対象の手順の適用）を
    検査する材料にする (#700)。総称語だけの対象は返さない。対象の意味や同義語は推測しない。
    """
    text = unicodedata.normalize("NFKC", question)
    return [t for t in dict.fromkeys(_ACTION_TARGET.findall(text)) if t not in GENERIC_TARGET_WORDS]


# 質問が示すエラー文。「…」というエラー / 「…」のエラー のように引用された文と、「…のエラーが出ている」の「…」。
_ERROR_QUOTED = re.compile(r"[「『]([^」』\n]{3,80})[」』]")
_ERROR_PHRASE = re.compile(r"([^。「」『』\n]{4,80}?)(?:の|という|と|といった)エラー")
# 「…で」「…、」の区切り。「では」「でも」「できない」の「で」は区切りにしない。
_ERROR_CLAUSE_SPLIT = re.compile(r"で(?![はもき])|、")


def error_messages(question: str) -> list[str]:
    """質問が示すエラー文（引用された文、または「…のエラー」の「…」）を返す (#686)。

    エラーを含む文だけを対象にし、文に引用（「」『』）があればそれを、なければ「…のエラー」の
    直前の句を採る。回答生成では、これが根拠のどこにもない場合に別のエラーの手順を適用扱いにしない。
    """
    found: list[str] = []
    for sentence in re.split(r"[。\n]", question):
        if "エラー" not in sentence:
            continue
        quoted = [q.strip() for q in _ERROR_QUOTED.findall(sentence) if len(q.strip()) >= 3]
        if quoted:
            found.extend(quoted)
            continue
        for phrase in _ERROR_PHRASE.findall(sentence):
            # 文頭からの句は対象名や文脈（「出荷検品エラーリストで定期出荷契約の」）を含むので、区切りの最後の句に絞る (#694)。
            clauses = [c.strip("①②③④⑤⑥⑦⑧⑨⑩ ") for c in _ERROR_CLAUSE_SPLIT.split(phrase)]
            clauses = [c for c in clauses if len(c) >= 4]
            if clauses:
                found.append(clauses[-1])
    return list(dict.fromkeys(v for v in found if v))


# 疑問・依頼・可否・方法を尋ねる標識。これを含まない文（「課名が変更になった。」「既に登録済み。」）は経緯・背景。
_REQUEST_MARKER = re.compile(r"か[。！？?\s]*$|[？?]|たい|ほしい|欲しい|教え|ください|下さい|どう|どこ|何|なぜ|いつ|でしょう|可能|でき|よい|良い|方法|手順|必要|お願い")


def _with_request_kind(units: list[dict[str, str]]) -> list[dict[str, str]]:
    """各文に kind=request / context を付ける。背景文は単位として残すが、答える対象ではない (#650)。

    要求の標識を持つ文が 1 つもない質問（「〇〇の件。」だけ等）は、全文を request として扱う。
    """
    kinds = ["request" if _REQUEST_MARKER.search(unit["text"]) else "context" for unit in units]
    if "request" not in kinds:
        kinds = ["request"] * len(units)
    return [{**unit, "kind": kind} for unit, kind in zip(units, kinds)]


def _sentence_units(question: str) -> list[dict[str, str]]:
    parts = [m.group().strip() for m in re.finditer(r"[^。！？?\n]+[。！？?\n]*", question)]
    return [{"id": f"Q{i}", "text": part} for i, part in enumerate(parts, 1) if part]


def definition_targets(question: str) -> list[dict[str, str]]:
    """原要求IDを維持して定義項目に子IDを付ける。記号は照合用にだけ正規化する。"""
    return [dict(id=f"{unit['id']}.D{n}", request_id=unit['id'], label=label,
                 marker='*' if '*' in unicodedata.normalize('NFKC', unit['text']) else '')
            for unit in _sentence_units(question)
            for n, label in enumerate(definition_labels(unit['text']), 1)]


_OPERATION_WORDS = r"追加|登録|変更|修正|訂正|削除|出力|設定|入力|再発行|発行|印刷"
# 「業務と種別を追加」「氏名、住所の変更」: 助詞を含まない 2 つの名詞句と、それに続く 1 つの操作語。
# 区切りは「と」のほか読点・「や」「及び」も受ける。実入力の並列対象は読点区切りが多く、「と」だけでは
# 一方の対象が子要求にならず黙って落ちた (#622 のデータ環境検証、#702)。名詞句は連続する漢字・カタカナ・英数字
# に限るので、「変更になった、ファイル名を変更」のように直前が述語の読点は一致しない。
_PARALLEL_OBJECTS = re.compile(r"([一-龯々ァ-ヶーA-Za-z0-9]{1,20})(?:と|、|や|及び|および)([一-龯々ァ-ヶーA-Za-z0-9]{1,20})(?:を|の)(" + _OPERATION_WORDS + r")")
# 「変更して再発行」「金額を変更して請求書を再発行」: 前の操作の完了を前提にした別の操作。間の対象語は 20 文字まで。
_CHAINED_OPERATIONS = re.compile(r"(" + _OPERATION_WORDS + r")(?:して|し、|した後に?|後に?)、?(?:[^、。]{0,20}?(?:を|の))?(" + _OPERATION_WORDS + r")")


def request_units(question: str) -> list[dict[str, str]]:
    """質問原文を句点・疑問符・改行で固定ID化する。背景文も捨てず判定側へ渡す。

    長い入力も切り捨てず、定義項目と経緯に埋もれた明示目的を子要求にする。
    原文単位のIDは維持し、既に独立した目的文は重複させない。
    """
    units = _with_request_kind(_sentence_units(question))
    goals = question_intent_parts(question)['desired_outcomes'] if goal_retrieval_queries(question) else []
    goal_units = [dict(id=f"{unit['id']}.G{n}", text=goal, kind="desired_outcome", request_id=unit['id'])
                  for unit in units for n, goal in enumerate(goals, 1)
                  if goal in unit['text'] and goal != unit['text'].strip('。！？? \n')]
    facets = []
    for unit in units:
        # 最終集計の構成員を尋ねる要求では、候補の閲覧と集計への採否を
        # 別々に検証する。集計値だけの質問へ明細要求を追加しない。
        if (re.search(r'集計|計上|算入', unit['text'])
                and re.search(r'対象者|誰|構成員|個人|明細|取引', unit['text'])):
            facets.extend([
                dict(id=f"{unit['id']}.M1", request_id=unit['id'], kind='explicit_facet',
                     text=f"原要求 {unit['id']} の構成明細の確認方法。集計値の出力だけでは充足しない。原文: {unit['text']}"),
                dict(id=f"{unit['id']}.M2", request_id=unit['id'], kind='explicit_facet',
                     text=f"原要求 {unit['id']} の明細候補と最終集計対象との対応。採用・除外条件が未確認なら一致を保証せず、その不足を明示する。原文: {unit['text']}"),
            ])
        # 「AとBを追加」のような同一操作の複数対象、「変更して再発行」のような操作の連結は、
        # 一文のままだと監査が一方だけで addressed にする。原文の語だけで対象・操作ごとの子要求にする (#622)。
        objects = _PARALLEL_OBJECTS.search(unit['text'])
        if objects:
            first, second, operation = objects.groups()
            facets.extend(dict(id=f"{unit['id']}.R{n}", request_id=unit['id'], kind='explicit_facet',
                               text=f"原要求 {unit['id']} の対象「{target}」の{operation}を個別に回答する。原文: {unit['text']}")
                          for n, target in enumerate((first, second), 1))
        chained = _CHAINED_OPERATIONS.search(unit['text'])
        if chained:
            facets.extend(dict(id=f"{unit['id']}.O{n}", request_id=unit['id'], kind='explicit_facet',
                               text=f"原要求 {unit['id']} の操作「{operation}」を個別に回答する。原文: {unit['text']}")
                          for n, operation in enumerate(chained.groups(), 1))
        # 一文内の明示的な複数論点だけを子要求化する。原文を保持し対象や効果を推測しない。
        found = [label for label, pattern in (
            ('操作方法', r'方法|手順'), ('影響', r'影響|副作用'),
            ('出力可否', r'出力でき|再発行でき|出力可能|再発行可能'),
            ('必要性', r'必要(?:か|ですか)|要否'),
        ) if re.search(pattern, unit['text'])
            and not re.search('(?:' + pattern + r')(?:の説明|の確認)?は(?:不要|必要ない)', unit['text'])]
        if len(found) > 1:
            facets.extend(dict(id=f"{unit['id']}.F{n}", request_id=unit['id'], kind='explicit_facet',
                text=f"原要求 {unit['id']} の「{label}」を個別に回答する。原文: {unit['text']}")
                for n, label in enumerate(found, 1))
    return units + goal_units + facets + [dict(id=t['id'], text=f"原要求 {t['request_id']} の項目「{t['label']}」{t['marker']}の意味と全表示条件")
                    for t in definition_targets(question)]


def query_rejection_reason(question: str, query: str, *, grounded_text: str = "") -> str:
    """目的の逸脱と未裏付けの業務IDを拒否する。grounded_textは検証済み別名のみ。

    日付や一文字の版名はIDと扱わず、一般的な言い換えは保持する。
    モデル自身が生成した説明をgrounded_textへ渡してはいけない。
    """
    source, value = _text(question), _text(query)
    if source == value:
        return ""
    # 日付・版ではなく業務IDを対象にする。明示別名以外の新しいIDは推測で追加しない。
    identifiers = re.findall(r'(?<![a-z0-9_])[a-z]{2,}[a-z0-9_-]*\d[a-z0-9_-]*', value)
    support = _text(question + " " + grounded_text)
    if any(not re.search(r'(?<![a-z0-9_])' + re.escape(identifier) + r'(?![a-z0-9_])', support)
           for identifier in identifiers):
        return "原質問・明示別名にない業務識別子"
    contract = task_contract(question)
    if contract["goal"] == "recipient_list" and re.search(r"人数|者数|件数", value) and not re.search(r"対象者|名簿|一覧|リスト|送付先", value):
        return "対象者を確認する目的が人数・件数に変化"
    if contract["missing_choice"] and re.search(r"(?:項目|欄|フィールド).{0,8}(?:表示されない|見えない)|データが取得できない", value) and not re.search(r"選択肢|候補|該当", value):
        return "選択肢に該当しない状態が項目非表示・取得失敗に変化"
    # 問い合わせ元の語（profile の requester_terms + 一般語）や地域が「〜別」「〜ごと」の絞込条件に転じた検索文を検出する (#849)。
    grouping = r"(?:" + _requester_terms_pattern() + r"|地域)"
    geographic_filter = grouping + r"(?:別|ごと)|" + grouping + r".{0,8}(?:絞|フィルタ)"
    if contract["requester_mentions"] and re.search(geographic_filter, value) and not re.search(geographic_filter, source):
        return "問い合わせ元が地域の絞込条件に変化"
    # 「<業務対象>の更新」（申請・台帳・伝票など、語彙は問わない）を尋ねる質問が、検索文でソフトウェアの版更新に
    # 変わっていないか。以前は福祉語彙（手帳・支給・手当）の列挙で判定していた (#849)。
    business_update = bool(re.search(r"[一-龯々ァ-ヶー]{2,}(?:の|を)?更新", source)) or any(obj in source for obj in contract["business_objects"])
    if business_update and "更新" in source and not re.search(r"バージョン|リリース|旧版|新版", source) and re.search(r"バージョン|リリース|旧版|新版", value):
        return "業務更新がソフトウェア版変更に変化"
    return ""


def filter_queries(question: str, queries: Sequence[str], *, grounded_text: str = "") -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """採用検索文と、理由付きの不採用検索文を返す。入力は変更しない。"""
    accepted, rejected = [], []
    for query in queries:
        reason = query_rejection_reason(question, query, grounded_text=grounded_text)
        if reason:
            rejected.append({"query": query, "reason": reason})
        else:
            accepted.append(query)
    return tuple(accepted), tuple(rejected)
