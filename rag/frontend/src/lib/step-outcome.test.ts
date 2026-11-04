import { describe, expect, it } from "vitest";
import { executionStepDetails } from "./step-outcome";

describe("実行判断の投影", () => {
  it("設定無効は判断時間と理由を日本語で表示する", () => {
    const details = executionStepDetails("skipped", {reason_code: "configuration_disabled"}, undefined);
    expect(details?.fields).toEqual([
      {label: "結果", value: "設定がオフのため実行しません。"},
      {label: "計測対象", value: "選択処理の実行可否を判断した時間です。"},
    ]);
  });
  it("実行済み0件は省略と区別し、既存の詳細を残す", () => {
    const details = executionStepDetails("done", {reason_code: "no_match", result_count: 0}, {fields: [{label: "結果", value: "旧要約"}, {label: "設定", value: "合成の値"}]});
    expect(details?.fields).toEqual([{label: "結果", value: "該当する候補はありません。"}, {label: "設定", value: "合成の値"}]);
  });
  it("失敗した記録の再利用を成功へ変換しない", () => {
    const details = executionStepDetails("failed", {reason_code: "selection_failed", reused: true}, undefined);
    expect(details?.fields?.[0].value).toBe("既存の結果を再利用しました。取得に失敗したため、既存の候補を保持して続けます。");
  });
  it("件数の記録が無ければ0を補わない", () => {
    expect(executionStepDetails("done", {reason_code: "candidates_found"}, undefined)?.fields?.[0].value).toBe("返した候補の件数は未記録です。");
  });
  it("旧記録と未知の理由はそのまま保持し推測しない", () => {
    const details = {lines: ["合成の旧記録"]};
    expect(executionStepDetails("done", {}, details)).toBe(details);
    expect(executionStepDetails("done", {reason_code: "unknown"}, details)).toBe(details);
    expect(executionStepDetails("done", {}, undefined)).toBeUndefined();
  });
});
