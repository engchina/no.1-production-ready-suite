import { describe, expect, it } from "vitest";

import { graphPathLabel } from "../src/lib/graph-path-label";

describe("graphPathLabel", () => {
  it("区切りの無い名前はそのまま主な表示名にする", () => {
    expect(graphPathLabel("見出し", " > ")).toEqual({ primary: "見出し" });
  });

  it("2 段のパスは親をそのまま文脈にする", () => {
    expect(graphPathLabel("親 > 子", " > ")).toEqual({ primary: "子", context: "親" });
  });

  it("3 段以上は最後の段と直近の親を残し、上の段を省略の印にする", () => {
    const common = "長い文書名".repeat(40);
    expect(graphPathLabel(`${common} > 第1章 > 第1条`, " > ")).toEqual({ primary: "第1条", context: "… > 第1章" });
    expect(graphPathLabel(`${common} > 第1章 > 第2条`, " > ").primary).toBe("第2条");
    expect(graphPathLabel(`${common} > 第2章 > 第1条`, " > ").context).toBe("… > 第2章");
  });

  it("空の段を除き、区切りだけの名前は元の文字列のまま返す", () => {
    expect(graphPathLabel("親 >  > 子 > ", " > ")).toEqual({ primary: "子", context: "親" });
    expect(graphPathLabel(" > ", " > ")).toEqual({ primary: " > " });
  });
});
