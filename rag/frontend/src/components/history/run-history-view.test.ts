import {chatProgressStepsFromEvents, chatProgressTimelineStep, parseChatProgressEvents} from "@production-ready/ui";
import {CHAT_PROGRESS_DEFINITIONS} from "@/lib/chat-progress";
import {answerProcessStepsFromStages, type AnswerStageRecord} from "@/lib/answer-process";
import { describe, expect, it } from "vitest";

import type { RunStage, RunStep } from "@/lib/api";

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
  answerStepStatus,
  runStepStatus,
  runTimelineSteps,
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

  it("段の結果を処理の経過の状態にする", () => {
    expect(runStepStatus("success")).toBe("done");
    expect(runStepStatus("completed")).toBe("done");
    expect(runStepStatus("error")).toBe("failed");
    expect(runStepStatus("cancelled")).toBe("skipped");
  });

  it("処理の経過: 旧記録は外側の工程を重複させず、未知の階層を補わない", () => {
    const stages: RunStage[] = [
      { stage: "history_rewrite", outcome: "success", elapsed_ms: 1000 },
      { stage: "answer_step:質問の理解", outcome: "success", elapsed_ms: 500 },
      { stage: "answer_step:文書検索", outcome: "error", elapsed_ms: 300 },
      { stage: "answer", outcome: "error", elapsed_ms: 900 },
    ];
    const steps: RunStep[] = [{ name: "質問の理解", status: "completed", elapsed_seconds: 0.5, llm_calls: 2 }];
    const rows = runTimelineSteps({ stages, steps, created_at: "2026-10-15T09:00:01.900Z", elapsed_ms: 1900 });
    expect(rows.map((row) => [row.label, row.level ?? 0, row.status])).toEqual([
      ["会話を踏まえた質問の書き換え", 0, "done"],
      ["質問の条件の読み取り", 0, "done"],
      ["文書の検索", 0, "failed"],
    ]);
    // 工程は親の段の始まり（1 秒後）から順に置く。
    expect(rows[1].startedAt).toBe("2026-10-15T09:00:01.000Z");
    expect(rows[2].startedAt).toBe("2026-10-15T09:00:01.500Z");
    expect(rows[1].detail).toBe("LLM 2 回");
  });

  it("回答の記録の工程の状態を処理の経過の状態にする", () => {
    // rag_engine は終わった工程を complete、結果の説明つきの工程を日本語の説明にする。
    expect(answerStepStatus("complete")).toBe("done");
    expect(answerStepStatus("該当なし")).toBe("done");
    expect(answerStepStatus("未実行: 候補なし")).toBe("done");
    expect(answerStepStatus("")).toBe("done");
    expect(answerStepStatus(null)).toBe("done");
    expect(answerStepStatus("エラーで中断")).toBe("failed");
    expect(answerStepStatus("中止")).toBe("failed");
    expect(answerStepStatus("error")).toBe("failed");
    expect(answerStepStatus("cancelled")).toBe("skipped");
  });

  it("処理の経過: 進捗の工程が無ければ回答の記録の工程を使う", () => {
    const rows = runTimelineSteps({
      stages: [{ stage: "answer", outcome: "success", elapsed_ms: 1000 }],
      steps: [
        { name: "質問の理解", status: "complete", elapsed_seconds: 0.3, llm_calls: 1 },
        { name: "文書検索", status: "complete", elapsed_seconds: null, llm_calls: 0 },
        { name: "画面の選択", status: "該当なし", elapsed_seconds: 0.1, llm_calls: 1 },
      ],
      created_at: "2026-10-15T09:00:01Z",
      elapsed_ms: 1000,
    });
    expect(rows.map((row) => [row.label, row.status])).toEqual([
      ["質問の条件の読み取り", "done"],
      ["文書の検索", "done"],
      ["関係する画面の選択", "done"],
    ]);
    expect(rows[1]).toMatchObject({ level: 0, durationMs: null, startedAt: null });
  });

  it("処理の経過: 工程と段の詳細を段を開いて見る詳細にする（同じ名前は記録の順。旧い行の配列も読む）", () => {
    const rows = runTimelineSteps({
      stages: [
        { stage: "answer_step:文書検索", outcome: "success", elapsed_ms: 300 },
        { stage: "answer_step:文書検索", outcome: "success", elapsed_ms: 200 },
        { stage: "answer_step:質問の理解", outcome: "success", elapsed_ms: 100 },
        { stage: "answer", outcome: "success", elapsed_ms: 900 },
        // 回答の安全チェックの段の詳細（#2341）。
        {
          stage: "answer_guardrail",
          outcome: "success",
          elapsed_ms: 20,
          details: { fields: [{ label: "結果", value: "問題なし" }] },
        },
      ],
      steps: [
        // 旧い記録（#2294）は行の配列。
        { name: "文書検索", status: "complete", elapsed_seconds: 0.3, llm_calls: 0, details: ["検索文: 2 本"] },
        {
          name: "文書検索",
          status: "complete",
          elapsed_seconds: 0.2,
          llm_calls: 0,
          details: { fields: [{ label: "検索の候補", value: "4 件（2 文書）" }], lines: ["1. 文書 A"], truncated: true },
        },
        { name: "質問の理解", status: "complete", elapsed_seconds: 0.1, llm_calls: 1, details: null },
      ],
      created_at: "2026-10-15T09:00:01Z",
      elapsed_ms: 900,
    });
    expect(rows.map((row) => row.details)).toEqual([
      { lines: ["検索文: 2 本"] },
      { fields: [{ label: "検索の候補", value: "4 件（2 文書）" }], lines: ["1. 文書 A"], truncated: true },
      undefined,
      { fields: [{ label: "結果", value: "問題なし" }] },
    ]);
  });
});

