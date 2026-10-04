import { describe, expect, it } from "vitest";

import { ApiError, type EvaluationJob } from "@/lib/api";
import { evaluationJobRefetchInterval } from "@/lib/queries";

import { evaluationCaseIdError } from "./evaluation-input";

describe("evaluationCaseIdError", () => {
  it("id がそろっていれば null", () => {
    expect(evaluationCaseIdError([{ id: "a" }, { id: "b" }])).toBeNull();
  });

  it("空・空白・文字列でない・長すぎる id は何件目かを示す", () => {
    expect(evaluationCaseIdError([{ id: "a" }, { id: "" }])).toContain("2 件目");
    expect(evaluationCaseIdError([{ id: "  " }])).toContain("1 件目");
    expect(evaluationCaseIdError([{ id: 1 }])).toContain("1 件目");
    expect(evaluationCaseIdError([{ query: "q" }])).toContain("1 件目");
    expect(evaluationCaseIdError([{ id: "x".repeat(201) }])).toContain("1 件目");
    expect(evaluationCaseIdError(["case"])).toContain("1 件目");
  });

  it("重複する id を 1 回ずつ挙げる（前後の空白は同じ id とみなす）", () => {
    const error = evaluationCaseIdError([{ id: "a" }, { id: "a " }, { id: "b" }, { id: "a" }]);
    expect(error).toContain("重複しています: a。");
  });
});

describe("evaluationJobRefetchInterval", () => {
  const running = { status: "RUNNING" } as EvaluationJob;

  it("実行中は取得の失敗の後も取得を続け、404 と終わった job では止める", () => {
    expect(evaluationJobRefetchInterval(running)).toBeGreaterThan(0);
    expect(evaluationJobRefetchInterval(running, new ApiError(503, ["x"]))).toBeGreaterThan(0);
    expect(evaluationJobRefetchInterval(running, new TypeError("Failed to fetch"))).toBeGreaterThan(0);
    expect(evaluationJobRefetchInterval(running, new ApiError(404, ["x"]))).toBe(false);
    expect(evaluationJobRefetchInterval({ status: "SUCCEEDED" } as EvaluationJob)).toBe(false);
    expect(evaluationJobRefetchInterval(undefined)).toBe(false);
  });
});
