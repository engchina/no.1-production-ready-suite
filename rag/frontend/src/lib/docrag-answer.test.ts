import { describe, expect, it } from "vitest";

import { confidenceVariant, parseDocragDiagnostics } from "./docrag-answer";

describe("parseDocragDiagnostics", () => {
  it("DocRAG 診断を表示用に正規化する", () => {
    const parsed = parseDocragDiagnostics({
      confidence: "high",
      needs_human_review: false,
      insufficient_reason: "",
      rewritten_question: "受注入力画面での受注の登録方法は？",
      generated_queries: ["受注 登録"],
      execution_steps: [
        {
          name: "質問の理解",
          status: "complete",
          elapsed_seconds: 0.2,
          llm_calls: 1,
        },
      ],
      evidence_tree: [
        {
          parent_id: "doc-1:p1",
          source: "manual.pdf",
          page: 1,
          children: [
            {
              chunk_id: "doc-1:c1",
              role: "retrieved_anchor",
              is_model_used: true,
              page: 1,
            },
          ],
        },
      ],
    });
    expect(parsed?.rewrittenQuestion).toBe("受注入力画面での受注の登録方法は？");
    expect(parsed?.steps[0]).toEqual({
      name: "質問の理解",
      status: "complete",
      elapsedSeconds: 0.2,
      llmCalls: 1,
    });
    expect(parsed?.tree[0].children[0]).toEqual({
      chunkId: "doc-1:c1",
      role: "retrieved_anchor",
      modelUsed: true,
      page: 1,
    });
  });

  it("DocRAG 以外は null、信頼度を variant に写す", () => {
    expect(parseDocragDiagnostics(null)).toBeNull();
    expect(confidenceVariant("high")).toBe("success");
    expect(confidenceVariant("low")).toBe("danger");
    expect(confidenceVariant("")).toBe("neutral");
  });
});
