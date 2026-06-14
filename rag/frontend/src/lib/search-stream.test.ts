import { afterEach, describe, expect, it, vi } from "vitest";

import { streamSearch } from "./search-stream";

function sseResponse(blocks: string[]): Response {
  return new Response(blocks.join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("streamSearch", () => {
  it("metadata/delta/citations/done を順に解析する", async () => {
    const body = [
      `event: metadata\ndata: ${JSON.stringify({ trace_id: "t1", elapsed_ms: 12, guardrail_warnings: [], diagnostics: {} })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ text: "請求" })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ text: "金額" })}\n\n`,
      `event: citations\ndata: ${JSON.stringify([{ chunk_id: "c1" }])}\n\n`,
      `event: done\ndata: ${JSON.stringify({ trace_id: "t1" })}\n\n`,
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(body)));

    let answer = "";
    let citationCount = 0;
    let done = false;
    let traceId = "";
    await streamSearch(
      { query: "請求金額" },
      {
        onMetadata: (m) => (traceId = m.trace_id),
        onDelta: (text) => (answer += text),
        onCitations: (c) => (citationCount = c.length),
        onDone: () => (done = true),
      }
    );

    expect(traceId).toBe("t1");
    expect(answer).toBe("請求金額");
    expect(citationCount).toBe(1);
    expect(done).toBe(true);
  });

  it("末尾の空行がない最後の event も解析する", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          `event: delta\ndata: ${JSON.stringify({ text: "最終チャンク" })}`,
        ])
      )
    );

    let answer = "";
    await streamSearch(
      { query: "最後" },
      {
        onDelta: (text) => (answer += text),
      }
    );

    expect(answer).toBe("最終チャンク");
  });

  it("CRLF 区切りの SSE event も解析する", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          `event: delta\r\ndata: ${JSON.stringify({ text: "CRLF" })}\r\n\r\n`,
        ])
      )
    );

    let answer = "";
    await streamSearch(
      { query: "改行" },
      {
        onDelta: (text) => (answer += text),
      }
    );

    expect(answer).toBe("CRLF");
  });

  it("非 2xx は ApiError を投げる", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error_messages: ["タイムアウトしました。"] }), { status: 504 })
      )
    );

    await expect(streamSearch({ query: "x" }, {})).rejects.toMatchObject({
      status: 504,
      messages: ["タイムアウトしました。"],
    });
  });
});
