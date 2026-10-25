import { chatProgressStepsFromEvents, chatProgressTimelineStep, parseChatProgressEvents } from "@production-ready/ui";
import { describe, expect, it } from "vitest";

import { CHAT_PROGRESS_DEFINITIONS } from "@/lib/chat-progress";

import {
  formatRunElapsed,
  runConversationOwner,
  runConversationOwnerLabel,
  runExecutorLabel,
  runExecutorParam,
  runOutcomeView,
  runProfileLabel,
  runStartedAt,
  runStatusLabels,
  runStatusParam,
  runTimeline,
  runVerificationView,
  RUN_STATUS_OPTIONS,
  toRunStatus,
} from "./run-history-view";

describe("実行履歴の表示の対応表", () => {
  it("RAG の状態を共通の状態に写し、絞り込みは API の値へ戻す", () => {
    expect(toRunStatus("success")).toBe("succeeded");
    expect(toRunStatus("blocked")).toBe("blocked");
    expect(toRunStatus("error")).toBe("failed");
    expect(RUN_STATUS_OPTIONS.map(runStatusParam)).toEqual(["success", "blocked", "error"]);
    expect(runStatusParam("all")).toBeUndefined();
    // ブロックは安全チェックで止めた実行（文言だけ RAG の言い方）。
    expect(runStatusLabels()).toEqual({ succeeded: "完了", blocked: "安全チェックで停止", failed: "失敗" });
  });

  it("実行者の絞り込みは共通の self を RAG の mine にする", () => {
    expect(runExecutorParam("all")).toBe("all");
    expect(runExecutorParam("self")).toBe("mine");
    expect(runExecutorParam("others")).toBe("others");
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

  it("資料との照合は 3 段階で、回答が無ければ出さない", () => {
    expect(runVerificationView("verified")?.label).toBe("照合済み");
    expect(runVerificationView("unverified")?.label).toBe("未照合");
    expect(runVerificationView("partial")?.variant).toBe("warning");
    expect(runVerificationView("unverified")?.variant).toBe("neutral");
    expect(runVerificationView(null)).toBeNull();
  });

  it("所要時間は共通の書式（記録なしは「—」）", () => {
    expect(formatRunElapsed(1234)).toBe("1.2 秒");
    expect(formatRunElapsed(null)).toBe("—");
  });

  it("開始日時は記録の時刻（終わった時刻）から所要時間を引く", () => {
    expect(runStartedAt({ created_at: "2026-10-15T09:00:05.000Z", elapsed_ms: 5000 })).toBe("2026-10-15T09:00:00.000Z");
    expect(runStartedAt({ created_at: "2026-10-15T09:00:05Z", elapsed_ms: null })).toBe("2026-10-15T09:00:05Z");
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

  it("会話の持ち主は会話の無い実行では出さない", () => {
    expect(runConversationOwnerLabel({ conversation_owner_display_name: null })).toBeNull();
    expect(runConversationOwnerLabel({ conversation_owner_display_name: "アリス" })).toBe("アリス");
    expect(runConversationOwnerLabel({ conversation_owner_display_name: "" })).toBe("（削除済みのユーザー）");
  });

  it("詳細の「会話の持ち主」は実行者と違うときだけ出す", () => {
    const base = { executor_display_name: "管理者", conversation_owner_display_name: "アリス" };
    // 自分が実行した自分の会話。
    expect(runConversationOwner({ ...base, executed_by_me: true, owned_by_me: true })).toBeNull();
    // 持ち主の会話で system_admin が実行した行（持ち主から見る）。
    expect(runConversationOwner({ ...base, executed_by_me: false, owned_by_me: true })).toEqual({
      name: "アリス",
      isSelf: true,
    });
    // 他の人の会話で自分（system_admin）が実行した行。
    expect(runConversationOwner({ ...base, executed_by_me: true, owned_by_me: false })).toEqual({
      name: "アリス",
      isSelf: false,
    });
    // 他の人が自分の会話で実行した行（system_admin から見る）は名前で比べる。
    expect(
      runConversationOwner({ ...base, executor_display_name: "アリス", executed_by_me: false, owned_by_me: false })
    ).toBeNull();
    expect(
      runConversationOwner({ ...base, conversation_owner_display_name: null, executed_by_me: true, owned_by_me: null })
    ).toBeNull();
  });
});

const TRACE_ID = "c".repeat(32);

/** span のイベント（backend の `ChatProgressRecorder.record_span` と同じ形）。 */
function spanEvent(seq: number, id: string, fields: Record<string, unknown>) {
  return {
    schema_version: 1, target_id: "synthetic", seq, attempt: 0, emitted_at: "2026-10-23T00:00:02Z",
    type: "step", step_id: id.repeat(16), kind: id.repeat(16), status: "done",
    started_at: "2026-10-23T00:00:00Z", finished_at: "2026-10-23T00:00:02Z", trace_id: TRACE_ID, ...fields,
  };
}

it("処理の経過は実行の trace の API の snapshot の段を、操作画面と同じ組み立てで木にする（Issue 3226）", () => {
  const raw = [
    spanEvent(1, "1", { name: "rag.answer", root: true }),
    spanEvent(2, "2", { name: "rag.answer", parent_id: "1".repeat(16), attributes: { "pr.stage": "answer" } }),
    spanEvent(3, "3", {
      name: "rag.engine.stage", parent_id: "2".repeat(16), kind: "answer_step",
      params: { name: "回答生成フロー", note: "根拠不足で終了", llm_calls: 1, llm_sends: 2 },
      details: { fields: [{ label: "結果", value: "合成の結果" }] },
    }),
    spanEvent(4, "4", { name: "rag.answer_guardrail", parent_id: "1".repeat(16), kind: "answer_guardrail", status: "skipped" }),
  ];
  const events = parseChatProgressEvents(raw);
  const steps = chatProgressStepsFromEvents(events, CHAT_PROGRESS_DEFINITIONS);
  const actual = runTimeline({ steps, traceId: TRACE_ID, legacy: false });
  expect(actual.legacy).toBe(false);
  expect(actual.traceId).toBe(TRACE_ID);
  expect(actual.steps).toEqual(steps.map(chatProgressTimelineStep));
  expect(actual.steps.map((row) => [row.level ?? 0, row.status])).toEqual([[0, "done"], [1, "done"], [0, "skipped"]]);
  expect(actual.steps[1].detail).toContain("根拠不足で終了");
});

it("span の無い古い形式の記録は行を作らず古い形式にする。span の見えない記録は空（古い形式にしない）", () => {
  const steps = [{ id: "s1", label: "文書検索", status: "done" as const }];
  expect(runTimeline({ steps, legacy: true })).toEqual({ steps: [], legacy: true });
  expect(runTimeline({ steps: [], legacy: false })).toEqual({ steps: [], traceId: undefined, legacy: false });
});
