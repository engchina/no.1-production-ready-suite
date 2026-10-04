import { describe, expect, it } from "vitest";

import {
  blocklistFromText,
  queryHistoryErrors,
  retentionOptions,
  shortensRetention,
} from "./retrieval-settings.logic";

// #1002: 検索方法の画面の質問履歴・回答の記録の保存期間の検証は backend と同じ範囲にする。
describe("質問履歴の欄の検証", () => {
  const valid = { minCount: "3", suggestionLimit: "5", blocklistText: "" };

  it("範囲内の整数ならエラーにしない", () => {
    expect(queryHistoryErrors(valid)).toEqual({ minCount: null, suggestionLimit: null, blocklist: null });
    expect(queryHistoryErrors({ ...valid, minCount: "1000", suggestionLimit: "20" }).minCount).toBeNull();
  });

  it("空を 0 にせず、入力を求める", () => {
    expect(queryHistoryErrors({ ...valid, minCount: "" }).minCount).toBe(
      "候補にする最小回数を入力してください。"
    );
    expect(queryHistoryErrors({ ...valid, suggestionLimit: " " }).suggestionLimit).toBe(
      "候補の最大件数を入力してください。"
    );
  });

  it("範囲外・小数は backend と同じ範囲を示す", () => {
    expect(queryHistoryErrors({ ...valid, minCount: "1001" }).minCount).toBe(
      "候補にする最小回数は 1 以上 1,000 以下の整数を入力してください。"
    );
    expect(queryHistoryErrors({ ...valid, minCount: "1.5" }).minCount).toContain("整数");
    expect(queryHistoryErrors({ ...valid, suggestionLimit: "21" }).suggestionLimit).toBe(
      "候補の最大件数は 1 以上 20 以下の整数を入力してください。"
    );
    expect(queryHistoryErrors({ ...valid, suggestionLimit: "0" }).suggestionLimit).toContain("1 以上");
  });

  it("除外する語は空行・重複を除いて 200 語まで", () => {
    const words = (count: number) => Array.from({ length: count }, (_, index) => `語${index}`).join("\n");
    expect(queryHistoryErrors({ ...valid, blocklistText: words(200) }).blocklist).toBeNull();
    expect(queryHistoryErrors({ ...valid, blocklistText: `${words(200)}\n語0\n\n` }).blocklist).toBeNull();
    expect(queryHistoryErrors({ ...valid, blocklistText: words(201) }).blocklist).toBe(
      "保存・表示しない語は 200 語までです（今は 201 語）。"
    );
  });

  it("除外する語は前後の空白・空行・重複を除く", () => {
    expect(blocklistFromText("給与\n\n 住所 \n給与")).toEqual(["給与", "住所"]);
  });
});

describe("保存期間", () => {
  it("短くする（無期限から期限ありにするのを含む）ときだけ確認の対象にする", () => {
    expect(shortensRetention(90, 30)).toBe(true);
    expect(shortensRetention(0, 365)).toBe(true);
    expect(shortensRetention(30, 90)).toBe(false);
    expect(shortensRetention(90, 0)).toBe(false);
    expect(shortensRetention(90, 90)).toBe(false);
  });

  it("既定に無い保存値は「N 日」として選択肢に足す", () => {
    expect(retentionOptions(60).map((option) => option.label)).toEqual([
      "30 日",
      "90 日",
      "180 日",
      "365 日",
      "無期限（手動で削除）",
      "60 日",
    ]);
    expect(retentionOptions(90)).toHaveLength(5);
  });
});
