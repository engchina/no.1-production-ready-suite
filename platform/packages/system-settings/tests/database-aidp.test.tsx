// @vitest-environment happy-dom
import { ConfirmProvider } from "@production-ready/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ADB_INFO_QUERY_KEY,
  DATABASE_SETTINGS_QUERY_KEY,
  DatabaseSettingsPage,
  type AdbInfoData,
  type AidpIntegrationData,
  type DatabaseSettingsApi,
  type DatabaseSettingsData,
} from "../src";

// #5931: 「システム設定 › データベース」の Oracle AI Data Platform の区画（読み取り専用）。
// OCID が設定されているときだけ出し、未設定なら区画も API の呼び出しも無い。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const SETTINGS: DatabaseSettingsData = {
  user: "app",
  dsn: "appdb_high",
  driver_mode: "thin",
  connection_security: "wallet_mtls",
  client_lib_dir: "",
  wallet_dir: "/wallet",
  wallet_uploaded: true,
  available_services: ["appdb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "EMBEDDING",
  adb_ocid: "ocid1.autonomousdatabase.oc1..example",
  region: "ap-osaka-1",
  aidp_configured: true,
  config_source: "runtime",
};

const ADB_NOT_CONFIGURED: AdbInfoData = {
  status: "not_configured",
  message: "ADB OCID が設定されていません。",
};

const AIDP: AidpIntegrationData = {
  configured: true,
  url: "https://aidp.example.invalid/?ocid=ocid1.aidataplatform.oc1..example",
  ocid: "ocid1.aidataplatform.oc1..example",
  region: "ap-osaka-1",
  adb_db_name: "appdb",
  adb_service_name: "appdb_high",
  db_user: "ADB_AIDP_USER",
  db_user_status: "exists",
};

const never = () => new Promise<never>(() => undefined);

function apiWith(
  getAidpIntegration: DatabaseSettingsApi["getAidpIntegration"],
): DatabaseSettingsApi {
  return {
    getDatabaseSettings: never,
    updateDatabaseSettings: never,
    uploadDatabaseWallet: never,
    downloadDatabaseWallet: never,
    testDatabaseSettings: never,
    getAdbInfo: never,
    updateAdbSettings: never,
    startAdb: never,
    stopAdb: never,
    getAidpIntegration,
  };
}

