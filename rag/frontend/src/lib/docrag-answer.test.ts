import { describe, expect, it } from "vitest";

import {
  confidenceVariant,
  evaluationOutcome,
  parseAnswerEvaluation,
  parseDocragDiagnostics,
} from "./docrag-answer";

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

describe("parseAnswerEvaluation", () => {
  it("4 軸の点と固定項目の対応を表示用に正規化する", () => {
    const parsed = parseAnswerEvaluation({
      status: "completed",
      total_score: 17,
      max_score: 20,
      pass_threshold: 16,
      passed: true,
      standard_answer: "受注番号を入力する",
      scores: {
        accuracy: { score: 5, reason: "一致" },
        coverage: { score: 4, reason: "一部" },
        evidence_consistency: { score: 4, reason: "根拠あり" },
        generation_quality: { score: 4, reason: "明確" },
      },
      standard_answer_scope: { requirements: [{ requirement: "登録の手順" }] },
      coverage_checks: [{ requirement_index: 1, status: "partial", answer_quote: "受注番号" }],
      claim_checks: [{ answer_quote: "受注番号", status: "supported", reason: "原文" }],
      external_data_items: [],
    });

    expect(parsed?.axes.map((axis) => [axis.key, axis.score])).toEqual([
      ["accuracy", 5],
      ["coverage", 4],
      ["evidence_consistency", 4],
      ["generation_quality", 4],
    ]);
    expect(parsed?.coverage).toEqual([
      { index: 1, requirement: "登録の手順", status: "partial", quote: "受注番号" },
    ]);
    expect(parsed && evaluationOutcome(parsed)).toEqual({ variant: "success", labelKey: "passed" });
  });

  it("評価できなかった結果は点を持たず、未完了として扱う", () => {
    const parsed = parseAnswerEvaluation({ status: "error", message: "評価を完了できませんでした。" });

    expect(parsed?.axes).toEqual([]);
    expect(parsed?.totalScore).toBeNull();
    expect(parsed && evaluationOutcome(parsed).labelKey).toBe("notCompleted");
    expect(parseAnswerEvaluation(null)).toBeNull();
  });
});
