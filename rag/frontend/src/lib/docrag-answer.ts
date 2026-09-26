/** DocRAG 回答エンジンの診断(backend diagnostics.docrag)。 */
export type DocragDiagnostics = {
  confidence: string;
  needsHumanReview: boolean | null;
  insufficientReason: string;
  /** チャットで会話履歴から書き換えた質問(書き換えなしは空)。 */
  rewrittenQuestion: string;
  generatedQueries: string[];
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

/** diagnostics.docrag を表示用に正規化する。DocRAG 以外の回答では null。 */
export function parseDocragDiagnostics(
  value: unknown,
): DocragDiagnostics | null {
  if (!value || typeof value !== "object") return null;
  const raw = record(value);
  return {
    confidence: String(raw.confidence ?? ""),
    needsHumanReview:
      typeof raw.needs_human_review === "boolean"
        ? raw.needs_human_review
        : null,
    insufficientReason: String(raw.insufficient_reason ?? ""),
    rewrittenQuestion: String(raw.rewritten_question ?? ""),
    generatedQueries: list(raw.generated_queries).map(String).filter(Boolean),
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
