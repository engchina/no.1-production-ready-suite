import { afterEach, describe, expect, it, vi } from "vitest";

import { chatProgressStepsFromEvent, chatSubmitProgressSteps } from "./chat-progress";
import { streamChatMessage } from "./chat-stream";

afterEach(() => vi.unstubAllGlobals());

// 処理の段階（Issue 1146）。
describe("chatProgressStepsFromEvent", () => {
  it("段階の id と状態から名前を付け、時刻と補足はそのまま渡す", () => {
    const steps = chatProgressStepsFromEvent([
      { id: "rewrite_query", label: "質問を整理しています", status: "done", startedAt: "2026-10-04T00:00:00.000Z", finishedAt: "2026-10-04T00:00:01.000Z" },
      { id: "retrieve", label: "関係する文書を探しています", status: "running", startedAt: "2026-10-04T00:00:01.000Z", detail: "2 回目" },
      { id: "rerank", label: "並べ替えています", status: "skipped" },
      { id: "generate_answer", label: "回答を作っています", status: "failed" },
      { id: "check_guardrail", label: "回答を確認しています", status: "pending" },
    ]);
    expect(steps.map((step) => step.label)).toEqual([
      "質問を整理しました",
      "関係する文書を探しています",
      "並べ替え",
      "回答を作れませんでした",
      "回答の確認",
    ]);
    expect(steps[0]).toMatchObject({ startedAt: "2026-10-04T00:00:00.000Z", finishedAt: "2026-10-04T00:00:01.000Z" });
    expect(steps[1].detail).toBe("2 回目");
  });

  it("未知の段階は backend の名前を出し、形の違う要素は捨てる", () => {
    const steps = chatProgressStepsFromEvent([
      { id: "new_step", label: "新しい段階です", status: "running" },
      { id: "retrieve", label: "x", status: "unknown" },
      { label: "id が無い", status: "done" },
      null,
      "text",
    ]);
    expect(steps).toEqual([
      { id: "new_step", label: "新しい段階です", status: "running", startedAt: undefined, finishedAt: undefined, detail: undefined },
    ]);
    expect(chatProgressStepsFromEvent({ steps: [] })).toEqual([]);
  });

  it("送信の応答待ちは「質問を送信しています」を送った時刻から出す", () => {
    expect(chatSubmitProgressSteps(Date.parse("2026-10-04T00:00:00.000Z"))).toEqual([
      { id: "submit", label: "質問を送信しています", status: "running", startedAt: "2026-10-04T00:00:00.000Z" },
    ]);
  });

  it("チャットの SSE の progress を onProgress へ渡す", async () => {
    const body = [
      `event: progress\ndata: ${JSON.stringify({ model_id: "m1", steps: [{ id: "retrieve", label: "x", status: "running" }] })}\n\n`,
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }))
    );
    const received: [string, string[]][] = [];
    await streamChatMessage("c1", { content: "質問" }, {
      onProgress: (modelId, steps) => received.push([modelId, steps.map((step) => `${step.id}:${step.label}`)]),
    });
    expect(received).toEqual([["m1", ["retrieve:関係する文書を探しています"]]]);
  });
});
