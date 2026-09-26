import { describe, expect, it } from "vitest";

import { buildFeedbackContentSnapshot, buildFeedbackPayload } from "./FeedbackControls";

describe("feedback submission payload", () => {
  it("keeps the selected low-rating reason and optional comment", () => {
    const payload = buildFeedbackPayload({
      trace_id: "trace-1",
      business_view_id: "bv-1",
      target_type: "answer",
      source_surface: "search",
      rating: "not_helpful",
      reason: "outdated_source",
      comment: "  古い回答です。  ",
      corrected_answer: "  2026年版では部長承認です。  ",
    });
    expect(payload).toMatchObject({
      reason: "outdated_source",
      comment: "古い回答です。",
      corrected_answer: "2026年版では部長承認です。",
    });
  });

  it("removes low-rating fields from a helpful vote", () => {
    const payload = buildFeedbackPayload({
      trace_id: "trace-1",
      business_view_id: "bv-1",
      target_type: "answer",
      source_surface: "search",
      rating: "helpful",
      reason: "incorrect",
      comment: "コメント",
      corrected_answer: "修正",
    });
    expect(payload.reason).toBeNull();
    expect(payload.comment).toBeNull();
    expect(payload.corrected_answer).toBeNull();
  });

  it("maps the visible search result into a bounded snapshot", () => {
    const snapshot = buildFeedbackContentSnapshot(" 質問 ", " 回答 ", [
      {
        document_id: "doc-1",
        chunk_id: "chunk-1",
        text: "根拠本文",
        score: 0.8,
        rerank_score: 0.9,
        file_name: "規程.pdf",
        category_name: null,
        metadata: { section_title: "申請", page: 3 },
      },
    ]);
    expect(snapshot).toEqual({
      question: "質問",
      answer: "回答",
      citations: [
        {
          document_id: "doc-1",
          chunk_id: "chunk-1",
          file_name: "規程.pdf",
          section_title: "申請",
          page_number: 3,
          content_preview: "根拠本文",
          rerank_score: 0.9,
        },
      ],
    });
  });
});
