import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("api.request envelope", () => {
  it("成功時は data を取り出す", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { stats: { total_uploads: 3 } },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getDashboardSummary();

    expect(result.stats.total_uploads).toBe(3);
    expect(fetchMock).toHaveBeenCalledWith("/api/dashboard/summary", expect.anything());
  });

  it("エラー時は error_messages を持つ ApiError を投げる", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ data: null, error_messages: ["権限がありません。"], warning_messages: [] }, 403)
      )
    );

    await expect(api.getDashboardSummary()).rejects.toMatchObject({
      status: 403,
      messages: ["権限がありません。"],
    });
    await expect(api.getDashboardSummary()).rejects.toBeInstanceOf(ApiError);
  });

  it("listDocuments は query string を組み立てる", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ data: { items: [], total: 0, limit: 50, offset: 0, has_next: false }, error_messages: [], warning_messages: [] })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listDocuments({ status: "UPLOADED", q: "invoice", limit: 20, offset: 40 });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("status=UPLOADED");
    expect(url).toContain("q=invoice");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=40");
  });

  it("getReadiness は 503 の degraded envelope も data として返す", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            data: {
              status: "degraded",
              version: "0.1.0",
              message: "adapter=oci",
              checks: { oci_common: "missing" },
            },
            error_messages: [],
            warning_messages: [],
          },
          503
        )
      )
    );

    const result = await api.getReadiness();

    expect(result.status).toBe("degraded");
    expect(result.checks.oci_common).toBe("missing");
  });

  it("selectAi は Select AI endpoint へ JSON body を送る", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          action: "showsql",
          result_text: "SELECT COUNT(*) FROM rag_documents",
          generated_sql: "SELECT COUNT(*) FROM rag_documents",
          profile_name: "rag_profile",
          query_chars: 12,
          guardrail_warnings: [],
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.selectAi({
      query: "文書数を集計",
      action: "showsql",
      profile_name: "rag_profile",
      max_result_chars: 12000,
    });

    expect(result.generated_sql).toContain("SELECT COUNT");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/search/select-ai",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          query: "文書数を集計",
          action: "showsql",
          profile_name: "rag_profile",
          max_result_chars: 12000,
        }),
      })
    );
  });

  it("selectAi は 503 を ApiError として扱う", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            data: null,
            error_messages: ["Oracle Select AI profile が未設定です。"],
            warning_messages: [],
          },
          503
        )
      )
    );

    await expect(api.selectAi({ query: "文書数" })).rejects.toMatchObject({
      status: 503,
      messages: ["Oracle Select AI profile が未設定です。"],
    });
  });

  it("updateModelSettings は Enterprise AI payload template を保持して送る", async () => {
    const payload = {
      enterprise_ai: {
        endpoint: "https://enterprise-ai.example",
        project_ocid: "ocid1.generativeaiproject.oc1..example",
        api_key: "",
        has_api_key: false,
        clear_api_key: false,
        models: [
          {
            model_id: "enterprise-llm",
            display_name: "標準 LLM",
            vision_enabled: true,
          },
        ],
        default_model_id: "enterprise-llm",
        api_path: "/responses",
        text_payload_template: '{"input":"${user_message}"}',
        vision_payload_template: '{"input":"${data_base64}"}',
        text_response_path: "/data/text",
        vision_response_path: "/data/document",
        timeout_seconds: 60,
        max_retries: 2,
      },
      generative_ai: {
        embedding_model: "cohere.embed-v4.0",
        embedding_dim: 1536,
        rerank_model: "cohere.rerank-v4.0-fast",
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { settings: payload, checks: {}, source: "runtime" },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.updateModelSettings(payload);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/model",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(payload),
      })
    );
  });

  it("updateUploadStorageSettings は保存先 payload を設定 API へ送る", async () => {
    const payload = {
      backend: "oci" as const,
      local_storage_dir: "/tmp/production-ready-rag",
      object_storage_namespace: "example-namespace",
      object_storage_bucket: "rag-originals",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          ...payload,
          ai_service_adapter: "local",
          readiness: "ok",
          max_upload_bytes: 209715200,
          config_source: "runtime",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updateUploadStorageSettings(payload);

    expect(result.backend).toBe("oci");
    expect(result.max_upload_bytes).toBe(209715200);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/upload-storage",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(payload),
      })
    );
  });

  it("readOciObjectStorageNamespace は OCI 設定 payload を送る", async () => {
    const payload = {
      config_file: "~/.oci/config",
      profile: "DEFAULT",
      region: "ap-osaka-1",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { namespace: "mytenancynamespace" },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.readOciObjectStorageNamespace(payload);

    expect(result.namespace).toBe("mytenancynamespace");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/oci/object-storage/namespace",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    );
  });

  it("uploadOciPrivateKey は秘密鍵ファイルを FormData で送る", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { key_file: "~/.oci/oci_api_key.pem", saved: true },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"], "key.pem", {
      type: "application/x-pem-file",
    });
    const result = await api.uploadOciPrivateKey(file);

    expect(result.key_file).toBe("~/.oci/oci_api_key.pem");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/oci/key-file",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      })
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("runEvaluation は golden set payload を評価 API へ送る", async () => {
    const payload = {
      cases: [
        {
          id: "case-1",
          query: "承認フローは？",
          relevant_document_ids: ["doc-1"],
          expected_answer_keywords: ["承認"],
        },
      ],
      top_k: 10,
      rerank_top_n: 5,
      mode: "hybrid" as const,
      filters: { status: "INDEXED" },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          case_count: 1,
          error_count: 0,
          evaluated_k: 10,
          precision_at_k: 1,
          recall_at_k: 1,
          mrr: 1,
          answer_keyword_hit_rate: 1,
          groundedness_pass_rate: 1,
          passed: true,
          threshold_failures: [],
          failure_reason_counts: {},
          case_results: [],
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.runEvaluation(payload);

    expect(result.passed).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evaluation/run",
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) })
    );
  });

  it("compareEvaluation は experiments payload を比較 API へ送る", async () => {
    const payload = {
      cases: [
        {
          id: "case-1",
          query: "承認フローは？",
          relevant_document_ids: ["doc-1"],
          expected_answer_keywords: ["承認"],
        },
      ],
      experiments: [
        {
          id: "hybrid",
          top_k: 10,
          rerank_top_n: 5,
          mode: "hybrid" as const,
          filters: {},
          rag_overrides: {
            rrf_k: 30,
            context_diversity_lambda: 0.4,
            context_neighbor_window: 1,
          },
        },
      ],
      ranking_metric: "mrr" as const,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { ranking_metric: "mrr", best_experiment_id: "hybrid", results: [] },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.compareEvaluation(payload);

    expect(result.best_experiment_id).toBe("hybrid");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evaluation/compare",
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) })
    );
  });
});
