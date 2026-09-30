import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api 業務ビュー(Business View)", () => {
  it("createBusinessView は config を POST し data を返す", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          id: "bv-1",
          name: "経理アシスタント",
          description: null,
          status: "ACTIVE",
          knowledge_base_count: 2,
          config: {
            version: 1,
            knowledge_base_ids: ["kb-1", "kb-2"],
            query: {
              guardrail_policy: null,
              docrag_query_strategy: "rag_fusion",
            },
          },
          knowledge_bases: [
            { id: "kb-1", name: "社内規程" },
            { id: "kb-2", name: "製品 FAQ" },
          ],
          created_at: "2026-06-19T00:00:00Z",
          updated_at: "2026-06-19T00:00:00Z",
          archived_at: null,
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.createBusinessView({
      name: "経理アシスタント",
      config: {
        version: 1,
        knowledge_base_ids: ["kb-1", "kb-2"],
        query: {
          guardrail_policy: null,
          docrag_query_strategy: "rag_fusion",
        },
        serving_mode: "fused",
      },
    });

    expect(result.id).toBe("bv-1");
    expect(result.knowledge_base_count).toBe(2);
    expect(result.knowledge_bases.map((kb) => kb.name)).toEqual(["社内規程", "製品 FAQ"]);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/business-views");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body.config.query.docrag_query_strategy).toBe("rag_fusion");
  });

  it("listBusinessViews は warning_messages 付きで縮退できる", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: ["データベースに接続できません。"],
        })
      )
    );

    const page = await api.listBusinessViews({ status: "ACTIVE" });
    expect(page.total).toBe(0);
    expect(page.warning_messages).toEqual(["データベースに接続できません。"]);
  });

  it("archiveBusinessView は archive endpoint を POST する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          id: "bv-1",
          name: "経理アシスタント",
          description: null,
          status: "ARCHIVED",
          knowledge_base_count: 0,
          config: {
            version: 1,
            knowledge_base_ids: [],
            query: {
              guardrail_policy: null,
            },
          },
          knowledge_bases: [],
          created_at: "2026-06-19T00:00:00Z",
          updated_at: "2026-06-19T00:00:00Z",
          archived_at: "2026-06-19T01:00:00Z",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.archiveBusinessView("bv-1");
    expect(result.status).toBe("ARCHIVED");
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/business-views/bv-1/archive");
    expect(init?.method).toBe("POST");
  });
});
