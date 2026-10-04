import { describe, expect, it } from "vitest";

import { answerPromptError } from "./answer-prompt.logic";

// #1010: 編集できるプロンプトは backend の validate_prompt と同じ規則で、送る前に欄の下へ出す。
describe("編集できるプロンプトの欄の検証", () => {
  const placeholders = ["question", "images"];

  it("必須の placeholder がすべてあればエラーにしない", () => {
    expect(answerPromptError("独自 {{question}} {{images}}", "テンプレート", placeholders)).toBeNull();
  });

  it("空・空白だけは入力を求める", () => {
    expect(answerPromptError("  \n", "テンプレート", placeholders)).toBe("テンプレートを入力してください。");
  });

  it("欠けた placeholder を backend と同じ書き方で示す", () => {
    expect(answerPromptError("質問だけ {{question}}", "テンプレート", placeholders)).toBe(
      "必須の placeholder がありません: {{images}}"
    );
    expect(answerPromptError("何もない", "テンプレート", placeholders)).toBe(
      "必須の placeholder がありません: {{question}}、{{images}}"
    );
  });
});
