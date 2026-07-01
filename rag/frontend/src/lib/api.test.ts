import { afterEach, describe, expect, it, vi } from "vitest";

import { API_REQUEST_TIMEOUT_MS, DASHBOARD_REQUEST_TIMEOUT_MS, ApiError, api } from "./api";
import { t } from "./i18n";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

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

  it("応答が返らない場合はタイムアウトを ApiError として返す", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal | undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const requestPromise = expect(api.getDashboardSummary()).rejects.toMatchObject({
      status: 408,
      messages: [
        t("common.api.timeout", { seconds: Math.ceil(DASHBOARD_REQUEST_TIMEOUT_MS / 1000) }),
      ],
    });
    await vi.advanceTimersByTimeAsync(DASHBOARD_REQUEST_TIMEOUT_MS);

    await requestPromise;
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard/summary",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
  });

  it("getDatabaseStatus は Oracle 接続テスト用に通常 API timeout を使う", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal | undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const requestPromise = expect(api.getDatabaseStatus()).rejects.toMatchObject({
      status: 408,
      messages: [
        t("common.api.timeout", { seconds: Math.ceil(API_REQUEST_TIMEOUT_MS / 1000) }),
      ],
    });
    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS);

    await requestPromise;
  });

  it("listDocuments は query string を組み立てる", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ data: { items: [], total: 0, limit: 50, offset: 0, has_next: false }, error_messages: [], warning_messages: [] })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listDocuments({
      status: "UPLOADED",
      q: "invoice",
      knowledge_base_id: "kb-1",
      limit: 20,
      offset: 40,
    });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("status=UPLOADED");
    expect(url).toContain("q=invoice");
    expect(url).toContain("knowledge_base_id=kb-1");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=40");
  });

  it("listDocuments は縮退応答の warning_messages を data へ併設する", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: ["データベースに接続できませんでした。"],
        })
      )
    );

    const page = await api.listDocuments();

    expect(page.items).toEqual([]);
    expect(page.warning_messages).toEqual(["データベースに接続できませんでした。"]);
  });

  it("正常応答では warning_messages が空配列になる", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        })
      )
    );

    const page = await api.listKnowledgeBases({ status: "ACTIVE" });

    expect(page.warning_messages).toEqual([]);
  });

  it("uploadDocument は knowledge_base_ids と ingestion_mode を multipart に含める", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          id: "doc-1",
          file_name: "policy.txt",
          status: "UPLOADED",
          file_size_bytes: 4,
          content_sha256: "a".repeat(64),
          duplicate_of_document_id: null,
          knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.uploadDocument(new File(["test"], "policy.txt"), ["kb-1", "kb-2"], "manual");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/upload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.getAll("knowledge_base_ids")).toEqual(["kb-1", "kb-2"]);
    expect(form.get("ingestion_mode")).toBe("manual");
  });

  it("knowledge base API は CRUD endpoint を呼び分ける", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          id: "kb-1",
          name: "社内規程",
          description: null,
          status: "ACTIVE",
          default_search_mode: "hybrid",
          document_count: 0,
          indexed_document_count: 0,
          error_document_count: 0,
          searchable_chunk_count: 0,
          retrieval_config: {},
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          archived_at: null,
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listKnowledgeBases({ status: "ACTIVE", q: "規程", limit: 20, offset: 0 });
    await api.createKnowledgeBase({ name: "社内規程", description: null });
    await api.archiveKnowledgeBase("kb-1");
    await api.assignDocumentsToKnowledgeBase("kb-1", { document_ids: ["doc-1"] });
    await api.removeDocumentFromKnowledgeBase("kb-1", "doc-1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/knowledge-bases?");
    expect(fetchMock.mock.calls[0][0]).toContain("status=ACTIVE");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/knowledge-bases");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/knowledge-bases/kb-1/archive");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/knowledge-bases/kb-1/documents");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/knowledge-bases/kb-1/documents/doc-1");
  });

  it("document knowledge base API は所属取得と置換 endpoint を呼ぶ", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: [{ id: "kb-1", name: "社内規程" }],
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listDocumentKnowledgeBases("doc-1");
    await api.replaceDocumentKnowledgeBases("doc-1", {
      knowledge_base_ids: ["kb-1", "kb-2"],
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/knowledge-bases");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/doc-1/knowledge-bases");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({ knowledge_base_ids: ["kb-1", "kb-2"] }),
    });
  });

  it("ingestion job API は status filter と queue 操作 endpoint を呼ぶ", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          id: "job-1",
          document_id: "doc-1",
          status: "QUEUED",
          parser_profile: "local_text_structure",
          quality_warnings: [],
          skip_reason: null,
          error_message: null,
          attempt_count: 0,
          max_attempts: 3,
          queued_at: "2026-06-15T00:00:00Z",
          started_at: null,
          finished_at: null,
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.ingestDocument("doc-1", true);
    await api.enqueueDocumentIngestionJob("doc-2");
    await api.getIngestionJob("job-1");
    await api.retryIngestionJob("job-1");
    await api.drainIngestionJobs(25);
    await api.cancelIngestionJob("job-1");

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        data: { items: [], total: 0, limit: 10, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      })
    );
    await api.listIngestionJobs({ status: "FAILED", limit: 10, offset: 20 });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/documents/doc-1/ingestion-jobs?force=true&phase=PREPROCESS"
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/documents/doc-2/ingestion-jobs?phase=PREPROCESS"
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/documents/ingestion-jobs/job-1");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/documents/ingestion-jobs/job-1/retry");
    expect(fetchMock.mock.calls[3][1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[4][0]).toBe("/api/documents/ingestion-jobs/drain?limit=25");
    expect(fetchMock.mock.calls[4][1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[5][0]).toBe("/api/documents/ingestion-jobs/job-1/cancel");
    expect(fetchMock.mock.calls[5][1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[6][0]).toContain("status=FAILED");
    expect(fetchMock.mock.calls[6][0]).toContain("limit=10");
    expect(fetchMock.mock.calls[6][0]).toContain("offset=20");
  });

  it("document workspace API は chunk / export / segment endpoint を呼ぶ", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: [],
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listDocumentChunks("doc-1");
    await api.exportDocumentExtraction("doc-1", "chunks");
    await api.exportDocumentExtraction("doc-1", "html");
    await api.listDocumentIngestionJobs("doc-1");
    await api.listDocumentIngestionSegments("doc-1");
    await api.retryFailedDocumentIngestionSegments("doc-1", "recipe-2");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/chunks");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/documents/doc-1/extraction-export?format=chunks"
    );
    expect(fetchMock.mock.calls[2][0]).toBe(
      "/api/documents/doc-1/extraction-export?format=html"
    );
    expect(fetchMock.mock.calls[3][0]).toBe("/api/documents/doc-1/ingestion-jobs");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/documents/doc-1/ingestion-segments");
    expect(fetchMock.mock.calls[5][0]).toBe(
      "/api/documents/doc-1/ingestion-segments/retry?recipe_id=recipe-2"
    );
    expect(fetchMock.mock.calls[5][1]).toMatchObject({ method: "POST" });
  });

  it("segment retry は recipe 未指定の legacy URL も維持する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ data: {}, error_messages: [], warning_messages: [] })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.retryFailedDocumentIngestionSegments("legacy-doc");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/documents/legacy-doc/ingestion-segments/retry"
    );
  });

  it("文書処理設定 API は GET と PUT を同じ resource に送る", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ data: {}, error_messages: [], warning_messages: [] })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.getDocumentIngestionConfig("doc-1");
    await api.updateDocumentIngestionConfig("doc-1", {
      preprocess_profile: null,
      parser_adapter_backend: "mineru",
      parser_docling_enabled: null,
      parser_marker_enabled: null,
      parser_unstructured_enabled: null,
      parser_unlimited_ocr_enabled: null,
      parser_mineru_enabled: null,
      parser_dots_ocr_enabled: null,
      parser_glm_ocr_enabled: null,
      chunking_strategy: null,
      chunk_size: 512,
      chunk_overlap: null,
      chunk_child_size: null,
      chunk_sentence_window_size: null,
      chunk_min_chars: null,
      graph_profile: null,
      field_extraction_enabled: null,
      asset_summary_enabled: null,
      navigation_summary_enabled: null,
      auto_parse_after_preprocess_enabled: null,
      auto_chunk_after_extract_enabled: null,
      auto_index_after_chunk_enabled: null,
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/documents/doc-1/ingestion-config");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/documents/doc-1/ingestion-config");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "PUT" });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      parser_adapter_backend: "mineru",
      chunk_size: 512,
    });
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
              message: "oci",
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
        vlm_input_mode: "files_api" as const,
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

  it("testModelSettings は対象モデルをテスト API へ送る", async () => {
    const payload = {
      settings: {
        enterprise_ai: {
          endpoint: "https://enterprise-ai.example",
          project_ocid: "ocid1.generativeaiproject.oc1..example",
          api_key: "",
          has_api_key: true,
          clear_api_key: false,
          models: [
            {
              model_id: "enterprise-llm",
              display_name: "標準 LLM",
              vision_enabled: false,
            },
          ],
          default_model_id: "enterprise-llm",
          api_path: "/responses",
          vlm_input_mode: "files_api" as const,
          text_payload_template: "",
          vision_payload_template: "",
          text_response_path: "",
          vision_response_path: "",
          timeout_seconds: 60,
          max_retries: 2,
        },
        generative_ai: {
          embedding_model: "cohere.embed-v4.0",
          embedding_dim: 1536,
          rerank_model: "cohere.rerank-v4.0-fast",
        },
      },
      target_type: "enterprise_text" as const,
      model_id: "enterprise-llm",
      vision_enabled: false,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          status: "success",
          target_type: "enterprise_text",
          model_id: "enterprise-llm",
          message: "ok",
          troubleshooting: [],
          raw_error: null,
          error_type: null,
          elapsed_ms: 12,
          checked_at: "2026-06-14T00:00:00Z",
          details: { surface: "llm" },
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.testModelSettings(payload);

    expect(result.status).toBe("success");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/model/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    );
  });

  it("testDatabaseSettings は timeout 診断付きの結果を返す", async () => {
    const payload = {
      user: "rag_app",
      dsn: "ragdb_high",
      wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          status: "failed",
          readiness: "ok",
          message: "Oracle 26ai 接続テストが 15 秒でタイムアウトしました。",
          elapsed_ms: 15001,
          troubleshooting: ["ADB が起動中か確認してください。"],
          details: { timeout_seconds: 15, tcp_connect_timeout_seconds: 10 },
          checked_at: "2026-06-14T00:00:00Z",
          error_type: "OracleConnectionTimeoutError",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.testDatabaseSettings(payload);

    expect(result.elapsed_ms).toBe(15001);
    expect(result.troubleshooting).toContain("ADB が起動中か確認してください。");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/database/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    );
  });

  it("updateUploadStorageSettings は保存先 payload を設定 API へ送る", async () => {
    const payload = {
      backend: "oci" as const,
      local_storage_dir: "/u01/production-ready-rag",
      object_storage_namespace: "example-namespace",
      object_storage_bucket: "rag-originals",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          ...payload,
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

  it("getParserAdapterSettings は adapter readiness API を読む", async () => {
    const payload = {
      adapter_backend: "docling",
      effective_order: ["docling"],
      adapters: [
        {
          backend: "docling",
          package_name: "docling",
          import_name: "docling",
          distribution_name: "docling",
          install_package: "docling==2.103.0",
          enabled: true,
          selected: true,
          installed: true,
          status: "active",
          version: "1.2.3",
          warning_code: null,
        },
        {
          backend: "marker",
          package_name: "marker",
          import_name: "marker",
          distribution_name: null,
          install_package: "marker-pdf[full]==1.10.2",
          enabled: true,
          selected: false,
          installed: false,
          status: "ignored",
          version: null,
          warning_code: "adapter_flag_ignored_by_backend",
        },
        {
          backend: "unstructured",
          package_name: "unstructured",
          import_name: "unstructured",
          distribution_name: null,
          install_package: "unstructured[all-docs]==0.23.1",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
        {
          backend: "unlimited_ocr",
          package_name: "sglang",
          import_name: "sglang",
          distribution_name: null,
          install_package: "sglang + lmsysorg/sglang sidecar (baidu/Unlimited-OCR)",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
        {
          backend: "mineru",
          package_name: "mineru",
          import_name: "mineru",
          distribution_name: null,
          install_package: "mineru[core]==3.4.0",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
      ],
      service_backends: [
        {
          backend: "oci_genai_vision",
          selected: false,
          configured: false,
          warning_code: "enterprise_ai_endpoint_unconfigured",
        },
      ],
      scorecard: {
        selected_backend: "docling",
        recommended_backend: "local",
        metrics_source: "runtime",
        metrics_applied_to: null,
        entries: [
          {
            backend: "local",
            rank: 1,
            score: 62,
            status: "recommended",
            recommended: true,
            executable: true,
            selected: false,
            enabled: true,
            installed: true,
            metric_source: "none",
            metric_count: 0,
            signals: {},
            reason_codes: ["local_parser_available"],
            warning_codes: [],
          },
          {
            backend: "mineru",
            rank: 2,
            score: 24,
            status: "disabled",
            recommended: false,
            executable: false,
            selected: false,
            enabled: false,
            installed: false,
            metric_source: "none",
            metric_count: 0,
            signals: {},
            reason_codes: ["adapter_disabled"],
            warning_codes: [],
          },
        ],
      },
      source_routes: [
        {
          source_kind: "pdf",
          candidate_order: [
            "docling",
            "marker",
            "unstructured",
            "unlimited_ocr",
            "mineru",
            "glm_ocr",
          ],
          attempted_order: ["docling"],
          active_order: ["docling"],
          selected_backend: "docling",
          reason_codes: ["selected_adapter_supported_for_source"],
          warning_codes: [],
        },
      ],
      backend_source_kind_matrix: {
        evidence_source: "runtime_routes",
        required_source_kinds: ["pdf"],
        covered_source_kinds: ["pdf"],
        missing_source_kinds: [],
        backend_source_kinds: { docling: ["pdf"] },
        route_evidence: [],
      },
      config_source: "runtime",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: payload,
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getParserAdapterSettings();

    expect(result.adapter_backend).toBe("docling");
    expect(result.effective_order).toEqual(["docling"]);
    expect(result.adapters[1].warning_code).toBe("adapter_flag_ignored_by_backend");
    expect(result.adapters.map((adapter) => adapter.backend)).toContain("unlimited_ocr");
    expect(result.adapters.map((adapter) => adapter.backend)).toContain("mineru");
    expect(result.service_backends[0].backend).toBe("oci_genai_vision");
    expect(result.source_routes[0].candidate_order).toContain("mineru");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/parser-adapters",
      expect.objectContaining({
        credentials: "same-origin",
      })
    );
  });

  it("getParserAdapterContract は adapter compatibility matrix API を読む", async () => {
    const payload = {
      passed: false,
      fixture_root: "/repo/evaluation/file-processing-fixtures",
      source_kinds: ["pdf", "email"],
      backends: ["docling", "unstructured"],
      case_count: 4,
      blocking_failure_count: 1,
      cases: [
        {
          backend: "docling",
          source_kind: "pdf",
          fixture_name: "policy-ja.pdf",
          content_type: "application/pdf",
          status: "missing",
          blocking: true,
          parser_backend: null,
          parser_version: null,
          adapter_import_name: "docling",
          adapter_distribution_name: null,
          adapter_package_version: null,
          template: null,
          element_count: 0,
          page_count: 0,
          table_count: 0,
          table_cell_count: 0,
          asset_count: 0,
          bbox_count: 0,
          warning_codes: ["adapter_package_missing"],
          reason_codes: ["adapter_missing"],
        },
      ],
      summary: {
        passed: false,
        case_count: 4,
        blocking_failure_count: 1,
        source_kinds: ["pdf", "email"],
        backends: ["docling", "unstructured"],
        passed_source_kinds: [],
        missing_source_kinds: ["email", "pdf"],
        blocking_failure_source_kinds: ["pdf"],
        blocking_failure_backends: ["docling"],
        backend_status_counts: { docling: { missing: 1 } },
        backend_source_status: { docling: { pdf: "missing" } },
        source_kind_status_counts: { pdf: { missing: 1 } },
        backend_passed_source_kinds: {},
        reason_code_counts: { adapter_missing: 1 },
        warning_code_counts: { adapter_package_missing: 1 },
        blocking_failure_reason_counts: { adapter_missing: 1 },
        blocking_failures: [
          {
            backend: "docling",
            source_kind: "pdf",
            status: "missing",
            warning_codes: ["adapter_package_missing"],
            reason_codes: ["adapter_missing"],
          },
        ],
      },
      config_source: "runtime",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: payload,
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getParserAdapterContract();

    expect(result.passed).toBe(false);
    expect(result.blocking_failure_count).toBe(1);
    expect(result.cases[0].adapter_import_name).toBe("docling");
    expect(result.cases[0].adapter_distribution_name).toBeNull();
    expect(result.cases[0].adapter_package_version).toBeNull();
    expect(result.summary.backend_source_status.docling?.pdf).toBe("missing");
    expect(result.summary.missing_source_kinds).toEqual(["email", "pdf"]);
    expect(result.summary.blocking_failure_source_kinds).toEqual(["pdf"]);
    expect(result.summary.blocking_failure_backends).toEqual(["docling"]);
    expect(result.summary.source_kind_status_counts.pdf?.missing).toBe(1);
    expect(JSON.stringify(result)).not.toContain("raw_text");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/parser-adapters/contract",
      expect.objectContaining({
        credentials: "same-origin",
      })
    );
  });

  it("updateParserAdapterSettings は adapter backend と feature flags を保存する", async () => {
    const requestPayload = {
      adapter_backend: "docling" as const,
      docling_enabled: true,
      marker_enabled: false,
      unstructured_enabled: true,
      unlimited_ocr_enabled: false,
      mineru_enabled: false,
      dots_ocr_enabled: false,
      glm_ocr_enabled: false,
    };
    const responsePayload = {
      adapter_backend: "docling",
      effective_order: ["docling"],
      adapters: [
        {
          backend: "docling",
          package_name: "docling",
          import_name: "docling",
          distribution_name: null,
          install_package: "docling==2.103.0",
          enabled: true,
          selected: true,
          installed: false,
          status: "missing",
          version: null,
          warning_code: "adapter_package_missing",
        },
        {
          backend: "marker",
          package_name: "marker",
          import_name: "marker",
          distribution_name: null,
          install_package: "marker-pdf[full]==1.10.2",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
        {
          backend: "unstructured",
          package_name: "unstructured",
          import_name: "unstructured",
          distribution_name: null,
          install_package: "unstructured[all-docs]==0.23.1",
          enabled: true,
          selected: false,
          installed: false,
          status: "ignored",
          version: null,
          warning_code: "adapter_flag_ignored_by_backend",
        },
        {
          backend: "unlimited_ocr",
          package_name: "sglang",
          import_name: "sglang",
          distribution_name: null,
          install_package: "sglang + lmsysorg/sglang sidecar (baidu/Unlimited-OCR)",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
        {
          backend: "dots_ocr",
          package_name: "dots_ocr",
          import_name: "dots_ocr",
          distribution_name: null,
          install_package: "git+https://github.com/rednote-hilab/dots.ocr.git",
          enabled: false,
          selected: false,
          installed: false,
          status: "disabled",
          version: null,
          warning_code: null,
        },
      ],
      service_backends: [],
      scorecard: {
        selected_backend: "docling",
        recommended_backend: "local",
        metrics_source: "runtime",
        metrics_applied_to: null,
        entries: [],
      },
      source_routes: [],
      backend_source_kind_matrix: {
        evidence_source: "runtime_routes",
        required_source_kinds: [],
        covered_source_kinds: [],
        missing_source_kinds: [],
        backend_source_kinds: {},
        route_evidence: [],
      },
      config_source: "runtime",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: responsePayload,
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updateParserAdapterSettings(requestPayload);

    expect(result.effective_order).toEqual(["docling"]);
    expect(result.adapters.map((adapter) => adapter.backend)).toContain("unlimited_ocr");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/parser-adapters",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(requestPayload),
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

  it("updateOciSettings は OCI config 保存 payload を設定 API へ送る", async () => {
    const payload = {
      user: "ocid1.user.oc1..example",
      fingerprint: "12:34:56:78:90:ab:cd:ef",
      tenancy: "ocid1.tenancy.oc1..example",
      region: "ap-osaka-1",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          config_file: "~/.oci/config",
          profile: "DEFAULT",
          user: payload.user,
          fingerprint: payload.fingerprint,
          tenancy: payload.tenancy,
          region: payload.region,
          key_file: "~/.oci/oci_api_key.pem",
          key_file_exists: false,
          config_file_exists: true,
          config_source: "runtime",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updateOciSettings(payload);

    expect(result.config_file_exists).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/oci",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(payload),
      })
    );
  });

  it("updateOciObjectStorageSettings は Object Storage payload を設定 API へ送る", async () => {
    const payload = {
      object_storage_region: "us-chicago-1",
      object_storage_namespace: "mytenancynamespace",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          backend: "local",
          local_storage_dir: "/u01/production-ready-rag",
          object_storage_region: payload.object_storage_region,
          object_storage_namespace: payload.object_storage_namespace,
          object_storage_bucket: "",
          readiness: "ok",
          max_upload_bytes: 209715200,
          config_source: "runtime",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updateOciObjectStorageSettings(payload);

    expect(result.object_storage_region).toBe("us-chicago-1");
    expect(result.object_storage_namespace).toBe("mytenancynamespace");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/oci/object-storage",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(payload),
      })
    );
  });

  it("testOciConfig は保存済み OCI config のテスト API を呼ぶ", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          status: "success",
          profile: "DEFAULT",
          config_file: "~/.oci/config",
          key_file: "~/.oci/oci_api_key.pem",
          config_file_exists: true,
          key_file_exists: true,
          missing_fields: [],
          permission_issues: [],
          oci_directory_mode: "0700",
          config_file_mode: "0600",
          key_file_mode: "0600",
          message: "OCI config と秘密鍵ファイルを確認できました。",
          checked_at: "2026-06-14T00:00:00Z",
          error_type: null,
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.testOciConfig();

    expect(result.status).toBe("success");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/oci/config/test",
      expect.objectContaining({ method: "POST" })
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

describe("api.services", () => {
  it("getServiceCatalog は /api/services/catalog からプローブなし一覧を取り出す", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          control_enabled: true,
          deployment_mode: "dev",
          services: [
            {
              service_id: "parser-docling",
              category: "parser",
              profile: "cpu",
              label_key: "settings.services.item.parserDocling",
              execution_policy: "selected_adapter",
              configured: true,
            },
          ],
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getServiceCatalog();

    expect(result.deployment_mode).toBe("dev");
    expect(result.services[0].service_id).toBe("parser-docling");
    expect(fetchMock).toHaveBeenCalledWith("/api/services/catalog", expect.anything());
  });

  it("getServiceStatus は service_id を URL エンコードして状態を取り出す", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          service_id: "parser-dots-ocr",
          category: "parser",
          profile: "gpu",
          label_key: "settings.services.item.parserDotsOcr",
          execution_policy: "selected_adapter",
          status: "running",
          configured: true,
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getServiceStatus("parser-dots-ocr");

    expect(result.status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/parser-dots-ocr/status",
      expect.anything()
    );
  });

  it("getServices は /api/services から一覧を取り出す", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          control_enabled: false,
          services: [
            {
              service_id: "parser-docling",
              category: "parser",
              profile: "cpu",
              label_key: "settings.services.item.parserDocling",
              execution_policy: "selected_adapter",
              status: "stopped",
              configured: true,
            },
          ],
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getServices();

    expect(result.control_enabled).toBe(false);
    expect(result.services[0].service_id).toBe("parser-docling");
    expect(fetchMock).toHaveBeenCalledWith("/api/services", expect.anything());
  });

  it("controlService は service_id を URL エンコードして POST する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: { service_id: "parser-mineru", action: "start", status: "running" },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.controlService("parser-mineru", "start");

    expect(result.status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/parser-mineru/start",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("getServiceLogs は service_id と lines を URL エンコードして取得する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        data: {
          service_id: "parser-docling",
          source: "docker",
          lines: 200,
          content: "ready",
        },
        error_messages: [],
        warning_messages: [],
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getServiceLogs("parser-docling", 200);

    expect(result.content).toBe("ready");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/parser-docling/logs?lines=200",
      expect.anything()
    );
  });
});
