/** DocRAG 回答エンジンの診断(backend diagnostics.docrag)。 */
export type DocragDiagnostics = {
  confidence: string;
  needsHumanReview: boolean | null;
  insufficientReason: string;
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
