import { describe, expect, it } from "vitest";

import { groundingUseCaseLabel, parseNumberInput, validateGroundingForm } from "./GroundingSettingsClient";

describe("groundingUseCaseLabel", () => {
  it("推奨用途 token を日本語化し、未知値を露出しない", () => {
    const expected = {
      advanced: "高度な設定",
      manual: "手動調整",
      low_latency: "低遅延",
      simple: "シンプル",
      general: "汎用",
      balanced: "バランス",
      multi_page: "複数ページ",
      dependency: "依存関係",
      token_budget: "トークン節約",
      long_context: "長文コンテキスト",
      compliance: "コンプライアンス",
      max_quality: "最高品質",
    };

    for (const [token, label] of Object.entries(expected)) {
      expect(groundingUseCaseLabel(token)).toBe(label);
    }
    expect(groundingUseCaseLabel("future_internal_token")).toBe("その他");
  });
});

describe("parseNumberInput", () => {
  it("空欄を 0 ではなく未入力(NaN)として扱う", () => {
    expect(parseNumberInput("")).toBeNaN();
    expect(parseNumberInput("  ")).toBeNaN();
    expect(parseNumberInput("0")).toBe(0);
    expect(parseNumberInput("0.45")).toBe(0.45);
  });
});

describe("validateGroundingForm（欄ごとのエラー）", () => {
  const base = {
    grounding_pipeline: "balanced",
    crag_enabled: true,
    crag_low_confidence_threshold: 0.3,
    crag_high_confidence_threshold: 0.7,
    crag_max_hops: 1,
    crag_low_evidence_abstain: false,
  } as unknown as Parameters<typeof validateGroundingForm>[0];

  it("正しい値ではエラーを返さない", () => {
    expect(Object.values(validateGroundingForm(base)).filter(Boolean)).toEqual([]);
  });

  it("空は 0 として扱わず、欄ごとに「〇〇を入力してください。」を返す", () => {
    const errors = validateGroundingForm({ ...base, crag_low_confidence_threshold: Number.NaN });
    expect(errors.crag_low_confidence_threshold).toBe("低しきい値を入力してください。");
    expect(errors.crag_high_confidence_threshold).toBeNull();
  });

  it("範囲外・大小の関係・整数でない値を、その欄のエラーにする", () => {
    const errors = validateGroundingForm({
      ...base,
      crag_high_confidence_threshold: 0.1,
      crag_max_hops: 1.5,
    });
    expect(errors.crag_high_confidence_threshold).toBe("高しきい値は低しきい値以上の数値を入力してください。");
    expect(errors.crag_max_hops).toBe("再検索の上限回数は 0 以上 3 以下の整数を入力してください。");
    expect(validateGroundingForm({ ...base, crag_low_confidence_threshold: 1.2 }).crag_low_confidence_threshold).toBe(
      "低しきい値は 0 以上 1 以下の数値を入力してください。",
    );
  });
});
