import { chatProgressStepsFromEvents, type ChatProgressEvent } from "@production-ready/ui";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHAT_PROGRESS_DEFINITIONS, chatSubmitProgressSteps } from "./chat-progress";
import { streamChatMessage } from "./chat-stream";

afterEach(() => vi.unstubAllGlobals());

const TARGET = "a".repeat(32);
let seq = 0;

function step(
  stepId: string,
  status: "pending" | "running" | "done" | "failed" | "skipped",
  extra: Partial<Extract<ChatProgressEvent, { type: "step" }>> = {}
): ChatProgressEvent {
  seq += 1;
  return {
    schema_version: 1,
    seq,
    target_id: TARGET,
    attempt: 0,
    emitted_at: "2026-10-04T00:00:00.000Z",
    type: "step",
    step_id: stepId,
    status,
    ...extra,
  };
}

// 処理の段階の定義（Issue 1146 / 1359）。
describe("CHAT_PROGRESS_DEFINITIONS", () => {
  it("段階の id と状態から名前を付け、時刻はイベントのものを使う", () => {
    seq = 0;
    const steps = chatProgressStepsFromEvents(
      [
        step("rewrite_query", "pending"),
        step("retrieve", "pending"),
        step("rerank", "pending"),
        step("generate_answer", "pending"),
        step("check_guardrail", "pending"),
        step("rewrite_query", "done", {
          started_at: "2026-10-04T00:00:00.000Z",
          finished_at: "2026-10-04T00:00:01.000Z",
        }),
        step("retrieve", "running", { started_at: "2026-10-04T00:00:01.000Z", params: { attempt: 2 } }),
        step("rerank", "skipped"),
        step("generate_answer", "failed"),
      ],
      CHAT_PROGRESS_DEFINITIONS
    );
    expect(steps.map((item) => item.label)).toEqual([
      "質問を整理しました",
      "関係する文書を探しています",
      "並べ替え",
      "回答を作れませんでした",
      "回答の確認",
    ]);
    expect(steps[0]).toMatchObject({ startedAt: "2026-10-04T00:00:00.000Z", finishedAt: "2026-10-04T00:00:01.000Z" });
    // 補正検索の回数は値から i18n で作る。
    expect(steps[1].detail).toBe("2 回目");
  });

  it("完了した検索は根拠の件数を補足にし、1 回目の検索は回数を出さない", () => {
    seq = 0;
    const done = chatProgressStepsFromEvents(
      [step("retrieve", "running", { params: { attempt: 2 } }), step("retrieve", "done", { params: { attempt: 2, citations: 3 } })],
      CHAT_PROGRESS_DEFINITIONS
    );
    expect(done[0]).toMatchObject({ label: "関係する文書を探しました", detail: "根拠 3 件" });
    seq = 0;
    const first = chatProgressStepsFromEvents([step("retrieve", "running", { params: { attempt: 1 } })], CHAT_PROGRESS_DEFINITIONS);
    expect(first[0].detail).toBeUndefined();
  });

  it("未知の段階は id をそのまま出す", () => {
    seq = 0;
    expect(chatProgressStepsFromEvents([step("new_step", "running")], CHAT_PROGRESS_DEFINITIONS)).toEqual([
      { id: "new_step", label: "new_step", status: "running" },
    ]);
  });

  it("送信の応答待ちは「質問を送信しています」を送った時刻から出す", () => {
    expect(chatSubmitProgressSteps(Date.parse("2026-10-04T00:00:00.000Z"))).toEqual([
      { id: "submit", label: "質問を送信しています", status: "running", startedAt: "2026-10-04T00:00:00.000Z" },
    ]);
  });

  it("チャットの SSE の chat_progress を検証して onChatProgress へ渡す（形の違うイベントは捨てる）", async () => {
    seq = 0;
    const event = step("retrieve", "running");
    const body = [
      `event: chat_progress\ndata: ${JSON.stringify({ model_id: "m1", message_id: TARGET, event })}\n\n`,
      `event: chat_progress\ndata: ${JSON.stringify({ model_id: "m1", message_id: TARGET, event: { seq: 0 } })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }))
    );
    const received: unknown[] = [];
    await streamChatMessage("c1", { content: "質問" }, { onChatProgress: (payload) => received.push(payload) });
    expect(received).toEqual([{ model_id: "m1", message_id: TARGET, event }]);
  });
});
