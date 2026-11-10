import { describe, expect, it } from "vitest";

import { t } from "@/lib/i18n";

import { negativeQuestionsError, parseNegativeQuestions } from "./ApprovedFaqManager.logic";

describe("当てはめない質問の欄", () => {
  it("1 行に 1 つとして読み、空の行と前後の空白を除く", () => {
    expect(parseNegativeQuestions("合成の質問 A\n\n  合成の質問 B  \n")).toEqual(["合成の質問 A", "合成の質問 B"]);
    expect(parseNegativeQuestions("  \n")).toEqual([]);
  });

  it("件数と長さの上限を超えると、欄の下に出す文を返す", () => {
    const many = Array.from({ length: 21 }, (_, index) => `合成の質問 ${index}`);
    expect(negativeQuestionsError(many)).toBe(t("searchAnswerProfiles.faq.negative.tooMany"));
    expect(negativeQuestionsError(many.slice(0, 20))).toBeNull();
    expect(negativeQuestionsError(["あ".repeat(1001)])).toBe(t("searchAnswerProfiles.faq.negative.tooLong"));
    expect(negativeQuestionsError(["あ".repeat(1000)])).toBeNull();
  });
});
