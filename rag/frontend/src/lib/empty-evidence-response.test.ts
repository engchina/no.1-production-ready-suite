import { describe, expect, it } from "vitest";
import { effectiveEmptyEvidenceResponsePreview, isEmptyEvidenceResponseField, withBuiltinEmptyEvidenceResponse } from "./empty-evidence-response";
import { overridesFromTexts } from "./language-pack";
import type { LanguagePackField } from "./api";

const fields: LanguagePackField[] = ["insufficient_evidence_summary", "empty_search_guidance"].map((key) => ({
  section: "answer_labels", key, kind: "text", choices: [], ingest: false, max_chars: 1000,
}));

describe("根拠なし固定返信の基本本文", () => {
  it("プロファイルは未編集の組み込みの値と保存済み上書きを再度フロントの trim に通さない", () => {
    const baseline = { answer_labels: { insufficient_evidence_summary: "\uFEFFA\uFEFF", empty_search_guidance: "G" } };
    expect(effectiveEmptyEvidenceResponsePreview(baseline)).toBe("\uFEFFA\uFEFFG");
    expect(effectiveEmptyEvidenceResponsePreview(baseline, { answer_labels: { insufficient_evidence_summary: "\uFEFFB\uFEFF" } })).toBe("\uFEFFB\uFEFFG");
    const payload = overridesFromTexts(fields, {
      "answer_labels.insufficient_evidence_summary": "\u0085\uFEFFC\uFEFF\u0085",
      "answer_labels.empty_search_guidance": "G",
    }, baseline);
    expect(effectiveEmptyEvidenceResponsePreview(baseline, payload)).toBe("\uFEFFC\uFEFFG");
  });

  it.each([
    ["\u0085A\u0085", "A"],
    ["\u001c\u001dA\u001e\u001f", "A"],
    ["\uFEFFA\uFEFF", "A"],
    ["\u200bA\u200b", "\u200bA\u200b"],
    ["\u0085\uFEFFA\uFEFF\u0085", "\uFEFFA\uFEFF"],
    [" A\u0085\u001c\uFEFFB\nC ", "A\u0085\u001c\uFEFFB\nC"],
    ["\u0085\u001c\u001f", ""],
  ])("保存の二段階の空白処理と内部文字を再現する %#", (input, expected) => {
    const saved = overridesFromTexts(fields.slice(0, 1), { "answer_labels.insufficient_evidence_summary": input }, {});
    expect(effectiveEmptyEvidenceResponsePreview({}, saved)).toBe(expected);
  });

  it("保存される2項目だけを順番どおりに連結し、区切りや追加案内を入れない", () => {
    const texts = {
      "answer_labels.insufficient_evidence_summary": "  合成の前置き。 ",
      "answer_labels.empty_search_guidance": "\n合成の確認案内。\n",
      "answer_notice.text": "含めない案内",
    };
    const saved = overridesFromTexts(fields, texts, {});
    expect(effectiveEmptyEvidenceResponsePreview({}, saved)).toBe("合成の前置き。合成の確認案内。");
  });

  it("空欄を別の固定文で補わず、他の節にある同名項目を集中欄に含めない", () => {
    expect(effectiveEmptyEvidenceResponsePreview({})).toBe("");
    expect(isEmptyEvidenceResponseField(fields[0])).toBe(true);
    expect(isEmptyEvidenceResponseField({ section: "other", key: fields[0].key })).toBe(false);
    expect(isEmptyEvidenceResponseField({ section: "answer_labels", key: "other" })).toBe(false);
  });

  it("プロファイルの土台は固定返信の 2 項目だけ組み込みの値にし、全体の値を継承しない", () => {
    const effective = { answer_labels: { insufficient_evidence_summary: "全体の前置き。", empty_search_guidance: "全体の案内。", other: "全体の別項目。" }, question_cues: { words: ["語"] } };
    const builtin = { answer_labels: { insufficient_evidence_summary: "組み込みの前置き。", empty_search_guidance: "組み込みの案内。", other: "組み込みの別項目。" } };
    expect(withBuiltinEmptyEvidenceResponse(effective, builtin)).toEqual({
      answer_labels: { insufficient_evidence_summary: "組み込みの前置き。", empty_search_guidance: "組み込みの案内。", other: "全体の別項目。" },
      question_cues: { words: ["語"] },
    });
  });
});
