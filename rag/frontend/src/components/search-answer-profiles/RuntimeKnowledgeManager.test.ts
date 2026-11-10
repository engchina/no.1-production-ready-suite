import { describe, expect, it } from "vitest";

import { runtimeKnowledgeRequiredErrors } from "./RuntimeKnowledgeManager";

const term = { selected: null, name: "合成用語 A", labels: "合成語 A", content: "合成の説明。", enabled: true };

describe("用語・同義語の入力", () => {
  it("用語があれば保存できる", () => {
    expect(runtimeKnowledgeRequiredErrors(term).name).toBeNull();
  });
  it("用語の名前は必須にする", () => {
    expect(runtimeKnowledgeRequiredErrors({ ...term, name: " " }).name).toBe("用語を入力してください。");
  });
});
