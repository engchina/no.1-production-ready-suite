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

/** 標準回答による評価(rag_poc の 4 軸評価、backend の evaluation)。 */
export type AnswerEvaluationView = {
  status: string;
  message: string;
  totalScore: number | null;
  maxScore: number;
  passThreshold: number;
  passed: boolean | null;
  standardAnswer: string;
  evaluatedAt: string;
  axes: { key: string; score: number | null; reason: string }[];
  coverage: { index: number; requirement: string; status: string; quote: string }[];
  claims: { quote: string; status: string; reason: string }[];
  externalDataItems: string[];
};

export const EVALUATION_AXES = [
  "accuracy",
  "coverage",
  "evidence_consistency",
  "generation_quality",
] as const;

export function parseAnswerEvaluation(value: unknown): AnswerEvaluationView | null {
  if (!value || typeof value !== "object") return null;
  const raw = record(value);
  const scores = record(raw.scores);
  const requirements = list(record(raw.standard_answer_scope).requirements).map(
    (item) => String(record(item).requirement ?? "")
  );
  return {
    status: String(raw.status ?? ""),
    message: String(raw.message ?? ""),
    totalScore: num(raw.total_score),
    maxScore: num(raw.max_score) ?? 20,
    passThreshold: num(raw.pass_threshold) ?? 16,
    passed: typeof raw.passed === "boolean" ? raw.passed : null,
    standardAnswer: String(raw.standard_answer ?? ""),
    evaluatedAt: String(raw.evaluated_at ?? ""),
    axes: Object.keys(scores).length
      ? EVALUATION_AXES.map((key) => ({
          key,
          score: num(record(scores[key]).score),
          reason: String(record(scores[key]).reason ?? ""),
        }))
      : [],
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
): { variant: "success" | "danger" | "warning"; labelKey: "passed" | "failed" | "notCompleted" } {
  if (evaluation.status !== "completed") return { variant: "warning", labelKey: "notCompleted" };
  return evaluation.passed
    ? { variant: "success", labelKey: "passed" }
    : { variant: "danger", labelKey: "failed" };
}
