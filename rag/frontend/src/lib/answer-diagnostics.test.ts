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

  // #737
  it("承認済み FAQ から回答したときは、FAQ の原文を出典として読む", () => {
    expect(
      parseAnswerDiagnostics({
        answer_source: "approved_faq",
        approved_faq_question: "出張の日当はいくらですか？",
        approved_faq_answer: "一般は 1 泊 2,000 円です。",
      })?.approvedFaq,
    ).toEqual({ question: "出張の日当はいくらですか？", answer: "一般は 1 泊 2,000 円です。" });
    // 変更前に保存した回答は本文を持たない。
    expect(
      parseAnswerDiagnostics({ answer_source: "approved_faq", approved_faq_question: "Q" })?.approvedFaq,
    ).toEqual({ question: "Q", answer: "" });
  });

  it("回答フローの診断が無ければ null、信頼度を variant に写す", () => {
    expect(parseAnswerDiagnostics(null)).toBeNull();
    // モデルを持たない古い回答の記録でも壊れない。
    expect(parseAnswerDiagnostics({})?.autoFieldFilter).toBeNull();
    expect(parseAnswerDiagnostics({})?.models).toEqual({ llm: null, vision: null, embedding: "", rerank: "" });
    expect(parseAnswerDiagnostics({})?.approvedFaq).toBeNull();
    expect(confidenceVariant("high")).toBe("success");
    expect(confidenceVariant("low")).toBe("danger");
    expect(confidenceVariant("")).toBe("neutral");
  });
});

describe("parseAnswerEvaluation", () => {
  it("評価の基準の指標と固定項目の対応を表示用に正規化する", () => {
    const parsed = parseAnswerEvaluation({
      status: "completed",
      passed: false,
      suite: "strict",
      standard_answer: "受注番号を入力する",
      metrics: [
        { name: "requirement_coverage", value: 0.5, threshold: 0.9, passed: false },
        { name: "claim_support_rate", value: 1, threshold: 1, passed: true, reference: false },
        { name: "faithfulness", value: 0.4, threshold: 0.8, passed: false, reference: true },
      ],
      standard_answer_scope: { requirements: [{ requirement: "登録の手順" }] },
      coverage_checks: [{ requirement_index: 1, status: "partial", answer_quote: "受注番号" }],
      claim_checks: [{ answer_quote: "受注番号", status: "supported", reason: "原文" }],
      external_data_items: [],
    });

    expect(parsed?.suite).toBe("strict");
    expect(parsed?.metrics).toEqual([
      { name: "requirement_coverage", value: 0.5, threshold: 0.9, passed: false, reference: false },
      { name: "claim_support_rate", value: 1, threshold: 1, passed: true, reference: false },
      { name: "faithfulness", value: 0.4, threshold: 0.8, passed: false, reference: true },
    ]);
    expect(parsed?.coverage).toEqual([
      { index: 1, requirement: "登録の手順", status: "partial", quote: "受注番号" },
    ]);
    expect(parsed?.legacy).toBe(false);
    expect(parsed && evaluationOutcome(parsed)).toEqual({ variant: "danger", labelKey: "failed" });
  });

  it("以前の方式（4 軸・20 点満点）の結果は指標を持たず、再評価を促す", () => {
    const parsed = parseAnswerEvaluation({ status: "completed", total_score: 20, passed: true });

    expect(parsed?.metrics).toEqual([]);
    expect(parsed && evaluationOutcome(parsed)).toEqual({ variant: "neutral", labelKey: "legacy" });
  });

  it("評価できなかった結果は指標を持たず、未完了として扱う", () => {
    const parsed = parseAnswerEvaluation({ status: "error", message: "評価を完了できませんでした。" });

    expect(parsed?.metrics).toEqual([]);
    expect(parsed?.legacy).toBe(false);
    expect(parsed && evaluationOutcome(parsed).labelKey).toBe("notCompleted");
    expect(parseAnswerEvaluation(null)).toBeNull();
  });
});
