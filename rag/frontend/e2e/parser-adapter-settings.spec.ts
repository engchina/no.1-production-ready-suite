import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  await mockParserServiceStatuses(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`文書解析設定は稼働状況を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockParserAdapters(page);
    await mockParserAdapterContract(page);

    await page.goto("/settings/parser-adapters");

    await expect(page.getByRole("heading", { name: "文書解析" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Local/ })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /Docling.*CPU.*稼働中/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Marker.*CPU.*停止/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Unstructured.*CPU.*縮退/ })).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /Unlimited-OCR.*GPU.*停止/ })
    ).toBeVisible();
    await expect(page.getByRole("radio", { name: /MinerU.*GPU.*停止/ })).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /Dots\.OCR.*GPU.*停止/ })
    ).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /OCI Generative AI \(Vision\).*OCI.*稼働中/ })
    ).toBeVisible();
    const engineNames = (await page.getByRole("radio").allTextContents()).map((text) =>
      text.replace(/\s+/g, " ").trim()
    );
    expect(engineNames[0]).toContain("Docling");
    expect(engineNames[1]).toContain("Marker");
    expect(engineNames[2]).toContain("Unstructured");
    expect(engineNames[3]).toContain("Unlimited-OCR");
    expect(engineNames[4]).toContain("MinerU");
    expect(engineNames[5]).toContain("Dots.OCR");
    expect(engineNames[6]).toContain("GLM-OCR");
    expect(engineNames[7]).toContain("OCI Generative AI (Vision)");
    expect(engineNames[8]).toContain("OCI Document Understanding");
    await page.getByText("運用診断", { exact: true }).click();
    await expect(page.getByText("解析方式の稼働状況")).toHaveCount(0);
    await expect(page.getByText("原本種別ごとの実行順")).toHaveCount(0);
    await expect(page.getByText("未導入", { exact: true })).toHaveCount(0);
    await expect(page.getByText("パッケージ未導入", { exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("radio", { name: /OCI Document Understanding/ })
    ).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /OCI Generative AI \(Vision\)/ })
    ).toBeVisible();
    await expect(page.getByText("未設定", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "StructuredExtraction 互換性確認" })).toBeVisible();
    await expect(page.getByText("StructuredExtraction 互換性確認は未実行です。")).toBeVisible();
    await page.getByRole("button", { name: "互換性を確認" }).click();
    await expect(page.getByText("失敗", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("コード別サマリ")).toBeVisible();
    await expect(page.getByText("阻害理由", { exact: true })).toBeVisible();
    await expect(page.getByText("警告分布", { exact: true })).toBeVisible();
    await expect(page.getByText("理由分布", { exact: true })).toBeVisible();
    await expect(page.getByText("未確認 / 阻害")).toBeVisible();
    await expect(page.getByText("未導入", { exact: true })).toHaveCount(0);
    await expect(page.getByText("パッケージ未導入", { exact: true })).toHaveCount(0);
    await expect(
      page.getByText("現在の設定の証跡", { exact: true }).nth(viewport.width >= 768 ? 0 : 1)
    ).toBeVisible();
    await expect(page.getByText("docling 1.2.3", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("pdf_fixture:hash-policy", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("要素 1 / ページ 1 / 表 0 / セル 0 / アセット 0 / BBox 1")).toBeVisible();
    await expect(page.getByText("schema remap 成功", { exact: true })).toBeVisible();

    const navLink = page.getByRole("link", { name: "文書解析" });
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await navLink.focus();
    await expect(navLink).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings\/parser-adapters$/);
    await expectNoHorizontalOverflow(page);
  });
}

async function mockParserAdapterContract(page: Page) {
  await page.route("**/api/settings/parser-adapters/contract", async (route) => {
    await route.fulfill({
      json: {
        data: {
          passed: false,
          fixture_root: "fixture_root:hash-fixtures",
          source_kinds: ["pdf", "email"],
          backends: ["docling", "marker", "unstructured"],
          case_count: 3,
          blocking_failure_count: 1,
          cases: [
            {
              backend: "docling",
              source_kind: "pdf",
              fixture_name: "pdf_fixture:hash-policy",
              content_type: "application/pdf",
              status: "passed",
              blocking: true,
              parser_backend: "docling",
              parser_version: "1.2.3",
              adapter_import_name: "docling",
              adapter_distribution_name: "docling",
              adapter_package_version: "1.2.3",
              template: "pdf_layout",
              element_count: 1,
              page_count: 1,
              table_count: 0,
              table_cell_count: 0,
              asset_count: 0,
              bbox_count: 1,
              warning_codes: [],
              reason_codes: ["schema_remap_contract_ok"],
            },
            {
              backend: "marker",
              source_kind: "pdf",
              fixture_name: "pdf_fixture:hash-policy",
              content_type: "application/pdf",
              status: "missing",
              blocking: true,
              parser_backend: null,
              parser_version: null,
              adapter_import_name: "marker",
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
            {
              backend: "unstructured",
              source_kind: "email",
              fixture_name: "email_fixture:hash-approval",
              content_type: "message/rfc822",
              status: "available",
              blocking: false,
              parser_backend: null,
              parser_version: null,
              adapter_import_name: "unstructured",
              adapter_distribution_name: "unstructured",
              adapter_package_version: "0.18.32",
              template: null,
              element_count: 0,
              page_count: 0,
              table_count: 0,
              table_cell_count: 0,
              asset_count: 0,
              bbox_count: 0,
              warning_codes: [],
              reason_codes: ["adapter_available"],
            },
          ],
          summary: {
            passed: false,
            case_count: 3,
            blocking_failure_count: 1,
            source_kinds: ["pdf", "email"],
            backends: ["docling", "marker", "unstructured"],
            passed_source_kinds: ["pdf"],
            backend_status_counts: {
              docling: { passed: 1 },
              marker: { missing: 1 },
              unstructured: { available: 1 },
            },
            backend_source_status: {
              docling: { pdf: "passed" },
              marker: { pdf: "missing" },
              unstructured: { email: "available" },
            },
            reason_code_counts: {
              schema_remap_contract_ok: 1,
              adapter_missing: 1,
              adapter_available: 1,
            },
            warning_code_counts: { adapter_package_missing: 1 },
            blocking_failure_reason_counts: { adapter_missing: 1 },
            blocking_failures: [
              {
                backend: "marker",
                source_kind: "pdf",
                status: "missing",
                warning_codes: ["adapter_package_missing"],
                reason_codes: ["adapter_missing"],
              },
            ],
          },
          config_source: "runtime",
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

test("文書解析設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["文書解析設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");

  await expect(page.getByRole("alert")).toContainText(
    "文書解析設定を取得できませんでした。"
  );
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書解析設定は使用エンジンを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/parser-adapters", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: parserAdapterEnvelope({
          adapter_backend: "mineru",
          effective_order: ["mineru"],
          config_source: "runtime",
          adapters: [
            disabledAdapter("docling"),
            disabledAdapter("marker"),
            disabledAdapter("unstructured"),
            disabledAdapter("unlimited_ocr"),
            { ...disabledAdapter("mineru"), enabled: true, selected: true, status: "active" },
            disabledAdapter("dots_ocr"),
            disabledAdapter("glm_ocr"),
          ],
        }),
      });
      return;
    }
    await route.fulfill({
      json: parserAdapterEnvelope({
        adapter_backend: "local",
        effective_order: [],
        config_source: "runtime",
        adapters: [
          disabledAdapter("docling"),
          disabledAdapter("marker"),
          disabledAdapter("unstructured"),
          disabledAdapter("unlimited_ocr"),
          disabledAdapter("mineru"),
          disabledAdapter("dots_ocr"),
          disabledAdapter("glm_ocr"),
        ],
      }),
    });
  });

  await page.goto("/settings/parser-adapters");

  // local は廃止。microservice エンジン(MinerU)を選択する。
  await expect(page.getByRole("radio", { name: /^Local/ })).toHaveCount(0);
  const mineruBackend = page.getByRole("radio", { name: /MinerU/ });
  await mineruBackend.focus();
  await expect(mineruBackend).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(mineruBackend).toHaveAttribute("aria-checked", "true");

  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("文書解析設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    adapter_backend: "mineru",
    docling_enabled: false,
    marker_enabled: false,
    unstructured_enabled: false,
    unlimited_ocr_enabled: false,
    mineru_enabled: true,
    dots_ocr_enabled: false,
    glm_ocr_enabled: false,
  });
  await expectNoHorizontalOverflow(page);
});

async function mockParserAdapters(page: Page) {
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      json: parserAdapterEnvelope({
          adapter_backend: "docling",
          effective_order: ["docling"],
          config_source: "runtime",
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
              install_package: "unstructured[all-docs]==0.18.32",
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
              install_package:
                "parser-unlimited-ocr image (official SGLang wheel + baidu/Unlimited-OCR)",
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
            {
              backend: "glm_ocr",
              package_name: "transformers",
              import_name: "transformers",
              distribution_name: null,
              install_package: "transformers (zai-org/GLM-OCR via HuggingFace)",
              enabled: false,
              selected: false,
              installed: false,
              status: "disabled",
              version: null,
              warning_code: null,
            },
          ],
      }),
    });
  });
}

function parserAdapterEnvelope(data: object) {
  const sourceRoutes = defaultSourceRoutes();
  return {
    data: {
      source_routes: sourceRoutes,
      service_backends: [
        {
          backend: "oci_genai_vision",
          selected: false,
          configured: true,
          warning_code: null,
        },
        {
          backend: "oci_document_understanding",
          selected: false,
          configured: false,
          warning_code: "oci_document_understanding_unconfigured",
        },
      ],
      backend_source_kind_matrix: {
        evidence_source: "runtime_routes",
        required_source_kinds: ["pdf", "image", "office", "html", "email", "audio", "text", "unknown"],
        covered_source_kinds: ["pdf", "image", "office", "html", "email", "audio", "text", "unknown"],
        missing_source_kinds: [],
        backend_source_kinds: {
          docling: ["pdf", "image", "office", "html"],
          unlimited_ocr: ["pdf", "image"],
          local: ["audio", "text", "unknown"],
        },
        route_evidence: sourceRoutes,
      },
      ...data,
    },
    error_messages: [],
    warning_messages: [],
  };
}

function defaultSourceRoutes() {
  return [
    {
      source_kind: "pdf",
      candidate_order: ["docling", "marker", "unstructured", "unlimited_ocr", "mineru", "glm_ocr"],
      attempted_order: ["docling", "marker"],
      active_order: ["docling"],
      selected_backend: "docling",
      reason_codes: ["selected_adapter_supported_for_source", "active_adapter_available_for_source"],
      warning_codes: ["marker_adapter_package_missing"],
    },
    {
      source_kind: "image",
      candidate_order: [
        "unstructured",
        "marker",
        "docling",
        "dots_ocr",
        "unlimited_ocr",
        "mineru",
        "glm_ocr",
      ],
      attempted_order: ["docling"],
      active_order: ["docling"],
      selected_backend: "docling",
      reason_codes: ["selected_adapter_supported_for_source", "active_adapter_available_for_source"],
      warning_codes: [],
    },
    {
      source_kind: "email",
      candidate_order: ["unstructured"],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["selected_adapter_unsupported_for_source"],
      warning_codes: ["docling_adapter_source_unsupported"],
    },
    {
      source_kind: "audio",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["audio_transcription_not_configured", "selected_adapter_unsupported_for_source"],
      warning_codes: ["unsupported_audio", "audio_transcription_not_configured"],
    },
    {
      source_kind: "text",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["local_parser_preferred_for_source", "selected_adapter_unsupported_for_source"],
      warning_codes: [],
    },
  ];
}

async function mockParserServiceStatuses(page: Page) {
  const statuses: Record<string, string> = {
    "parser-docling": "running",
    "parser-marker": "stopped",
    "parser-unstructured": "degraded",
    "parser-unlimited-ocr": "stopped",
    "parser-mineru": "stopped",
    "parser-dots-ocr": "stopped",
    "parser-glm-ocr": "stopped",
    "parser-oci-genai-vision": "running",
    "parser-oci-document-understanding": "unconfigured",
  };
  await page.route("**/api/services/*/status", async (route) => {
    const serviceId = decodeURIComponent(
      route.request().url().match(/services\/([^/]+)\/status/)?.[1] ?? ""
    );
    const status = statuses[serviceId];
    await route.fulfill({
      status: status ? 200 : 404,
      json: {
        data: status
          ? {
              service_id: serviceId,
              category: "parser",
              profile: serviceProfileForId(serviceId),
              label_key: "settings.services.item.parserDocling",
              execution_policy: "selected_adapter",
              configured: status !== "unconfigured",
              status,
            }
          : null,
        error_messages: status ? [] : ["指定したサービスが見つかりません。"],
        warning_messages: [],
      },
    });
  });
}

function serviceProfileForId(serviceId: string) {
  if (serviceId.includes("oci")) return "oci";
  if (
    serviceId.includes("mineru") ||
    serviceId.includes("unlimited-ocr") ||
    serviceId.includes("dots-ocr") ||
    serviceId.includes("glm-ocr")
  ) {
    return "gpu";
  }
  return "cpu";
}

function disabledAdapter(
  backend:
    | "docling"
    | "marker"
    | "unstructured"
    | "unlimited_ocr"
    | "mineru"
    | "dots_ocr"
    | "glm_ocr"
) {
  return {
    backend,
    package_name:
      backend === "unlimited_ocr" ? "sglang" : backend === "glm_ocr" ? "transformers" : backend,
    import_name:
      backend === "unlimited_ocr" ? "sglang" : backend === "glm_ocr" ? "transformers" : backend,
    distribution_name: null,
    install_package:
      backend === "marker"
        ? "marker-pdf[full]==1.10.2"
        : backend === "unstructured"
          ? "unstructured[all-docs]==0.18.32"
          : backend === "unlimited_ocr"
            ? "parser-unlimited-ocr image (official SGLang wheel + baidu/Unlimited-OCR)"
            : backend === "mineru"
              ? "mineru[core]==3.4.0"
              : backend === "dots_ocr"
                ? "git+https://github.com/rednote-hilab/dots.ocr.git"
                : backend === "glm_ocr"
                  ? "transformers (zai-org/GLM-OCR via HuggingFace)"
                  : "docling==2.103.0",
    enabled: false,
    selected: false,
    installed: false,
    status: "disabled",
    version: null,
    warning_code: null,
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
