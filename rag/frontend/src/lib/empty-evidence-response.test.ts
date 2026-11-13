import { describe, expect, it } from "vitest";
import { emptyEvidenceResponsePreview, effectiveEmptyEvidenceResponsePreview, isEmptyEvidenceResponseField } from "./empty-evidence-response";
import { overridesFromTexts } from "./language-pack";
import type { LanguagePackField } from "./api";

const fields: LanguagePackField[] = ["insufficient_evidence_summary", "empty_search_guidance"].map((key) => ({
  section: "answer_labels", key, kind: "text", choices: [], ingest: false, max_chars: 1000,
}));

describe("根拠なし固定返信の基本本文", () => {
  it("プロファイルは未編集の継承値と保存済み上書きを再度フロントの trim に通さない", () => {
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
    expect(emptyEvidenceResponsePreview({ "answer_labels.insufficient_evidence_summary": input })).toBe(expected);
  });

  it("保存される2項目だけを順番どおりに連結し、区切りや追加案内を入れない", () => {
    const texts = {
      "answer_labels.insufficient_evidence_summary": "  合成の前置き。 ",
      "answer_labels.empty_search_guidance": "\n合成の確認案内。\n",
      "answer_notice.text": "含めない案内",
    };
    const saved = overridesFromTexts(fields, texts, {});
    expect(emptyEvidenceResponsePreview(texts)).toBe(
      String(saved.answer_labels.insufficient_evidence_summary) + String(saved.answer_labels.empty_search_guidance),
    );
    expect(emptyEvidenceResponsePreview(texts)).toBe("合成の前置き。合成の確認案内。");
  });

  it("空欄を別の固定文で補わず、他の節にある同名項目を集中欄に含めない", () => {
    expect(emptyEvidenceResponsePreview({})).toBe("");
    expect(isEmptyEvidenceResponseField(fields[0])).toBe(true);
    expect(isEmptyEvidenceResponseField({ section: "other", key: fields[0].key })).toBe(false);
    expect(isEmptyEvidenceResponseField({ section: "answer_labels", key: "other" })).toBe(false);
  });
});
