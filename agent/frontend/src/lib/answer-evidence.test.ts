import { describe, expect, it } from "vitest";

import { answerEvidenceData } from "@/lib/answer-evidence";

describe("回答の根拠", () => {
  it("会話の添付の根拠は「添付ファイル」の印を付け、原文のプレビューを開かない", () => {
    const data = answerEvidenceData({
      items: [
        {
          document_id: "doc-a",
          chunk_id: "doc-a:cs:1",
          file_name: "文書 A.pdf",
          excerpt: "合成の本文。",
          locator: { page_start: 3, page_end: 3 },
          cited: true,
        },
        {
          document_id: "attachment:att-1",
          chunk_id: "attachment:att-1:table-b:1-2",
          file_name: "table-b.csv",
          excerpt: "code: A / amount: 100",
          locator: { sheet_name: "table-b", row_start: 1, row_end: 2 },
          chat_attachment_id: "att-1",
          cited: false,
        },
      ],
    });
    const [rag, attachment] = data.entries;
    expect(rag.attachment).toBe(false);
    expect(rag.item.sourceUnavailable).toBe(false);
    expect(rag.item.tags).toEqual([]);
    expect(attachment.attachment).toBe(true);
    expect(attachment.item.sourceUnavailable).toBe(true);
    expect(attachment.item.tags?.map((tag) => tag.value)).toEqual(["添付ファイル"]);
    expect(attachment.item.usedInAnswer).toBe(false);
    expect(attachment.citationLocation).toBe("table-b · 1–2 行");
  });

  it("利用者が選んだ登録済みQ&Aの根拠は「登録済みQ&A」の印を付け、原文のプレビューを開かない", () => {
    const data = answerEvidenceData({
      items: [
        {
          document_id: "approved_faq:faq-1",
          chunk_id: "approved_faq:faq-1",
          file_name: "登録済みQ&A",
          excerpt: "合成の回答 A",
          section_title: "合成の質問 A",
          approved_faq_id: "faq-1",
          cited: true,
        },
      ],
    });
    const [faq] = data.entries;
    expect(faq.approvedFaq).toBe(true);
    expect(faq.attachment).toBe(false);
    expect(faq.item.sourceUnavailable).toBe(true);
    expect(faq.item.tags?.map((tag) => tag.value)).toEqual(["登録済みQ&A"]);
    expect(faq.item.usedInAnswer).toBe(true);
    expect(faq.citationLocation).toBe("合成の質問 A");
  });
});
