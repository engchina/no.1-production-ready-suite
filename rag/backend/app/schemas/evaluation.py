"""RAG 評価スキーマ。"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import AnswerFlow, EvaluationSuite, QueryStrategy
from app.schemas.search import (
    SearchDiagnostics,
    format_search_id_filter,
    normalize_query_text,
    normalize_search_filters,
    normalize_search_id_list,
    parse_search_id_filter,
)

EvaluationSuiteName = EvaluationSuite

# 評価ケースの id の長さの上限(文字)。job の `current_case_id` の列(VARCHAR2(200 CHAR))。
EVALUATION_CASE_ID_MAX_CHARS = 200

# 標準回答の長さの上限(文字)。標準回答による評価の入力の予算(48,000 bytes)に収まる長さにする。
STANDARD_ANSWER_MAX_CHARS = 8000

# 評価のケースの分類（#1226。業務支援の改修 #1218 の評価の契約）。答えられるかの種類で結果を
# 分けて見る。
# - document_answerable: 資料だけで答えられる
# - clarification_required: 利用者に条件を確かめる必要がある
# - environment_data_required: 現場の値・記録（参照の道具）が要る
# - knowledge_missing: 資料に答えが無い（拒答・人への引き継ぎが正しい）
# - conflicting_sources: 資料どうしが矛盾する
EvaluationCaseCategory = Literal[
    "document_answerable",
    "clarification_required",
    "environment_data_required",
    "knowledge_missing",
    "conflicting_sources",
]
EVALUATION_UNCATEGORIZED = "uncategorized"

# 評価のケースの区分（#1284。handoff §14.1）。dev は設定・業務ガイドの調整に使ってよいケース、
# holdout は調整に使わず最後の確認だけに使うケース。同じ業務の流れ・資料の族のケースは同じ区分に
# する（言い換えの漏れを防ぐ）。区分の無いケースは内訳で unassigned にする。
EvaluationCaseSplit = Literal["dev", "holdout"]
EVALUATION_UNASSIGNED_SPLIT = "unassigned"

# 多段の質問（multi-hop）の種類（#1335）。複数の根拠をつないで初めて答えられる質問を、つなぎ方で
# 分けて見る。採点の方法は変えない。
# - single_hop: 1 つの根拠で答えられる（対照）
# - bridge: 橋渡し（A の属性で B を引き、B の属性で答える）
# - comparison: 2 つ以上の実体の属性を比べる
# - intra_document_reference: 同じ文書の中の参照（「第 3 章を参照」）をたどる
# - table_lookup: 本文の実体で表の行を引く（表の中の絞り込みを含む）
EvaluationReasoningType = Literal[
    "single_hop",
    "bridge",
    "comparison",
    "intra_document_reference",
    "table_lookup",
]
EVALUATION_UNSPECIFIED_REASONING = "unspecified"
# 段の数の上限と、2 段以上を求める種類（比べる・表を引くは 1 段でもよい）。
EVALUATION_MAX_HOPS = 10
EVALUATION_MULTI_HOP_REASONING_TYPES: frozenset[str] = frozenset(
    {"bridge", "intra_document_reference"}
)

# 1 ケースの往復の上限（最初の質問を除く）と、往復をまたいだ条件の数の上限
# （SearchRequest.conditions と同じ）。
EVALUATION_MAX_TURNS = 5
EVALUATION_MAX_CONDITIONS = 30

# 回答の対応（#1231。業務支援の評価）。評価のケースは受け入れる対応を複数持てる
# （例: 確認が要る質問は、確認の質問か、条件ごとに分けた回答のどちらでもよい）。
# - answered: 資料にもとづいて答えた
# - conditional: 条件・不足を示して答えた（一部に確認が要る）
# - needs_clarification: 利用者に条件を確かめた
# - needs_environment_data: 現場の値・記録が要ると示した
# - needs_human: 人への引き継ぎを示した
# - insufficient_evidence: 資料から答えられないと示した（拒答）
EvaluationOutcome = Literal[
    "answered",
    "conditional",
    "needs_clarification",
    "needs_environment_data",
    "needs_human",
    "insufficient_evidence",
]

# 失敗理由(#591)。保存済みの結果には削除した理由(content_kind_miss など)が残るため、
# 結果の model は str で受ける。
EvaluationFailureReason = Literal[
    "retrieval_miss",
    "partial_recall",
    "unexpected_answer",
    "unexpected_refusal",
    "answer_keyword_miss",
    "low_groundedness",
    "unsupported_claim",
    "missing_content",
    "answer_failed",
    "answer_evaluation_error",
    "guardrail_warning",
    "unexpected_handling",
    "step_missing",
    "forbidden_action",
    "condition_missing",
    # 必要な根拠（required_evidence）の一部を最後の回答の引用で取れなかった（#1284）。
    "evidence_miss",
    # 質問・前の返答で渡した条件を、確認の質問でもう一度聞いた（#1284）。
    "known_condition_reasked",
    "case_error",
]

# 評価の指標(#591)。検索・根拠・回答の 3 つの観点に整理した 9 つと、業務支援の対応の 4 つ（#1231）。
EvaluationMetricName = Literal[
    "context_recall",
    "mrr",
    "faithfulness",
    "citation_traceability_coverage",
    "claim_support_rate",
    "answer_keyword_hit_rate",
    "refusal_accuracy",
    "requirement_coverage",
    "answer_pass_rate",
    "handling_accuracy",
    "step_order_score",
    "safe_answer_rate",
    "condition_coverage",
]
EVALUATION_METRIC_NAMES: tuple[EvaluationMetricName, ...] = (
    "context_recall",
    "mrr",
    "faithfulness",
    "citation_traceability_coverage",
    "claim_support_rate",
    "answer_keyword_hit_rate",
    "refusal_accuracy",
    "requirement_coverage",
    "answer_pass_rate",
    "handling_accuracy",
    "step_order_score",
    "safe_answer_rate",
    "condition_coverage",
)

# 閾値の判定に使わず、結果に出すだけの集計（#1284）。必要な根拠の再現率は、根拠を持つ評価セットが
# まだ少ないため、基準（閾値）と画面の指標の一覧には入れない。根拠の連鎖の完全率（#1335）は、必要な
# 根拠をすべて取れたケースの割合（多段の質問は 1 つでも欠けると答えられないため、再現率の平均とは
# 別に見る）。
EVALUATION_REPORT_ONLY_METRIC_NAMES: tuple[str, ...] = (
    "required_evidence_recall",
    "evidence_chain_complete_rate",
)

# 期待する手順の別解の上限（列の数）。
EVALUATION_MAX_STEP_ALTERNATIVES = 5


def _clean_phrase_list(value: list[str]) -> list[str]:
    """前後の空白を除き、空の語を捨てる。"""
    return [item.strip() for item in value if item.strip()]


def _clean_conditions(value: dict[str, str]) -> dict[str, str]:
    """条件の id と値の前後の空白を除き、空の id・値を捨てる。"""
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        condition_id, condition_value = key.strip(), item.strip()
        if condition_id and condition_value:
            cleaned[condition_id] = condition_value
    return cleaned


def _clean_alternatives(value: list[list[str]]) -> list[list[str]]:
    """別解の各列の語を整え、空になった列を捨てる。"""
    return [steps for steps in (_clean_phrase_list(item) for item in value) if steps]


class EvaluationHandlingExpectation(BaseModel):
    """1 つの回答への業務支援の期待（#1231。往復の回答にも使う。#1284）。

    どれも空ならその採点の対象外。
    """

    # 受け入れる回答の対応（1 つでも一致すれば正しい）。
    expected_outcomes: list[EvaluationOutcome] = Field(default_factory=list, max_length=6)
    # 回答に出るべき手順の語（期待の順）。網羅と順序を採点する。
    expected_steps: list[str] = Field(default_factory=list, max_length=30)
    # expected_steps と同等の別の手順の列（#1284。handoff §14.1「可接受替代方案」）。
    # いちばん点の高い列で採点する。同じ目的に根拠のある別の方法・別の画面の呼び方があるときに使う。
    acceptable_alternatives: list[list[str]] = Field(
        default_factory=list, max_length=EVALUATION_MAX_STEP_ALTERNATIVES
    )
    # 勧めてはいけない操作の表現（影響範囲を広げる操作など）。回答に含まれたら危険な回答。
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=30)
    # 回答が触れるべき条件（確かめる条件・適用の前提）。
    required_conditions: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("expected_steps", "forbidden_phrases", "required_conditions")
    @classmethod
    def validate_phrases(cls, value: list[str]) -> list[str]:
        """前後の空白を除き、空の語を捨てる。"""
        return _clean_phrase_list(value)

    @field_validator("acceptable_alternatives")
    @classmethod
    def validate_alternatives(cls, value: list[list[str]]) -> list[list[str]]:
        """別解の列ごとに語を整える（1 列は 30 語まで）。"""
        if any(len(item) > 30 for item in value):
            raise ValueError("手順の別解は 1 つの列につき 30 語までにしてください。")
        return _clean_alternatives(value)

    @model_validator(mode="after")
    def validate_alternatives_need_steps(self) -> Self:
        """別解は期待する手順の別の書き方なので、期待する手順が無ければ受け付けない。"""
        if self.acceptable_alternatives and not self.expected_steps:
            raise ValueError("acceptable_alternatives は expected_steps と一緒に指定してください。")
        return self


class EvaluationTurn(EvaluationHandlingExpectation):
    """確認の質問への返答の 1 往復（#1284。handoff §14.1「允许的澄清回应」）。

    ランナーはチャットと同じく、`reply` を次の質問にし、前の往復（質問と回答）を会話の履歴として
    渡す。`conditions` は返答で分かった条件の値で、前までの条件に足して渡す（チャットの確認の答え・
    MCP の `conditions` と同じ）。期待（対応・手順・危険な表現・条件）は、この往復の回答だけを採点
    する。
    """

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(..., min_length=1, max_length=8000)
    conditions: dict[str, str] = Field(default_factory=dict, max_length=EVALUATION_MAX_CONDITIONS)

    @field_validator("reply")
    @classmethod
    def validate_reply(cls, value: str) -> str:
        """SearchRequest の query と同じ規則で返答を正規化する。"""
        return normalize_query_text(value)

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, value: dict[str, str]) -> dict[str, str]:
        """条件の id と値の前後の空白を除く。"""
        return _clean_conditions(value)


class EvaluationEvidence(BaseModel):
    """回答に必要な根拠（#1284。handoff §14.2 の「各必需证据召回」）。

    最後の回答の引用（chunk）のうち、`document_id`（省略時はどの文書でもよい）の chunk の本文が
    `text` を含めば、この根拠を取れたとみなす（NFKC・大小文字・空白を無視）。文書 ID は評価セット
    では `file:<ファイル名>` で書き、`evaluation_corpus_cli` が配備先の ID に置き換える。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    document_id: str | None = Field(default=None, max_length=200)
    text: str = Field(..., min_length=1, max_length=300)

    @field_validator("id", "text")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """前後の空白を除き、空の値を拒否する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("根拠の id と語句を入力してください。")
        return cleaned

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str | None) -> str | None:
        """空の文書 ID は指定なしとして扱う。"""
        if value is None:
            return None
        return value.strip() or None


class EvaluationCase(EvaluationHandlingExpectation):
    """1 件の評価ケース。

    `answerable=false` のケース(資料に答えが無い質問)は、拒答の正しさだけを測る。省略したときは、
    正解の文書・期待する語・標準回答のどれも無いケースを答えるべきでない質問とみなす(#301)。
    `standard_answer` があるケースは、回答を標準回答と LLM で比較する(4 軸の採点・主張の監査・
    必要な項目の網羅)。削除した期待値の欄(`expected_content_kind`・`expected_section_paths`。
    #591)は、既存の golden set を読めるように無視する。

    複数往復のケース（`turns`。#1284）では、ケースの直下の業務支援の期待（対応・手順・別解・危険な
    表現・条件）は最初の質問への回答を、各往復の期待はその往復の回答を採点する。正解の文書・期待する
    語・標準回答・答えるべきか・必要な根拠は、最後の回答（往復を終えた後の回答）で採点する。
    """

    # 結果の表・job の実行中のケース（`rag_evaluation_jobs.current_case_id` は 200 文字）で
    # ケースを区別するため、空でない 200 文字までにする（#977）。
    id: str = Field(..., min_length=1, max_length=EVALUATION_CASE_ID_MAX_CHARS)
    query: str = Field(..., min_length=1)
    relevant_document_ids: list[str] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)
    standard_answer: str | None = Field(default=None, max_length=STANDARD_ANSWER_MAX_CHARS)
    answerable: bool | None = None
    # 分類（任意。#1226）。結果の分類ごとの内訳に使う。採点の方法は変えない。
    category: EvaluationCaseCategory | None = None
    # 区分（任意。#1284）。結果の区分ごとの内訳に使う。採点の方法は変えない。
    split: EvaluationCaseSplit | None = None
    # 質問と一緒に渡す既知の条件の値（条件の id → 値。#1284）。チャットの確認の答え・MCP の
    # conditions と同じく SearchRequest.conditions に渡す。業務ガイドの条件の id に合わせて書き、
    # 業務ガイドを使わない評価（検索・回答プロファイルを指定しない評価）では回答に影響しない。
    conditions: dict[str, str] = Field(default_factory=dict, max_length=EVALUATION_MAX_CONDITIONS)
    # 確認の質問への返答の列（#1284）。最初の質問の後に順に送る。
    turns: list[EvaluationTurn] = Field(default_factory=list, max_length=EVALUATION_MAX_TURNS)
    # 最後の回答に必要な根拠（#1284）。根拠ごとの再現率を求める。多段の質問（#1335）は段ごとの
    # 根拠を全部書く。
    required_evidence: list[EvaluationEvidence] = Field(default_factory=list, max_length=30)
    # 多段の質問の種類と段の数（任意。#1335）。結果の種類別・段の数別の内訳に使う。採点の方法は
    # 変えない。段の数は、根拠をたどる回数（比べる質問は 1 つの実体あたりの回数）。
    reasoning_type: EvaluationReasoningType | None = None
    hops: int | None = Field(default=None, ge=1, le=EVALUATION_MAX_HOPS)
    # 新しい版に置き換えた文書（旧版）も検索して答えるケース（版の比較など。#1366）。true なら、
    # 利用者が旧版・変更点を尋ねるときと同じく filters に include_superseded=true を足して回答する。
    include_superseded: bool = False

    @property
    def expects_answer(self) -> bool:
        """答えるべき質問か(明示が無ければ、期待する内容を 1 つでも持つか)。"""
        if self.answerable is not None:
            return self.answerable
        return bool(
            self.relevant_document_ids
            or self.expected_answer_keywords
            or self.standard_answer
            or self.required_evidence
        )

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """前後の空白を除き、空の id を拒否する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("評価ケースの id を入力してください。")
        return cleaned

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """SearchRequest と同じ規則で query を正規化する。"""
        return normalize_query_text(query)

    @field_validator("standard_answer")
    @classmethod
    def validate_standard_answer(cls, value: str | None) -> str | None:
        """空白だけの標準回答は無いものとして扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, value: dict[str, str]) -> dict[str, str]:
        """条件の id と値の前後の空白を除く。"""
        return _clean_conditions(value)

    @model_validator(mode="after")
    def validate_case_contract(self) -> Self:
        """根拠の id の重複と、往復をまたいだ条件の数の上限（SearchRequest と同じ）を確かめる。"""
        evidence_ids = [item.id for item in self.required_evidence]
        duplicates = sorted({item for item in evidence_ids if evidence_ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"必要な根拠の id が重複しています: {', '.join(duplicates)}")
        if len(self.all_condition_ids()) > EVALUATION_MAX_CONDITIONS:
            raise ValueError(
                f"条件は往復をまたいで {EVALUATION_MAX_CONDITIONS} 個までにしてください。"
            )
        self._validate_reasoning()
        return self

    def _validate_reasoning(self) -> None:
        """多段の質問の種類と段の数の組み合わせを確かめる（#1335）。

        段の数は種類と一緒に書く。1 段の質問（single_hop）は 1 段、橋渡し・文書の中の参照は 2 段
        以上。段ごとの根拠を書くため、必要な根拠があるときは段の数より少なくしない。
        """
        if self.hops is None:
            return
        if self.reasoning_type is None:
            raise ValueError("hops は reasoning_type と一緒に指定してください。")
        if self.reasoning_type == "single_hop" and self.hops != 1:
            raise ValueError("single_hop の質問の hops は 1 にしてください。")
        if self.reasoning_type in EVALUATION_MULTI_HOP_REASONING_TYPES and self.hops < 2:
            raise ValueError(f"{self.reasoning_type} の質問の hops は 2 以上にしてください。")
        if self.required_evidence and len(self.required_evidence) < self.hops:
            raise ValueError(
                "必要な根拠は段ごとに書いてください"
                f"（hops={self.hops}、根拠 {len(self.required_evidence)} 件）。"
            )

    def all_condition_ids(self) -> set[str]:
        """質問と往復で渡す条件の id の集合。"""
        ids = set(self.conditions)
        for turn in self.turns:
            ids.update(turn.conditions)
        return ids


class EvaluationAnswerJudgement(BaseModel):
    """標準回答による評価(評価の基準の指標と閾値による合否・主張の監査・必要な項目の網羅)の要約。

    詳細(指標ごとの値・主張ごとの判定)は trace_id の回答の記録に保存する(#680)。`status` は
    completed / error / input_too_large / timeout / unavailable(評価の記録が無いなど)。
    """

    status: str
    passed: bool | None = None
    claims_supported: bool | None = None
    requirement_coverage: float | None = None
    # 原質問に必要な項目のうち、説明の無い項目があったか(rag_poc の「必要内容欠落」)。
    missing_content: bool | None = None
    message: str | None = None


class EvaluationTurnResult(BaseModel):
    """複数往復のケースの 1 回の回答の採点（#1284）。

    turn 0 は最初の質問への回答。期待の無い項目は None / 空。
    """

    turn: int
    trace_id: str
    observed_outcome: EvaluationOutcome | None = None
    outcome_source: str | None = None
    handling_correct: bool | None = None
    step_order_score: float | None = None
    missing_steps: list[str] = Field(default_factory=list)
    forbidden_checked: bool = False
    forbidden_hits: list[str] = Field(default_factory=list)
    condition_coverage: float | None = None
    missing_conditions: list[str] = Field(default_factory=list)
    # 渡した条件のうち、確認の質問でもう一度聞いた条件の id。
    reasked_conditions: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class EvaluationCaseResult(BaseModel):
    """1 評価ケースごとの診断結果。測れない指標は None。

    複数往復のケース（#1284）では、検索・根拠・回答の指標は最後の回答で求め、業務支援の対応は
    往復ごと（`turn_results`）に採点してまとめる（対応はすべての往復で正しいとき正しい、手順・条件は
    往復の平均、危険な表現・聞き直しは往復の和）。`observed_outcome` は最後の回答の対応、`trace_id`
    は最後の回答の記録、`elapsed_ms` は往復の合計。
    """

    case_id: str
    trace_id: str
    category: EvaluationCaseCategory | None = None
    split: EvaluationCaseSplit | None = None
    # 多段の質問の種類と段の数（ケースの値。#1335）。
    reasoning_type: EvaluationReasoningType | None = None
    hops: int | None = None
    status: Literal["success", "error"] = "success"
    retrieved_document_ids: list[str] = Field(default_factory=list)
    relevant_document_ids: list[str] = Field(default_factory=list)
    hit_document_ids: list[str] = Field(default_factory=list)
    context_recall: float | None = None
    reciprocal_rank: float | None = None
    faithfulness: float | None = None
    grounding_overlap_count: int = 0
    grounding_answer_feature_count: int = 0
    citation_traceability_coverage: float | None = None
    answer_keyword_hit: bool | None = None
    # 回答が「資料から答えられない」旨だけだったか(拒答)と、それが期待どおりだったか。
    abstained: bool | None = None
    refusal_correct: bool | None = None
    # 業務支援の採点（#1231）。ケースに期待が無い項目は None / 空。
    observed_outcome: EvaluationOutcome | None = None
    # explicit（回答の記録が対応を持っていた）/ inferred（診断から推定した）
    outcome_source: str | None = None
    handling_correct: bool | None = None
    step_order_score: float | None = None
    missing_steps: list[str] = Field(default_factory=list)
    # 勧めてはいけない操作の表現を照合したか（ケースに forbidden_phrases があったか）。
    forbidden_checked: bool = False
    forbidden_hits: list[str] = Field(default_factory=list)
    condition_coverage: float | None = None
    missing_conditions: list[str] = Field(default_factory=list)
    # 渡した条件のうち、確認の質問でもう一度聞いた条件の id（#1284）。
    reasked_conditions: list[str] = Field(default_factory=list)
    # 必要な根拠の再現率と、取れなかった根拠の id（#1284）。
    # 根拠の無いケース・検索をしない回答は None。
    evidence_recall: float | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    # 必要な根拠をすべて取れたか（#1335。evidence_recall が 1 のとき True）。測れないときは None。
    evidence_chain_complete: bool | None = None
    # 往復ごとの採点（往復のあるケースだけ。#1284）。
    turn_results: list[EvaluationTurnResult] = Field(default_factory=list)
    answer_evaluation: EvaluationAnswerJudgement | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    elapsed_ms: float
    error_type: str | None = None
    # 時間切れになった工程（進捗の stage と同じ名前。例: answer）。
    # 時間切れ以外は None（#383）。
    error_stage: str | None = None
    error_message: str | None = None


class EvaluationThresholdFailure(BaseModel):
    """閾値を下回った aggregate metric。保存済みの結果の削除した指標も読めるよう str で受ける。"""

    metric: str
    actual: float
    threshold: float


class EvaluationCategorySummary(BaseModel):
    """分類ごとの結果の内訳（#1226）。率は測れるケースだけの割合で、測れなければ None。"""

    case_count: int
    error_count: int = 0
    answer_pass_rate: float | None = None
    answer_keyword_hit_rate: float | None = None
    # 回答が「資料から答えられない」旨だけだった割合と、拒答の判断が期待どおりだった割合。
    abstain_rate: float | None = None
    refusal_correct_rate: float | None = None
    # 業務支援の対応（#1231）。
    handling_correct_rate: float | None = None
    step_order_score: float | None = None
    safe_answer_rate: float | None = None


class EvaluationReasoningSummary(BaseModel):
    """多段の質問の種類別・段の数別の結果（#1335）。

    `metrics` は根拠と回答の指標（`EVALUATION_REASONING_METRIC_NAMES`）を、そのケースだけで求めた値
    （測れなければ None）。
    """

    case_count: int
    error_count: int = 0
    metrics: dict[str, float | None] = Field(default_factory=dict)
    metric_case_counts: dict[str, int] = Field(default_factory=dict)


# 種類別・段の数別の内訳に出す指標（#1335）。
EVALUATION_REASONING_METRIC_NAMES: tuple[str, ...] = (
    "required_evidence_recall",
    "evidence_chain_complete_rate",
    "answer_keyword_hit_rate",
)


class EvaluationSplitSummary(BaseModel):
    """区分（dev / holdout）ごとの結果（#1284）。

    `metrics` は全体と同じ指標（と参考の集計 `required_evidence_recall`）を、その区分のケースだけで
    求めた値（測れなければ None）。
    """

    case_count: int
    error_count: int = 0
    metrics: dict[str, float | None] = Field(default_factory=dict)
    metric_case_counts: dict[str, int] = Field(default_factory=dict)
    failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    # 区分の中の多段の質問の種類別・段の数別の内訳（#1335。ケースに種類・段の数があるときだけ）。
    reasoning_type_breakdown: dict[str, EvaluationReasoningSummary] = Field(default_factory=dict)
    hops_breakdown: dict[str, EvaluationReasoningSummary] = Field(default_factory=dict)


class EvaluationMetrics(BaseModel):
    """評価結果の集計指標(#591)。

    各指標は、その指標を測れるケースだけの平均で、対象の件数は `metric_case_counts` に持つ。
    対象のケースが無い指標は None。保存済みの結果にある削除した指標は読み込み時に捨てる。
    """

    case_count: int
    error_count: int = 0
    evaluation_suite: str = "standard"
    context_recall: float | None = None
    mrr: float | None = None
    faithfulness: float | None = None
    citation_traceability_coverage: float | None = None
    claim_support_rate: float | None = None
    answer_keyword_hit_rate: float | None = None
    refusal_accuracy: float | None = None
    requirement_coverage: float | None = None
    answer_pass_rate: float | None = None
    handling_accuracy: float | None = None
    step_order_score: float | None = None
    safe_answer_rate: float | None = None
    condition_coverage: float | None = None
    # 必要な根拠ごとの再現率（ケースの平均。参考の集計で閾値の判定には使わない。#1284）。
    required_evidence_recall: float | None = None
    # 必要な根拠をすべて取れたケースの割合（参考の集計で閾値の判定には使わない。#1335）。
    evidence_chain_complete_rate: float | None = None
    metric_case_counts: dict[str, int] = Field(default_factory=dict)
    passed: bool = True
    threshold_failures: list[EvaluationThresholdFailure] = Field(default_factory=list)
    failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    # 分類ごとの内訳（ケースに分類があるときだけ。分類の無いケースは uncategorized。#1226）。
    category_breakdown: dict[str, EvaluationCategorySummary] = Field(default_factory=dict)
    # 区分ごとの内訳（ケースに区分があるときだけ。区分の無いケースは unassigned。#1284）。
    split_breakdown: dict[str, EvaluationSplitSummary] = Field(default_factory=dict)
    # 多段の質問の種類別・段の数別の内訳（ケースに種類・段の数があるときだけ。無いケースは
    # unspecified。段の数の key は "1"・"2" などの文字列。#1335）。
    reasoning_type_breakdown: dict[str, EvaluationReasoningSummary] = Field(default_factory=dict)
    hops_breakdown: dict[str, EvaluationReasoningSummary] = Field(default_factory=dict)
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)


class EvaluationThresholds(BaseModel):
    """CI gate に使う aggregate metric の最低値。削除した指標の名前は受け付けない。"""

    model_config = ConfigDict(extra="forbid")

    context_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_traceability_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_support_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_keyword_hit_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    requirement_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    handling_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    step_order_score: float | None = Field(default=None, ge=0.0, le=1.0)
    safe_answer_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    condition_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationRagOverrides(BaseModel):
    """評価 experiment ごとに一時適用する非 secret の回答設定(#591)。

    回答エンジンの全体の既定(質問拡張戦略・回答生成フロー・近傍 child 数・rerank)と、検索の
    RRF 定数・同じ group から足す child の上限・交差参照で足す chunk(#1280)・ベクトル検索の
    target accuracy を上書きする。
    削除したキー(query_expansion_* / context_window_chars など)は受け付けない。
    """

    model_config = ConfigDict(extra="forbid")

    query_strategy: QueryStrategy | None = None
    answer_flow: AnswerFlow | None = None
    neighbor_child_count: int | None = Field(default=None, ge=0, le=20)
    rerank_enabled: bool | None = None
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    context_group_max_chunks: int | None = Field(default=None, ge=1, le=20)
    reference_expansion_enabled: bool | None = None
    reference_expansion_max_chunks: int | None = Field(default=None, ge=1, le=20)
    oracle_vector_target_accuracy: int | None = Field(default=None, ge=1, le=100)


class EvaluationExperiment(BaseModel):
    """同じ golden set で比較する 1 つの回答設定。

    回答エンジンは検索の方式(mode)と rerank の件数(rerank_top_n)を使わないため、指定は
    受け付けない(#591。比較の結果が同じになるのに、違う設定に見えるため)。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    rag_overrides: EvaluationRagOverrides | None = None
    # 検索・回答プロファイル（任意。#1249）。指定すると、そのプロファイルの KB・回答の設定・
    # 業務ガイドで答える。
    search_answer_profile_id: str | None = Field(default=None, max_length=128)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """比較結果で安全に表示できる短い ID に正規化する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("experiment id を入力してください。")
        return cleaned

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """検索評価に使う filters を SearchRequest と同じ規則で正規化する。"""
        return normalize_search_filters(filters)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """評価 experiment のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """SearchRequest と同じ KB 指定制約を適用する。"""
        self.filters, self.knowledge_base_ids = _sync_knowledge_base_filter(
            self.filters,
            self.knowledge_base_ids,
        )
        return self


class EvaluationExperimentResult(BaseModel):
    """1 experiment の評価結果と ranking 情報。順位の指標を測れなかったときは None。"""

    rank: int
    ranking_score: float | None = None
    experiment: EvaluationExperiment
    metrics: EvaluationMetrics


class EvaluationCompareResponse(BaseModel):
    """複数 experiment の比較結果。保存済みの結果の削除した指標も読めるよう str で受ける。"""

    ranking_metric: str
    best_experiment_id: str | None
    results: list[EvaluationExperimentResult] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_experiment_fields(cls, data: object) -> object:
        """保存済みの結果の experiment にある削除した欄(mode 等)を、読み込み時に捨てる。"""
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return data
        allowed = set(EvaluationExperiment.model_fields)
        overrides_allowed = set(EvaluationRagOverrides.model_fields)
        results = []
        for result in data["results"]:
            experiment = result.get("experiment") if isinstance(result, dict) else None
            if isinstance(experiment, dict):
                cleaned = {key: value for key, value in experiment.items() if key in allowed}
                overrides = cleaned.get("rag_overrides")
                if isinstance(overrides, dict):
                    cleaned["rag_overrides"] = {
                        key: value for key, value in overrides.items() if key in overrides_allowed
                    }
                result = {**result, "experiment": cleaned}
            results.append(result)
        return {**data, "results": results}


def _reject_duplicate_case_ids(cases: list[EvaluationCase]) -> None:
    """結果のケースを id で区別できるよう、ケースの id の重複を拒否する(#977)。"""
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"評価ケースの id が重複しています: {', '.join(duplicates)}")


class EvaluationCompareRequest(BaseModel):
    """複数の回答設定の比較実行リクエスト。"""

    cases: list[EvaluationCase] = Field(..., min_length=1)
    experiments: list[EvaluationExperiment] = Field(..., min_length=1, max_length=20)
    ranking_metric: EvaluationMetricName = "context_recall"
    thresholds: EvaluationThresholds | None = None
    suite: EvaluationSuiteName | None = None

    @model_validator(mode="after")
    def validate_unique_experiment_ids(self) -> Self:
        """比較結果の識別を安定させるため experiment id とケースの id の重複を拒否する。"""
        _reject_duplicate_case_ids(self.cases)
        ids = [experiment.id for experiment in self.experiments]
        duplicates = sorted(
            {experiment_id for experiment_id in ids if ids.count(experiment_id) > 1}
        )
        if duplicates:
            raise ValueError(f"experiment id が重複しています: {', '.join(duplicates)}")
        return self


class EvaluationRunRequest(BaseModel):
    """評価実行リクエスト。

    回答エンジンの全体の既定で評価する(検索・回答プロファイルは受け取らない。#301)。検索の方式(mode)と
    rerank の件数(rerank_top_n)は回答エンジンが使わないため持たない(#591。以前の golden set の
    指定は無視する)。
    """

    cases: list[EvaluationCase] = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    thresholds: EvaluationThresholds | None = None
    suite: EvaluationSuiteName | None = None
    rag_overrides: EvaluationRagOverrides | None = None
    # 検索・回答プロファイル（任意。#1249）。指定しなければ全体の既定で評価する（#301）。
    search_answer_profile_id: str | None = Field(default=None, max_length=128)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """検索評価に使う filters を SearchRequest と同じ規則で正規化する。"""
        return normalize_search_filters(filters)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """評価実行のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """SearchRequest と同じ KB 指定制約を適用し、ケースの id の重複を拒否する。"""
        _reject_duplicate_case_ids(self.cases)
        self.filters, self.knowledge_base_ids = _sync_knowledge_base_filter(
            self.filters,
            self.knowledge_base_ids,
        )
        return self


def _sync_knowledge_base_filter(
    filters: dict[str, str],
    knowledge_base_ids: list[str],
) -> tuple[dict[str, str], list[str]]:
    """評価 request の明示 KB 指定を既存 filters 経路へ同期する。"""
    filter_knowledge_base_ids = parse_search_id_filter(filters.get("knowledge_base_id"))
    if (
        knowledge_base_ids
        and filter_knowledge_base_ids
        and knowledge_base_ids != filter_knowledge_base_ids
    ):
        raise ValueError(
            "knowledge_base_ids と filters.knowledge_base_id は同じ値を指定してください。"
        )
    resolved_knowledge_base_ids = knowledge_base_ids or filter_knowledge_base_ids
    if not resolved_knowledge_base_ids:
        return filters, []
    return (
        {
            **filters,
            "knowledge_base_id": format_search_id_filter(resolved_knowledge_base_ids),
        },
        resolved_knowledge_base_ids,
    )


EvaluationJobKind = Literal["run", "compare"]
EvaluationJobStatus = Literal["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


class EvaluationJob(BaseModel):
    """品質評価の job（非同期の実行・進捗・取り消し。#390）。

    進捗は、終わったケースの数（比較では experiment × ケースの通しの数）と今のケースで示す。
    結果は、成功（`SUCCEEDED`）のときだけ返す（評価は `run_result`、比較は `compare_result`）。
    時間の上限に達して打ち切ったケースは、結果の中で失敗のケースとして返す。query の本文は持たない。
    """

    job_id: str
    kind: EvaluationJobKind
    status: EvaluationJobStatus
    total_cases: int
    completed_cases: int
    current_case_id: str | None = None
    current_experiment_id: str | None = None
    current_case_started_at: datetime | None = None
    # job 全体の時間の上限（秒）。上限に達したら、残りのケースは実行せずに失敗として記録する。
    time_limit_seconds: int
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    run_result: EvaluationMetrics | None = None
    compare_result: EvaluationCompareResponse | None = None
