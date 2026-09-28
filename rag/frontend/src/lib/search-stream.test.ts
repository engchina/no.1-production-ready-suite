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
      `event: stage\ndata: ${JSON.stringify({ trace_id: "t1", stage: "embedding", outcome: "started", elapsed_ms: 0, attributes: { input_count: 1 } })}\n\n`,
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
    let stageName = "";
    await streamSearch(
      { query: "請求金額" },
      {
        onStage: (stage) => (stageName = stage.stage),
        onMetadata: (m) => (traceId = m.trace_id),
        onDelta: (text) => (answer += text),
        onCitations: (c) => (citationCount = c.length),
        onDone: () => (done = true),
      }
    );

    expect(traceId).toBe("t1");
    expect(stageName).toBe("embedding");
    expect(answer).toBe("請求金額");
    expect(citationCount).toBe(1);
    expect(done).toBe(true);
  });

  it("replace event はマスク済み本文で onReplace を呼ぶ", async () => {
    const body = [
      `event: delta\ndata: ${JSON.stringify({ text: "口座番号は " })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ text: "1234567 です。" })}\n\n`,
      `event: replace\ndata: ${JSON.stringify({ text: "口座番号は [機微情報] です。" })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ trace_id: "t1" })}\n\n`,
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(body)));

    let answer = "";
    await streamSearch(
      { query: "口座番号" },
      {
        onDelta: (text) => (answer += text),
        onReplace: (text) => (answer = text),
      }
    );

    expect(answer).toBe("口座番号は [機微情報] です。");
  });

  it("末尾の空行がない最後の event も解析する", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          `event: delta\ndata: ${JSON.stringify({ text: "最終チャンク" })}\n\n`,
          `event: done\ndata: ${JSON.stringify({ trace_id: "t1" })}`,
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
          `event: done\r\ndata: ${JSON.stringify({ trace_id: "t1" })}\r\n\r\n`,
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

  it("error event（timeout）は 504 の ApiError として投げ、その後の event は流さない", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          `event: stage\ndata: ${JSON.stringify({ trace_id: "t1", stage: "retrieval", outcome: "started", elapsed_ms: 0, attributes: {} })}\n\n`,
          `event: error\ndata: ${JSON.stringify({ trace_id: "t1", message: "検索処理がタイムアウトしました。条件を絞って再度お試しください。", error_type: "TimeoutError" })}\n\n`,
          `event: delta\ndata: ${JSON.stringify({ text: "流してはいけない" })}\n\n`,
        ])
      )
    );

    let answer = "";
    let done = false;
    await expect(
      streamSearch(
        { query: "x" },
        { onDelta: (text) => (answer += text), onDone: () => (done = true) }
      )
    ).rejects.toMatchObject({
      status: 504,
      messages: ["検索処理がタイムアウトしました。条件を絞って再度お試しください。"],
    });
    expect(answer).toBe("");
    expect(done).toBe(false);
  });

  it("error event（回答形式の検証の失敗）は 502、その他は 500 にする", async () => {
    const errorBody = (payload: Record<string, unknown>) =>
      sseResponse([`event: error\ndata: ${JSON.stringify(payload)}\n\n`]);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          errorBody({ message: "回答形式の検証に失敗しました。", error_type: "GenerationContractError" })
        )
        .mockResolvedValueOnce(errorBody({ error_type: "RuntimeError" }))
    );

    await expect(streamSearch({ query: "x" }, {})).rejects.toMatchObject({
      status: 502,
      messages: ["回答形式の検証に失敗しました。"],
    });
    await expect(streamSearch({ query: "x" }, {})).rejects.toMatchObject({
      status: 500,
      messages: ["検索処理中にエラーが発生しました。時間をおいて再度お試しください。"],
    });
  });

  it("done を受けずに終わった stream は途中終了のエラーにする", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([`event: delta\ndata: ${JSON.stringify({ text: "途中まで" })}\n\n`])
      )
    );

    let answer = "";
    await expect(
      streamSearch({ query: "x" }, { onDelta: (text) => (answer += text) })
    ).rejects.toMatchObject({
      status: 502,
      messages: [
        "回答の受信が途中で途切れました。通信状態を確認して、もう一度検索してください。",
      ],
    });
    expect(answer).toBe("途中まで");
  });
});
