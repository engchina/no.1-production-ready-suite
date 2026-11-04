import { describe, expect, it } from "vitest";

import { answerPassageLabels, answerPassages, projectAnswerPassageSections } from "./answer-passages";

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
    expect(labels).not.toHaveProperty("badge");
    expect(labels.jumpLabel("「合成」")).toBe("「合成」（本文の段落へ移動）");
  });
});

describe("projectAnswerPassageSections", () => {
  const empty = { marks: [], removed: [], warnings: [] };
  it("空の診断配列でも本文だけの未確認事項を失わず、後続の別の節を取り込まない", () => {
    const result = projectAnswerPassageSections("合成の回答。\n\n確認が必要な点\n\n・合成の未確認事項\n\n資料から確認できなかった事項\n\n・別の不足事項", empty);
    expect(result.notes.marks).toEqual(["合成の未確認事項"]);
    expect(result.body).toBe("合成の回答。\n\n資料から確認できなかった事項\n\n・別の不足事項");
  });
  it("記録から再構成できる行だけ除き、混在した本文だけの注意を保持する", () => {
    const records = { marks: [{text: "合成段落。", state: "unverified" as const, reasons: [{code:"unsupported",label:"未確認",detail:""}]}], removed: [], warnings: [{code:"off_goal",label:"合成警告",detail:"合成詳細"}] };
    const result = projectAnswerPassageSections("確認が必要な点\n\n次の点は根拠で確かめきれていないか、注意が必要です。\n・未確認: 「合成段落。」\n・合成警告: 合成詳細\n・原文だけの注意", records);
    expect(result.body).toBe("");
    expect(result.notes.marks).toEqual(["原文だけの注意"]);
  });
  it("保存した形式を使い、未知の行を記録の信頼判定へ変換しない", () => {
    const result = projectAnswerPassageSections("保存した注意\n+合成の未確認事項", empty, {marked_section:"保存した注意",item_bullet:"+"});
    expect(result.notes.marks).toEqual(["合成の未確認事項"]);
    expect(empty.marks).toEqual([]);
  });
  it("項目のない見出しの次に別の節があっても削除しない", () => {
    expect(projectAnswerPassageSections("確認が必要な点\n\n資料により確認できた事項\n\n・合成の事実", empty).body).toContain("資料により確認できた事項");
  });
});


it("外した内容も記録から再構成できる行だけを除く", () => {
  const result = projectAnswerPassageSections("合成の回答。\n\n根拠と矛盾し除いた内容\n\n次の内容は根拠と矛盾するため、回答から除きました。\n・根拠と矛盾: 「合成の段落。」\n・本文だけの補足\n\n（対象: 合成範囲）", {marks:[],warnings:[],removed:[{text:"合成の段落。",reasons:[{code:"contradicted",label:"根拠と矛盾",detail:""}]}]});
  expect(result.body).toBe("合成の回答。\n\n（対象: 合成範囲）");
  expect(result.notes.removed).toEqual(["本文だけの補足"]);
});
