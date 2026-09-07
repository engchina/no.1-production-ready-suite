import { expect, test } from "@playwright/test";

import { SYSTEM_TABLES_STATUS_OK } from "./_helpers";

/**
 * カード単位 error boundary の隔離検証(#67)。
 *
 * 設定ページは独立した API と責務を持つカードを兄弟として並べているため、boundary が無いと
 * 1 枚の描画例外でページ全体が unmount される(実例: #63)。ここでは system table カードだけを
 * 意図的に throw させ、**同じページの他カードが生き残ること**を検証する。
 */

const databaseSettings = {
  user: "rag_app",
  dsn: "adb.ap-osaka-1.oraclecloud.com/ragdb_high",
  wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
  wallet_uploaded: true,
  available_services: ["ragdb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  adb_ocid: "ocid1.autonomousdatabase.oc1..rag",
  region: "ap-osaka-1",
  config_source: "runtime" as const,
};

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

const adbInfo = {
  data: {
    status: "success",
    message: "データベース情報を取得しました。",
    id: "ocid1.autonomousdatabase.oc1..rag",
    display_name: "RAG ADB",
    lifecycle_state: "STOPPED",
    db_name: "ragdb",
    cpu_core_count: 2,
    data_storage_size_in_tbs: 1,
    region: "ap-osaka-1",
  },
  error_messages: [],
  warning_messages: [],
};

/**
 * 型ガード(#63)は通過するが、詳細表示で throw する payload。
 *
 * `applied_versions` / `pending_versions` はカード内の `<details>` で `.join()` されるため、
 * 配列でないと描画中に TypeError になる。型ガードはカード上部の描画に必要な項目だけを見る
 * 浅い検査なので、この種の深い階層の破綻は boundary 側で受け止める設計。
 */
const systemTablesThrowingPayload = {
  data: {
    ...SYSTEM_TABLES_STATUS_OK.data,
    applied_versions: null,
    pending_versions: null,
  },
  error_messages: [],
  warning_messages: [],
};

test("system table カードが throw しても ADB 管理カードは表示され続ける", async ({
  page,
}) => {
  // 描画例外による console.error は想定内。テスト失敗の材料にしない。
  page.on("pageerror", () => {});

  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/settings/database**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings/database/system-tables") {
      await route.fulfill({ json: systemTablesThrowingPayload });
      return;
    }
    if (url.pathname.startsWith("/api/settings/database/adb")) {
      await route.fulfill({ json: adbInfo });
      return;
    }
    await route.fulfill({
      json: { data: databaseSettings, error_messages: [], warning_messages: [] },
    });
  });

  await page.goto("/settings/database");

  // 例外を起こしたカードは、そのカードだけがエラー表示へ差し替わる。
  await expect(page.getByText("「RAG システムテーブル」を表示できません")).toBeVisible();

  // 兄弟カード(ADB 管理)はページから消えず、値も描画されている。
  await expect(
    page.getByRole("heading", { name: "Autonomous Database 管理" })
  ).toBeVisible();
  await expect(page.getByLabel("ADB OCID")).toHaveValue(
    "ocid1.autonomousdatabase.oc1..rag"
  );
  await expect(page.getByText("状態: 停止済み")).toBeVisible();

  // データベース接続設定フォームも残る(ページ見出しとカード見出しの 2 箇所)。
  await expect(
    page.getByRole("heading", { name: "データベース設定" }).first()
  ).toBeVisible();
});
