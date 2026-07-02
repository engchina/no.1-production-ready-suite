import { describe, expect, it } from "vitest";

import { groundingUseCaseLabel } from "./GroundingSettingsClient";

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
