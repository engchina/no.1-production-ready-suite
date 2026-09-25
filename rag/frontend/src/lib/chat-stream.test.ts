import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatMessage } from "./chat-stream";

function sseResponse(blocks: string[]): Response {
  return new Response(blocks.join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("streamChatMessage", () => {
  it("単一モデル: start → delta → citations → done → all_done を解析する", async () => {
    const body = [
      `event: start\ndata: ${JSON.stringify({
        conversation_id: "c1",
        user_message: { message_id: "u1", role: "USER", content: "質問" },
        columns: [{ model_id: "m1", label: "MODEL 1" }],
      })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "回答" })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "です。" })}\n\n`,
      `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [] })}\n\n`,
      `event: citations\ndata: ${JSON.stringify({ model_id: "m1", citations: [{ chunk_id: "ch1" }] })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
      `event: all_done\ndata: ${JSON.stringify({ conversation_id: "c1" })}\n\n`,
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(body)));

    const answers: Record<string, string> = {};
    let columns: string[] = [];
    let citationCount = 0;
    let traceId = "";
    let allDone = false;
    let userContent = "";
    await streamChatMessage(
      "c1",
      { content: "質問" },
      {
        onStart: ({ user_message, columns: cols }) => {
          userContent = user_message.content;
          columns = cols.map((c) => c.model_id);
        },
        onDelta: (modelId, text) => {
          answers[modelId] = (answers[modelId] ?? "") + text;
        },
        onMetadata: ({ trace_id }) => (traceId = trace_id),
        onCitations: (_modelId, citations) => (citationCount = citations.length),
        onAllDone: () => (allDone = true),
      }
    );

    expect(userContent).toBe("質問");
    expect(columns).toEqual(["m1"]);
    expect(answers.m1).toBe("回答です。");
    expect(citationCount).toBe(1);
    expect(traceId).toBe("t1");
    expect(allDone).toBe(true);
  });

  it("マルチモデル: モデル別に delta を振り分ける", async () => {
    const body = [
      `event: start\ndata: ${JSON.stringify({
        conversation_id: "c1",
        user_message: { message_id: "u1", role: "USER", content: "比較" },
        columns: [
          { model_id: "m1", label: "M1" },
          { model_id: "m2", label: "M2" },
        ],
      })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "A" })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ model_id: "m2", text: "B" })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ model_id: "m2", message_id: "a2" })}\n\n`,
      `event: all_done\ndata: ${JSON.stringify({ conversation_id: "c1" })}\n\n`,
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(body)));

    const answers: Record<string, string> = {};
    const doneModels: string[] = [];
    await streamChatMessage(
      "c1",
      { content: "比較", model_ids: ["m1", "m2"] },
      {
        onDelta: (modelId, text) => {
          answers[modelId] = (answers[modelId] ?? "") + text;
        },
        onModelDone: ({ model_id }) => doneModels.push(model_id),
      }
    );

    expect(answers).toEqual({ m1: "A", m2: "B" });
    expect(doneModels.sort()).toEqual(["m1", "m2"]);
  });

  it("error event は onModelError を呼ぶ", async () => {
    const body = [
      `event: error\ndata: ${JSON.stringify({ model_id: "m1", message: "失敗しました。", error_type: "ValueError" })}\n\n`,
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(body)));

    let errorMessage = "";
    await streamChatMessage(
      "c1",
      { content: "x" },
      { onModelError: ({ message }) => (errorMessage = message) }
    );
    expect(errorMessage).toBe("失敗しました。");
  });

  it("非 2xx は ApiError を投げる", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "チャット機能は現在無効です。" }), { status: 404 })
      )
    );

    await expect(
      streamChatMessage("c1", { content: "x" }, {})
    ).rejects.toMatchObject({ status: 404, messages: ["チャット機能は現在無効です。"] });
  });
});