async function renderPage(
  api: DatabaseSettingsApi,
  {
    settings = SETTINGS,
    adbInfo = ADB_NOT_CONFIGURED,
  }: { settings?: DatabaseSettingsData; adbInfo?: AdbInfoData } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(DATABASE_SETTINGS_QUERY_KEY, settings);
  client.setQueryData(ADB_INFO_QUERY_KEY, adbInfo);
  // 同じテストで描き直すときは、前の画面（前の QueryClient の状態）を外してから描く。
  await act(async () => {
    root.render(null);
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ConfirmProvider>
            <DatabaseSettingsPage api={api} />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

/** query の結果の通知（タイマー）まで進める。 */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const card = () =>
  host.querySelector<HTMLElement>('[data-testid="settings-aidp-card"]');

function fieldValue(id: string) {
  const input = host.querySelector<HTMLInputElement>(`#${id}`);
  if (!input) throw new Error(`欄 ${id} が無い`);
  return input;
}

describe("Oracle AI Data Platform の区画", () => {
  it("OCID が未設定なら区画を出さず、API も呼ばない", async () => {
    const getAidpIntegration = vi.fn(() => Promise.resolve(AIDP));
    for (const settings of [
      { ...SETTINGS, aidp_configured: false },
      // 古い backend の応答（項目が無い）も未設定として扱う。
      (({ aidp_configured: _omitted, ...rest }) => rest)(SETTINGS),
    ]) {
      await renderPage(apiWith(getAidpIntegration), { settings });
      expect(card()).toBeNull();
    }
    expect(getAidpIntegration).not.toHaveBeenCalled();
  });

  it("URL のリンク・OCID・リージョンと、External Catalog の登録に使う値を読み取り専用で出す", async () => {
    await renderPage(apiWith(() => Promise.resolve(AIDP)));

    const region = card();
    expect(region).not.toBeNull();
    expect(region?.textContent).toContain("Oracle AI Data Platform");

    const link = host.querySelector<HTMLAnchorElement>(
      '[data-testid="settings-aidp-open-link"]',
    );
    expect(link?.getAttribute("href")).toBe(AIDP.url);
    // 新しいタブで開き、開いた先に元の画面を渡さない。
    expect(link?.target).toBe("_blank");
    expect(link?.rel).toBe("noopener noreferrer");

    for (const [id, value] of [
      ["aidp-url", AIDP.url],
      ["aidp-ocid", AIDP.ocid],
      ["aidp-region", "ap-osaka-1"],
      ["aidp-catalog-db-name", "appdb"],
      ["aidp-catalog-service-name", "appdb_high"],
      ["aidp-catalog-db-user", "ADB_AIDP_USER"],
    ] as const) {
      const input = fieldValue(id);
      expect(input.value).toBe(value);
      expect(input.readOnly).toBe(true);
    }

    // 状態は見出しの StatusBadge（アイコン + 文字）。在るときは案内を足さない。
    const badge = region?.querySelector('[data-status-variant="success"]');
    expect(badge?.textContent).toBe("DB ユーザー: 作成済み");
    expect(region?.querySelector('[role="status"], [role="alert"]')).toBeNull();

    // 読み取り専用: パスワードの欄も、登録・編集の操作も無い（あるのは再取得だけ）。
    expect(region?.querySelector('input[type="password"]')).toBeNull();
    expect(region?.textContent).not.toContain("保存");
    expect(
      Array.from(region?.querySelectorAll("button") ?? []).map((button) =>
        button.textContent?.trim(),
      ),
    ).toEqual(["情報を再取得"]);
    expect(region?.textContent).toContain(
      "External Catalog の登録は Oracle AI Data Platform の画面で行います",
    );
  });

  it("ADB の DB 名は、ADB の情報が取れていればその値を使う", async () => {
    await renderPage(apiWith(() => Promise.resolve(AIDP)), {
      adbInfo: {
        status: "success",
        message: "データベース情報を取得しました。",
        lifecycle_state: "AVAILABLE",
        db_name: "APPDB",
      },
    });
    expect(fieldValue("aidp-catalog-db-name").value).toBe("APPDB");
  });

  it("DB ユーザーが無い・確かめられないときは、状態と案内を出す", async () => {
    await renderPage(
      apiWith(() => Promise.resolve({ ...AIDP, db_user_status: "missing" })),
    );
    expect(
      card()?.querySelector('[data-status-variant="warning"]')?.textContent,
    ).toBe("DB ユーザー: 未作成");
    expect(card()?.textContent).toContain(
      "Autonomous Database に DB ユーザー ADB_AIDP_USER がありません。",
    );

    await renderPage(
      apiWith(() => Promise.resolve({ ...AIDP, db_user_status: "unknown" })),
    );
    expect(
      card()?.querySelector('[data-status-variant="neutral"]')?.textContent,
    ).toBe("DB ユーザー: 確認できません");
    expect(card()?.textContent).toContain("データベースに接続できないため");

    await renderPage(
      apiWith(() =>
        Promise.resolve({ ...AIDP, db_user: "", db_user_status: "unknown" }),
      ),
    );
    expect(card()?.textContent).toContain("PLATFORM_AIDP_DB_USER");
    expect(card()?.textContent).not.toContain("データベースに接続できないため");
  });

  it("https ではない URL はリンクにしない", async () => {
    await renderPage(apiWith(() => Promise.resolve({ ...AIDP, url: "" })));
    expect(
      host.querySelector('[data-testid="settings-aidp-open-link"]'),
    ).toBeNull();
    expect(card()?.textContent).toContain("PLATFORM_AIDP_URL");

    // API は https 以外を返さないが、画面でもリンクにしない。
    await renderPage(
      apiWith(() => Promise.resolve({ ...AIDP, url: "javascript:alert(1)" })),
    );
    expect(host.querySelector('[data-testid="settings-aidp-card"] a')).toBeNull();
  });

  it("読み込み中は経過時間付きの表示、失敗は区画の中の再試行にする（画面全体は失敗にしない）", async () => {
    await renderPage(apiWith(never));
    expect(
      host.querySelector('[data-testid="settings-aidp-loading"]'),
    ).not.toBeNull();
    expect(card()?.textContent).toContain(
      "Oracle AI Data Platform の連携の情報を読み込んでいます",
    );

    let fail = true;
    const getAidpIntegration = vi.fn(() =>
      fail ? Promise.reject(new Error("failed")) : Promise.resolve(AIDP),
    );
    await renderPage(apiWith(getAidpIntegration));
    const alert = card()?.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(
      "Oracle AI Data Platform の連携の情報を取得できませんでした。",
    );
    // ほかの区画（接続設定）は出たまま。
    expect(host.textContent).toContain("データベース設定");

    fail = false;
    const retry = alert?.querySelector("button");
    await act(async () => {
      retry?.click();
    });
    await settle();
    expect(fieldValue("aidp-ocid").value).toBe(AIDP.ocid);
  });

  it("設定の読み直しで未設定に変わったら、区画を出さない", async () => {
    await renderPage(
      apiWith(() =>
        Promise.resolve({
          configured: false,
          url: "",
          ocid: "",
          region: "",
          adb_db_name: "",
          adb_service_name: "",
          db_user: "",
          db_user_status: "unknown",
        }),
      ),
    );
    expect(card()).toBeNull();
  });
});
