import type { ExtractionFieldCondition } from "@/components/search/extraction-field-filters";

/** 回答フローの診断(backend の diagnostics.answer)。 */
export type AnswerDiagnostics = {
  confidence: string;
  needsHumanReview: boolean | null;
  insufficientReason: string;
  /** 判断理由（引用照合の件数など。#651）。 */
  reasoningSummary: string;
  /** 資料では決まらず業務システムの実データで確かめる値（#651）。 */
  externalDataRequired: boolean;
  externalDataItems: string[];
  /** 問い合わせ型（回答モデルが付けた質問の種類の語。#651）。 */
  questionType: string[];
  /**
   * 質問から読み取って検索に足した抽出項目の条件（#652）。relaxed は、その条件で見つからず
   * 外して検索し直したか。読み取っていなければ null。
   */
  autoFieldFilter: { conditions: ExtractionFieldCondition[]; relaxed: boolean } | null;
  /** チャットで会話履歴から書き換えた質問(書き換えなしは空)。 */
  rewrittenQuestion: string;
  generatedQueries: string[];
  /**
   * 使ったモデル（#649）。LLM（既定のテキストモデル）は質問の理解・拡張と回答の生成、vision は根拠の原画像を
   * 添付して答えたときの既定の Vision モデル（添付しなければ null）、rerank は無効なら空。
   */
  models: { llm: ModelRef | null; vision: ModelRef | null; embedding: string; rerank: string };
  steps: {
    name: string;
    status: string;
    elapsedSeconds: number | null;
    llmCalls: number;
  }[];
  tree: {
    parentId: string;
    source: string;
    page: number | null;
    children: {
      chunkId: string;
      role: string;
      modelUsed: boolean;
      page: number | null;
    }[];
  }[];
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** diagnostics.answer を表示用に正規化する。回答フローの診断が無い回答では null。 */
export function parseAnswerDiagnostics(
  value: unknown,
): AnswerDiagnostics | null {
  if (!value || typeof value !== "object") return null;
  const raw = record(value);
  return {
    confidence: String(raw.confidence ?? ""),
    needsHumanReview:
      typeof raw.needs_human_review === "boolean"
        ? raw.needs_human_review
        : null,
    insufficientReason: String(raw.insufficient_reason ?? ""),
    reasoningSummary: String(raw.reasoning_summary ?? ""),
    externalDataRequired: raw.external_data_required === true,
    externalDataItems: list(raw.external_data_items).map(String).filter(Boolean),
    questionType: list(raw.question_type).map(String).filter(Boolean),
    autoFieldFilter: parseAutoFieldFilter(raw.auto_field_filter),
    rewrittenQuestion: String(raw.rewritten_question ?? ""),
    generatedQueries: list(raw.generated_queries).map(String).filter(Boolean),
    models: parseModels(raw.models),
    steps: list(raw.execution_steps).map((step) => {
      const item = record(step);
      return {
        name: String(item.name ?? ""),
        status: String(item.status ?? ""),
        elapsedSeconds: num(item.elapsed_seconds),
        llmCalls: num(item.llm_calls) ?? 0,
      };
    }),
    tree: list(raw.evidence_tree).map((parent) => {
      const item = record(parent);
      return {
        parentId: String(item.parent_id ?? ""),
        source: String(item.source ?? ""),
        page: num(item.page),
        children: list(item.children).map((child) => {
          const entry = record(child);
          return {
            chunkId: String(entry.chunk_id ?? ""),
            role: String(entry.role ?? ""),
            modelUsed: entry.is_model_used === true,
            page: num(entry.page),
          };
        }),
      };
    }),
  };
}

function parseAutoFieldFilter(value: unknown): AnswerDiagnostics["autoFieldFilter"] {
  const raw = record(value);
  const conditions = list(raw.conditions).flatMap((item) => {
    const entry = record(item);
    const name = String(entry.name ?? "");
    const valueType = String(entry.value_type ?? "");
    const op = String(entry.op ?? "");
    if (!name || !["string", "number", "date", "bool"].includes(valueType)) return [];
    if (!["eq", "gte", "lte"].includes(op)) return [];
    return [
      {
        name,
        value_type: valueType as ExtractionFieldCondition["value_type"],
        op: op as ExtractionFieldCondition["op"],
        value: String(entry.value ?? ""),
      },
    ];
  });
  return conditions.length ? { conditions, relaxed: raw.relaxed === true } : null;
}

type ModelRef = { modelId: string; label: string };

function parseModelRef(value: unknown): ModelRef | null {
  const raw = record(value);
  const modelId = String(raw.model_id ?? "");
  return modelId ? { modelId, label: String(raw.label ?? "") || modelId } : null;
}

function parseModels(value: unknown): AnswerDiagnostics["models"] {
  const raw = record(value);
  return {
    llm: parseModelRef(raw.llm),
    vision: parseModelRef(raw.vision),
    embedding: String(raw.embedding ?? ""),
    rerank: String(raw.rerank ?? ""),
  };
}

/** 信頼度 → StatusBadge の variant。 */
export function confidenceVariant(
  confidence: string,
): "success" | "warning" | "danger" | "neutral" {
  if (confidence === "high") return "success";
  if (confidence === "medium") return "warning";
  if (confidence === "low") return "danger";
  return "neutral";
}

/** 標準回答による評価（評価の基準の指標と閾値で判定。backend の evaluation。#680）。 */
export type AnswerEvaluationView = {
  status: string;
  message: string;
  passed: boolean | null;
  /** 判定に使った評価の基準（standard / strict）。 */
  suite: string;
  standardAnswer: string;
  evaluatedAt: string;
  /** reference の指標は表示だけで合否に使わない（1 件の回答の根拠との語句の一致率。#680 / #711）。 */
  metrics: {
    name: string;
    value: number | null;
    threshold: number | null;
    passed: boolean;
    reference: boolean;
  }[];
  /** 以前の方式（4 軸・20 点満点。rubric_version 10 以前）の評価。指標が無いので再評価を促す。 */
  legacy: boolean;
  coverage: { index: number; requirement: string; status: string; quote: string }[];
  claims: { quote: string; status: string; reason: string }[];
  externalDataItems: string[];
};

export function parseAnswerEvaluation(value: unknown): AnswerEvaluationView | null {
  if (!value || typeof value !== "object") return null;
  const raw = record(value);
  const requirements = list(record(raw.standard_answer_scope).requirements).map(
    (item) => String(record(item).requirement ?? "")
  );
  const status = String(raw.status ?? "");
  return {
    status,
    message: String(raw.message ?? ""),
    passed: typeof raw.passed === "boolean" ? raw.passed : null,
    suite: String(raw.suite ?? ""),
    standardAnswer: String(raw.standard_answer ?? ""),
    evaluatedAt: String(raw.evaluated_at ?? ""),
    metrics: list(raw.metrics).map((item) => {
      const entry = record(item);
      return {
        name: String(entry.name ?? ""),
        value: num(entry.value),
        threshold: num(entry.threshold),
        passed: entry.passed === true,
        reference: entry.reference === true,
      };
    }),
    legacy: status === "completed" && !Array.isArray(raw.metrics),
    coverage: list(raw.coverage_checks).map((item) => {
      const entry = record(item);
      const index = num(entry.requirement_index) ?? 0;
      return {
        index,
        requirement: requirements[index - 1] ?? "",
        status: String(entry.status ?? ""),
        quote: String(entry.answer_quote ?? ""),
      };
    }),
    claims: list(raw.claim_checks).map((item) => {
      const entry = record(item);
      return {
        quote: String(entry.answer_quote ?? ""),
        status: String(entry.status ?? ""),
        reason: String(entry.reason ?? ""),
      };
    }),
    externalDataItems: list(raw.external_data_items).map(String).filter(Boolean),
  };
}

/** 評価の結果 → StatusBadge の variant とラベルの key。 */
export function evaluationOutcome(
  evaluation: AnswerEvaluationView
): {
  variant: "success" | "danger" | "warning" | "neutral";
  labelKey: "passed" | "failed" | "notCompleted" | "legacy";
} {
  if (evaluation.status !== "completed") return { variant: "warning", labelKey: "notCompleted" };
  if (evaluation.legacy) return { variant: "neutral", labelKey: "legacy" };
  return evaluation.passed
    ? { variant: "success", labelKey: "passed" }
    : { variant: "danger", labelKey: "failed" };
}
