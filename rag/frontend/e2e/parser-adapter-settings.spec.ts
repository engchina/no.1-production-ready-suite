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
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`Parser adapter 設定は runtime readiness を表示する (${viewport.name})`, async ({ page }) => {
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

    await expect(page.getByRole("heading", { name: "Parser アダプター" })).toBeVisible();
    await expect(page.getByText("Docling -> Marker", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Active", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Missing", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("package 未導入", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("導入: pip install marker-pdf[full]==1.10.2")).toBeVisible();
    await expect(page.getByText("backend 選択外", { exact: true }).first()).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /OCI Document Understanding/ })
    ).toBeVisible();
    await expect(page.getByRole("radio", { name: /Enterprise AI VLM/ })).toBeVisible();
    await expect(page.getByText("未設定", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("OCI サービス", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Source routing matrix")).toBeVisible();
    await expect(page.getByText("PDF", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Docling -> Marker -> Unstructured")).toBeVisible();
    await expect(page.getByText("音声は未対応")).toBeVisible();
    await expect(page.getByText("標準 parser を優先")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Schema remap 契約" })).toBeVisible();
    await expect(page.getByText("Schema remap 契約は未実行です。")).toBeVisible();
    await page.getByRole("button", { name: "互換性を確認" }).click();
    await expect(page.getByText("失敗", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("Contract code summary")).toBeVisible();
    await expect(page.getByText("阻害 reason", { exact: true })).toBeVisible();
    await expect(page.getByText("Warning 分布", { exact: true })).toBeVisible();
    await expect(page.getByText("Reason 分布", { exact: true })).toBeVisible();
    await expect(page.getByText("未導入 / blocking")).toBeVisible();
    await expect(
      page.getByText("Runtime 証跡", { exact: true }).nth(viewport.width >= 768 ? 0 : 1)
    ).toBeVisible();
    await expect(page.getByText("docling 1.2.3", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("pdf_fixture:hash-policy", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("要素 1 / ページ 1 / 表 0 / セル 0 / アセット 0 / BBox 1")).toBeVisible();
    await expect(page.getByText("schema remap 成功", { exact: true })).toBeVisible();

    const navLink = page.getByRole("link", { name: "Parser アダプター" });
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

test("Parser adapter 設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["Parser adapter 設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");

  await expect(page.getByRole("alert")).toContainText(
    "Parser adapter 設定を取得できませんでした。"
  );
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("Parser adapter 設定は backend と feature flag を保存できる", async ({ page }) => {
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
          adapter_backend: "auto",
          effective_order: ["docling", "unstructured"],
          config_source: "runtime",
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
              install_package: "unstructured[all-docs]==0.18.32",
              enabled: true,
              selected: true,
              installed: false,
              status: "missing",
              version: null,
              warning_code: "adapter_package_missing",
            },
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
        ],
      }),
    });
  });

  await page.goto("/settings/parser-adapters");

  const autoBackend = page.getByRole("radio", { name: /Auto/ });
  await autoBackend.focus();
  await expect(autoBackend).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(autoBackend).toHaveAttribute("aria-checked", "true");

  await page.getByRole("switch", { name: "Docling adapter feature flag" }).click();
  await page.getByRole("switch", { name: "Unstructured adapter feature flag" }).click();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("Parser adapter 設定を保存しました。")).toBeVisible();
  await expect(page.getByText("Docling -> Unstructured")).toBeVisible();
  expect(savedPayload).toEqual({
    adapter_backend: "auto",
    docling_enabled: true,
    marker_enabled: false,
    unstructured_enabled: true,
  });
  await expectNoHorizontalOverflow(page);
});

async function mockParserAdapters(page: Page) {
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      json: parserAdapterEnvelope({
          adapter_backend: "auto",
          effective_order: ["docling", "marker"],
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
              selected: true,
              installed: false,
              status: "missing",
              version: null,
              warning_code: "adapter_package_missing",
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
          backend: "enterprise_ai_vlm",
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
          docling: ["pdf", "office", "html"],
          unstructured: ["image", "email"],
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
      candidate_order: ["docling", "marker", "unstructured"],
      attempted_order: ["docling", "marker"],
      active_order: ["docling"],
      selected_backend: "docling",
      reason_codes: ["source_aware_auto_order", "active_adapter_available_for_source"],
      warning_codes: ["marker_adapter_package_missing"],
    },
    {
      source_kind: "image",
      candidate_order: ["unstructured", "marker", "docling"],
      attempted_order: ["unstructured", "marker", "docling"],
      active_order: ["unstructured"],
      selected_backend: "unstructured",
      reason_codes: ["source_aware_auto_order", "active_adapter_available_for_source"],
      warning_codes: [],
    },
    {
      source_kind: "email",
      candidate_order: ["unstructured"],
      attempted_order: ["unstructured"],
      active_order: ["unstructured"],
      selected_backend: "unstructured",
      reason_codes: ["source_aware_auto_order", "active_adapter_available_for_source"],
      warning_codes: [],
    },
    {
      source_kind: "audio",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["audio_transcription_not_configured", "source_aware_auto_order"],
      warning_codes: ["unsupported_audio", "audio_transcription_not_configured"],
    },
    {
      source_kind: "text",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["local_parser_preferred_for_source", "source_aware_auto_order"],
      warning_codes: [],
    },
  ];
}

function disabledAdapter(backend: "docling" | "marker" | "unstructured") {
  return {
    backend,
    package_name: backend,
    import_name: backend,
    distribution_name: null,
    install_package:
      backend === "marker"
        ? "marker-pdf[full]==1.10.2"
        : backend === "unstructured"
          ? "unstructured[all-docs]==0.18.32"
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