it("保存したチャットのイベントは内容・階層・補足・状態・時間を変えずに履歴へ写す", () => {
  const events = parseChatProgressEvents([0, 1, 2].map((depth) => ({
    schema_version: 1, target_id: "synthetic", seq: depth + 1, attempt: 0,
    emitted_at: "2026-10-23T00:00:02Z", type: "step", step_id: `s${depth}`, kind: "answer_step", status: "done",
    started_at: "2026-10-23T00:00:00Z", finished_at: "2026-10-23T00:00:02Z",
    params: {name: "回答生成フロー", depth, note: "根拠不足で終了", llm_calls: depth + 1, llm_sends: depth + 2},
    details: {fields: [{label: "結果", value: "合成の結果"}]},
  })));
  const actual = runTimelineSteps({progress_events: events, stages: [], steps: [], created_at: "2026-10-23T00:00:02Z", elapsed_ms: 2000});
  expect(actual).toEqual(chatProgressStepsFromEvents(events, CHAT_PROGRESS_DEFINITIONS).map(chatProgressTimelineStep));
  expect(actual.map((row) => row.level ?? 0)).toEqual([0,1,2]);
  expect(actual[0].detail).toContain("根拠不足で終了");
});
it("検索の開始・終了イベントは検索画面と同じ順序と入れ子で履歴へ写す", () => {
  const stage_events: AnswerStageRecord[] = [
    {stage: "answer", outcome: "started", elapsed_ms: 0},
    {stage: "answer_step:回答生成フロー", outcome: "started", elapsed_ms: 0},
    {stage: "answer_step:文書検索", outcome: "started", elapsed_ms: 0},
    {stage: "answer_step:文書検索", outcome: "success", elapsed_ms: 100},
    {stage: "answer_step:回答生成フロー", outcome: "success", elapsed_ms: 300, attributes: {status: "根拠不足で終了", llm_calls: 1}},
    {stage: "answer", outcome: "success", elapsed_ms: 300},
    {stage: "answer_guardrail", outcome: "started", elapsed_ms: 0},
    {stage: "answer_guardrail", outcome: "skipped", elapsed_ms: 1, details: {fields: [{label: "結果", value: "無効のため省略"}]}},
  ];
  const rows = runTimelineSteps({stage_events, stages: [], steps: [], created_at: "2026-10-23T00:00:02Z", elapsed_ms: 301});
  expect(rows).toEqual(answerProcessStepsFromStages(stage_events, {terminal: "done"}).map(chatProgressTimelineStep));
  expect(rows.map((row) => row.level ?? 0)).toEqual([0,1,0]);
  expect(rows.map((row) => row.status)).toEqual(["done","done","skipped"]);
});
