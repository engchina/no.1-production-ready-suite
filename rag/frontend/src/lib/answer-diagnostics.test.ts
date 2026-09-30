import { describe, expect, it } from "vitest";

import {
  confidenceVariant,
  evaluationOutcome,
  parseAnswerEvaluation,
  parseAnswerDiagnostics,
} from "./answer-diagnostics";

describe("parseAnswerDiagnostics", () => {
  it("回答フローの診断を表示用に正規化する", () => {
    const parsed = parseAnswerDiagnostics({
      confidence: "high",
      needs_human_review: false,
      insufficient_reason: "",
      reasoning_summary: "引用照合済みの説明 2 件。",
      external_data_required: true,
      external_data_items: ["対象の受注の登録状態", ""],
      question_type: ["操作方法"],
      auto_field_filter: {
        conditions: [
          { name: "金額", value_type: "number", op: "gte", value: "100000" },
          { name: "壊れた条件", value_type: "number", op: "between", value: "1" },
        ],
        relaxed: true,
      },
      rewritten_question: "受注入力画面での受注の登録方法は？",
      generated_queries: ["受注 登録"],
      models: {
        llm: { model_id: "model-a", label: "Model A" },
        vision: { model_id: "vlm-a", label: "" },
        embedding: "cohere.embed-v4.0",
        rerank: "",
      },
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
    expect(parsed?.reasoningSummary).toBe("引用照合済みの説明 2 件。");
    expect(parsed?.externalDataRequired).toBe(true);
    expect(parsed?.externalDataItems).toEqual(["対象の受注の登録状態"]);
    expect(parsed?.questionType).toEqual(["操作方法"]);
    expect(parsed?.autoFieldFilter).toEqual({
      conditions: [{ name: "金額", value_type: "number", op: "gte", value: "100000" }],
      relaxed: true,
    });
    expect(parsed?.models).toEqual({
      llm: { modelId: "model-a", label: "Model A" },
      vision: { modelId: "vlm-a", label: "vlm-a" },
      embedding: "cohere.embed-v4.0",
      rerank: "",
    });
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

  it("回答フローの診断が無ければ null、信頼度を variant に写す", () => {
    expect(parseAnswerDiagnostics(null)).toBeNull();
    // モデルを持たない古い回答の記録でも壊れない。
    expect(parseAnswerDiagnostics({})?.autoFieldFilter).toBeNull();
    expect(parseAnswerDiagnostics({})?.models).toEqual({ llm: null, vision: null, embedding: "", rerank: "" });
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
