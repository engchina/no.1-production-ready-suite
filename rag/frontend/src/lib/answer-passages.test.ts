import { describe, expect, it } from "vitest";

import { answerPassageLabels, answerPassages } from "./answer-passages";

// #2738: 回答の記録の診断の段落の印・外した段落・回答全体の注意を読む（Agent と同じ共有の部品で描く）。内容は合成。

describe("answerPassages", () => {
  it("診断の 3 つの項目を読み、記録の無い過去の回答は null", () => {
    const records = answerPassages({
      outcome: "conditional",
      passage_marks: [{ text: "合成の段落。", state: "unverified", reasons: [{ code: "unsupported", label: "根拠で確かめられない" }] }],
      removed_passages: [],
      answer_warnings: [{ code: "off_goal", label: "質問からずれている可能性", detail: "" }],
    });
    expect(records?.marks).toEqual([
      { text: "合成の段落。", state: "unverified", reasons: [{ code: "unsupported", label: "根拠で確かめられない", detail: "" }] },
    ]);
    expect(records?.warnings).toHaveLength(1);
    expect(answerPassages({ outcome: "answered", confidence: "high" })).toBeNull();
    expect(answerPassages(null)).toBeNull();
    expect(answerPassages([])).toBeNull();
  });

  it("回答の構造（envelope）に置かれた記録も読む", () => {
    const records = answerPassages({ envelope: { removed_passages: [{ text: "合成の段落。", reasons: [] }] } });
    expect(records?.removed).toHaveLength(1);
  });

  it("文言は Agent と同じ（未確認・要注意・件数のバッジ）", () => {
    const labels = answerPassageLabels();
    expect(labels.state).toEqual({ unverified: "未確認", warning: "要注意" });
    expect(labels.badge.removed(2)).toBe("矛盾で除外 2 件");
    expect(labels.jumpLabel("「合成」")).toBe("「合成」（本文の段落へ移動）");
  });
});
