import { describe, expect, it } from "vitest";

import {
  formatRunElapsed,
  runOutcomeView,
  runProfileLabel,
  runConversationOwnerLabel,
  runExecutedByNote,
  runExecutorFilterLabel,
  runExecutorLabel,
  runStatusView,
  runVerificationView,
  RUN_EXECUTOR_FILTERS,
} from "./run-history-view";

describe("実行履歴の表示の対応表", () => {
  it("状態は色だけでなく文言でも分ける", () => {
    expect(runStatusView("success")).toEqual({ variant: "success", label: "完了" });
    expect(runStatusView("blocked")).toEqual({ variant: "warning", label: "安全チェックで停止" });
    expect(runStatusView("error")).toEqual({ variant: "danger", label: "失敗" });
  });

  it("回答の対応は、検索だけ・止めた実行・回答した実行で分ける", () => {
    expect(runOutcomeView({ answer_generated: false, status: "success", answer_outcome: null }).label).toBe(
      "回答なし（検索のみ）"
    );
    expect(runOutcomeView({ answer_generated: true, status: "blocked", answer_outcome: null }).label).toBe(
      "回答なし"
    );
    expect(runOutcomeView({ answer_generated: true, status: "success", answer_outcome: "answered" })).toEqual({
      variant: "success",
      label: "回答",
    });
    expect(
      runOutcomeView({ answer_generated: true, status: "success", answer_outcome: "insufficient_evidence" })
    ).toEqual({ variant: "danger", label: "根拠不足" });
  });

  it("回答の検証は 3 段階で、回答が無ければ出さない", () => {
    expect(runVerificationView("verified")?.label).toBe("検証済み");
    expect(runVerificationView("partial")?.variant).toBe("warning");
    expect(runVerificationView("unverified")?.variant).toBe("neutral");
    expect(runVerificationView(null)).toBeNull();
  });

  it("所要時間は秒、1 分以上は分と秒", () => {
    expect(formatRunElapsed(1234)).toBe("1.2 秒");
    expect(formatRunElapsed(125_400)).toBe("2 分 5 秒");
    expect(formatRunElapsed(null)).toBe("—");
  });

  it("検索・回答プロファイルと利用者は ID を出さずに名前で出す", () => {
    expect(runProfileLabel({ search_answer_profile_id: "bv-1", search_answer_profile_name: "受注" })).toBe("受注");
    expect(runProfileLabel({ search_answer_profile_id: "bv-1", search_answer_profile_name: null })).toBe(
      "（削除された検索・回答プロファイル）"
    );
    expect(runProfileLabel({ search_answer_profile_id: null, search_answer_profile_name: null })).toBe(
      "検索・回答プロファイルなし"
    );
    expect(runExecutorLabel({ executor_display_name: "山田" })).toBe("山田");
    expect(runExecutorLabel({ executor_display_name: "" })).toBe("（削除済みのユーザー）");
  });

  it("実行した人が自分でない行だけに「〈名前〉が実行」を出す", () => {
    expect(runExecutedByNote({ executed_by_me: true, executor_display_name: "山田" })).toBeNull();
    expect(runExecutedByNote({ executed_by_me: false, executor_display_name: "管理者" })).toBe("管理者 が実行");
    expect(runExecutedByNote({ executed_by_me: false, executor_display_name: "" })).toBe(
      "（削除済みのユーザー） が実行"
    );
  });

  it("実行者の絞り込みは すべて / 自分が実行 / 他の人が実行 の順（既定はすべて）", () => {
    expect(RUN_EXECUTOR_FILTERS.map(runExecutorFilterLabel)).toEqual(["すべて", "自分が実行", "他の人が実行"]);
  });

  it("会話の持ち主は会話の無い実行では出さない", () => {
    expect(runConversationOwnerLabel({ conversation_owner_display_name: null })).toBeNull();
    expect(runConversationOwnerLabel({ conversation_owner_display_name: "アリス" })).toBe("アリス");
    expect(runConversationOwnerLabel({ conversation_owner_display_name: "" })).toBe("（削除済みのユーザー）");
  });
});
