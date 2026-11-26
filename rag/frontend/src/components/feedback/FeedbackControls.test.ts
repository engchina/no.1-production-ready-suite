import { describe, expect, it } from "vitest";

import {
  buildFeedbackContentSnapshot,
} from "./FeedbackControls";

describe("feedback submission payload", () => {
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

  it("skips results without an answer", () => {
    expect(buildFeedbackContentSnapshot("質問", "  ", [])).toBeNull();
    expect(buildFeedbackContentSnapshot(" ", "回答", [])).toBeNull();
  });
});
