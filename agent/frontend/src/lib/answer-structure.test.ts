import { describe, expect, it } from "vitest";

import { answerEvidenceData } from "@/lib/answer-evidence";
import { validationReview } from "@/lib/answer-review";
import { noticeHeading, structureAnswer } from "@/lib/answer-structure";

// #2328: Agent の回答を RAG のチャットと同じ節に組み立てる（表示だけ。本文の文は書き換えない）。内容は合成のデータ。

const TITLES = { confirmed: "確認できた", unconfirmed: "確認できなかった" };
const HEADING = "資料で確認できず除いた内容";

function evidence(passages: { text: string; chunk_ids: string[] }[], markers: { text: string; chunk_ids: string[] }[] = []) {
  return answerEvidenceData({
    items: [
      { document_id: "d1", chunk_id: "c1", file_name: "文書 A.pdf", excerpt: "x", locator: { page_start: 3 }, cited: true },
      { document_id: "d2", chunk_id: "c2", file_name: "文書 B.pdf", excerpt: "y", locator: { section_path: ["第 1 章"] }, cited: true },
    ],
    passages,
    markers,
  });
}

describe("structureAnswer", () => {
  it("要約の段落と、根拠の付いた項目の「確認できた」節、末尾の節を「確認できなかった」節にする", () => {
    const answer = [
      "答えは次のとおりです。",
      "",
      "- 申請は 30 日前です【証拠 ID: c1】。",
      "- 承認は部署が行います。",
      "",
      `**${HEADING}**`,
      "",
      "根拠で確かめられなかった次の内容は、回答に載せていません。",
      "- 根拠で確かめられない: 「延長は 2 回です。」",
    ].join("\n");
    const blocks = structureAnswer(
      answer,
      evidence(
        [
          { text: "- 申請は 30 日前です【証拠 ID: c1】。", chunk_ids: ["c1"] },
          { text: "- 承認は部署が行います。", chunk_ids: ["c2"] },
        ],
        [{ text: "【証拠 ID: c1】", chunk_ids: ["c1"] }]
      ),
      HEADING,
      TITLES
    );
    expect(blocks).toEqual([
      { kind: "paragraph", key: "b0", text: "答えは次のとおりです。", chunkIds: [] },
      {
        kind: "section",
        key: "confirmed",
        title: "確認できた",
        ordered: false,
        entries: [
          { key: "b1-0", text: "申請は 30 日前です。", chunkIds: ["c1"] },
          { key: "b1-1", text: "承認は部署が行います。", chunkIds: ["c2"] },
        ],
      },
      {
        kind: "section",
        key: "notice",
        title: "確認できなかった",
        ordered: false,
        entries: [
          { key: "n0", text: "根拠で確かめられなかった次の内容は、回答に載せていません。", chunkIds: [], muted: true },
          { key: "n1", text: "根拠で確かめられない: 「延長は 2 回です。」", chunkIds: [] },
        ],
      },
    ]);
  });

  it("回答の見出しは残し、根拠の無い段落が後ろにあれば「確認できた」の見出しを足さない", () => {
    const answer = "**結論**\n期限は 30 日です。\n\n補足として、資料に記載はありません。";
    const blocks = structureAnswer(answer, evidence([{ text: "期限は 30 日です。", chunk_ids: ["c1"] }]), null, TITLES);
    expect(blocks?.map((block) => (block.kind === "section" ? block.title : block.kind))).toEqual(["結論", "paragraph"]);
    const plain = structureAnswer(
      "期限は 30 日です。\n\n資料に記載はありません。",
      evidence([{ text: "期限は 30 日です。", chunk_ids: ["c1"] }]),
      null,
      TITLES
    );
    expect(plain?.map((block) => block.kind)).toEqual(["paragraph", "paragraph"]);
  });

  it("表は Markdown の塊のまま、根拠を結び付けられなければ null", () => {
    const table = "| 項目 | 値 |\n|---|---|\n| 期限 | 30 日 |";
    const blocks = structureAnswer(`期限は 30 日です。\n\n${table}`, evidence([{ text: "期限は 30 日です。", chunk_ids: ["c1"] }]), null, TITLES);
    expect(blocks?.[1]).toMatchObject({ kind: "markdown", text: table });
    expect(structureAnswer("期限は 30 日です。", evidence([]), null, TITLES)).toBeNull();
    expect(structureAnswer("期限は 30 日です。", null, null, TITLES)).toBeNull();
  });

  it("節の見出しは最終の検証の成果物の notice から読む", () => {
    expect(noticeHeading({ notice: { heading: HEADING } })).toBe(HEADING);
    expect(noticeHeading({})).toBeNull();
    expect(noticeHeading(null)).toBeNull();
  });
});

describe("answerEvidenceData", () => {
  it("根拠の行の場所は頁、無ければ節", () => {
    const data = evidence([]);
    expect(data.entries.map((entry) => entry.citationLocation)).toEqual(["p.3", "第 1 章"]);
    expect(data.entries[0].item).toMatchObject({ fileName: "文書 A.pdf", usedInAnswer: true, routeBadges: ["Evidence"] });
  });
});

describe("validationReview", () => {
  it("回答の本文の節に書いた項目は、回答の確かめに出し直さない（残りだけ）", () => {
    const claims = Array.from({ length: 7 }, (_, index) => ({ answer_quote: `段落 ${index}`, status: "unsupported", reason: "r" }));
    const content = {
      status: "completed",
      result: { status: "completed", valid: false, claims, findings: [], stale_evidence: [{ chunk_id: "c" }], missing_evidence: [] },
      withheld: { claims: 7, findings: 0, all: false },
    };
    expect(validationReview(content)?.points).toHaveLength(8);
    const listed = validationReview({ ...content, notice: { claims: 5, findings: 0, evidence: true, heading: HEADING } });
    expect(listed?.points.map((point) => (point.kind === "claim" ? point.quote : point.kind))).toEqual(["段落 5", "段落 6"]);
  });
});
