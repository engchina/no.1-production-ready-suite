import { describe, expect, it } from "vitest";

import type { ConfirmationRequest, RunState } from "./api";
import {
  agentConfirmationRecords,
  clarificationItems,
  confirmationAnswers,
  confirmationRound,
  pendingConfirmation,
  responseRecord,
} from "./confirmation";

// 回答前の確認（#1839）の Run の記録 → 共通のカード・記録の props。

const confirmation = (patch: Partial<ConfirmationRequest> = {}): ConfirmationRequest => ({
  id: "confirmation_1",
  source: "agent",
  call_id: "call_1",
  items: [
    {
      id: "item_1",
      prompt: "どの期間の売上を見ますか？",
      reason: "期間が質問から決められません。",
      selection: "single",
      options: [
        { id: "option_1", label: "今月", description: "" },
        { id: "option_2", label: "今年度" },
      ],
    },
  ],
  round: 1,
  max_rounds: 2,
  can_skip: true,
  status: "pending",
  answers: [],
  created_at: "2026-10-15T00:00:00Z",
  ...patch,
});

const run = (patch: Partial<RunState> = {}): RunState => ({
  id: "run_1",
  goal: "売上を教えて",
  agent_id: "agent_1",
  runtime_id: "builtin",
  status: "waiting_confirmation",
  steps: [],
  events: [],
  approvals: [],
  artifacts: [],
  pending_tool_calls: [],
  metadata: {},
  confirmations: [confirmation()],
  created_at: "2026-10-15T00:00:00Z",
  updated_at: "2026-10-15T00:00:00Z",
  ...patch,
});

describe("pendingConfirmation", () => {
  it("Agent 自身の確認は確認待ちの Run だけ、ツールの結果の確認は完了した Run だけ", () => {
    expect(pendingConfirmation(run())?.id).toBe("confirmation_1");
    expect(pendingConfirmation(run({ status: "cancelled" }))).toBeNull();
    const tool = confirmation({ source: "rag" });
    expect(pendingConfirmation(run({ status: "completed", confirmations: [tool] }))?.source).toBe("rag");
    expect(pendingConfirmation(run({ status: "running", confirmations: [tool] }))).toBeNull();
  });

  it("答え済み・古い確認と、確認の無い古い Run は出さない", () => {
    expect(pendingConfirmation(run({ confirmations: [confirmation({ status: "answered" })] }))).toBeNull();
    expect(pendingConfirmation(run({ status: "completed", confirmations: [confirmation({ source: "nl2sql", status: "expired" })] }))).toBeNull();
    expect(pendingConfirmation(run({ confirmations: undefined }))).toBeNull();
  });
});

describe("clarificationItems / confirmationAnswers / confirmationRound", () => {
  it("確認事項と選択肢をカードの形にする（空の説明は出さない）", () => {
    expect(clarificationItems(confirmation())).toEqual([
      {
        id: "item_1",
        prompt: "どの期間の売上を見ますか？",
        reason: "期間が質問から決められません。",
        selection: "single",
        options: [
          { id: "option_1", label: "今月", description: undefined },
          { id: "option_2", label: "今年度", description: undefined },
        ],
      },
    ]);
  });

  it("カードの答えを API の答えにする", () => {
    expect(confirmationAnswers([{ itemId: "item_1", optionIds: ["option_2"], otherText: "" }])).toEqual([
      { item_id: "item_1", option_ids: ["option_2"], other_text: "" },
    ]);
  });

  it("往復は上限の内だけ出す", () => {
    expect(confirmationRound(confirmation())).toEqual({ current: 1, max: 2 });
    expect(confirmationRound(confirmation({ round: 3, max_rounds: 2 }))).toBeUndefined();
  });
});

describe("記録の表示", () => {
  it("Agent 自身の確認に答えた内容（選んだ選択肢の名前と自由入力）とスキップ", () => {
    const answered = confirmation({
      status: "answered",
      answers: [{ item_id: "item_1", option_ids: ["option_2"], other_text: "" }],
    });
    const skipped = confirmation({ id: "confirmation_2", status: "skipped" });
    expect(agentConfirmationRecords(run({ status: "completed", confirmations: [answered, skipped] }))).toEqual([
      {
        key: "confirmation_1",
        outcome: "answered",
        entries: [{ id: "item_1", prompt: "どの期間の売上を見ますか？", selected: ["今年度"], otherText: undefined }],
      },
      {
        key: "confirmation_2",
        outcome: "skipped",
        entries: [{ id: "item_1", prompt: "どの期間の売上を見ますか？", selected: [], otherText: undefined }],
      },
    ]);
  });

  it("ツールの結果の確認に答えて作った Run は、metadata の答えを利用者の側に出す", () => {
    const response = run({
      metadata: {
        clarification_response: {
          confirmation_id: "confirmation_9",
          source: "rag",
          action: "answer",
          entries: [{ item_id: "role", prompt: "どの立場ですか？", selected: ["管理者"], other_text: "" }],
        },
      },
    });
    expect(responseRecord(response)).toEqual({
      key: "confirmation_9",
      outcome: "answered",
      entries: [{ id: "role", prompt: "どの立場ですか？", selected: ["管理者"], otherText: undefined }],
    });
    expect(responseRecord(run())).toBeNull();
  });
});
