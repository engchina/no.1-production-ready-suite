import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  SYSTEM_TABLES_MESSAGES,
  SYSTEM_TABLES_QUERY_KEY,
  SystemTablesCard,
  isSystemTableRecreateConfirmationValid,
  isSystemTablesStatusData,
  systemObjectTypeMessageKey,
  SYSTEM_TABLES_IDLE_REFETCH_MS,
  SYSTEM_TABLES_RUNNING_REFETCH_MAX_MS,
  SYSTEM_TABLES_RUNNING_REFETCH_MS,
  systemTableCompletedOperation,
  systemTableControlsBusy,
  systemTablesRefetchInterval,
  systemTableDetailCounts,
  systemTableObjects,
  type SystemTableSchemaStatus,
  type SystemTablesApi,
  type SystemTablesCardProps,
  type SystemTablesStatusData,
} from "../src";

const PHRASE = "RECREATE_DEMO_SYSTEM_TABLES";
const routes = { databaseSettings: "/settings/database#adb-management", systemTables: "/settings/system-tables" };
const api: SystemTablesApi = {
  getSystemTablesStatus: () => new Promise<never>(() => undefined),
  initializeSystemTables: () => new Promise<never>(() => undefined),
};

function statusData(
  status: SystemTableSchemaStatus,
  overrides: Partial<SystemTablesStatusData> = {},
): SystemTablesStatusData {
  return {
    status,
    schema_head: 17,
    applied_versions: [0, 1, 2],
    pending_versions: status === "outdated" ? [17] : [],
    expected_object_count: 3,
    existing_object_count: status === "missing" ? 0 : status === "partial" ? 2 : 3,
    expected_table_count: 2,
    existing_table_count: status === "missing" ? 0 : 2,
    missing_objects: status === "partial" ? [{ name: "DEMO_IDX", object_type: "INDEX" }] : [],
    tables: [
      {
        name: "DEMO_ITEMS",
        exists: status !== "missing",
        estimated_rows: 12,
        created_at: "2026-07-19T00:00:00Z",
        last_analyzed_at: null,
      },
    ],
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: 7,
      updated_at: null,
    },
    ...overrides,
  };
}

function clientWith(data?: unknown, error = false) {
  // 失敗した状態を描くため、mount 時の取り直しをしない。
  const client = new QueryClient({ defaultOptions: { queries: { retryOnMount: false, staleTime: Infinity } } });
  const query = client.getQueryCache().build(client, { queryKey: SYSTEM_TABLES_QUERY_KEY });
  if (data !== undefined) client.setQueryData(SYSTEM_TABLES_QUERY_KEY, data);
  if (error) query.setState({ status: "error", error: new Error("503"), fetchStatus: "idle" });
  return client;
}

/** 「作成・更新」「すべて再作成」のボタンの開始タグ。 */
function initializeButton(html: string): string {
  return html.match(/<button(?:(?!<button).)*?作成・更新<\/button>/s)?.[0].match(/<button[^>]*>/)?.[0] ?? "";
}

function recreateButton(html: string): string {
  return html.match(/<button(?:(?!<button).)*?すべて再作成<\/button>/s)?.[0].match(/<button[^>]*>/)?.[0] ?? "";
}

function renderCard(client: QueryClient, props: Partial<SystemTablesCardProps> = {}) {
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SystemTablesCard
          api={api}
          canManage
          recreateConfirmation={PHRASE}
          databaseRoutes={routes}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("判定の helper", () => {
  it("確認語は前後の空白を除いた完全一致だけを許す", () => {
    expect(isSystemTableRecreateConfirmationValid(PHRASE, PHRASE)).toBe(true);
    expect(isSystemTableRecreateConfirmationValid(`  ${PHRASE} `, PHRASE)).toBe(true);
    expect(isSystemTableRecreateConfirmationValid(PHRASE.toLowerCase(), PHRASE)).toBe(false);
    expect(isSystemTableRecreateConfirmationValid("ADMIN_EXECUTE", PHRASE)).toBe(false);
    expect(isSystemTableRecreateConfirmationValid("", PHRASE)).toBe(false);
  });

  it("状態の取り直しは、操作中は最初の 1 分 4 秒・その後は延ばして最大 30 秒、平時は 60 秒（#5278）", () => {
    const now = Date.parse("2026-11-17T01:00:00Z");
    const runningSince = (ms: number | null) =>
      statusData("ready", {
        operation_state: {
          ...statusData("ready").operation_state,
          status: "running",
          started_at: ms == null ? null : new Date(now - ms).toISOString(),
        },
      });
    expect(systemTablesRefetchInterval(undefined, now)).toBe(SYSTEM_TABLES_IDLE_REFETCH_MS);
    expect(systemTablesRefetchInterval(statusData("ready"), now)).toBe(60_000);
    expect(
      systemTablesRefetchInterval(
        statusData("ready", { operation_state: { ...statusData("ready").operation_state, status: "stale" } }),
        now,
      ),
    ).toBe(60_000);
    expect(systemTablesRefetchInterval(runningSince(null), now)).toBe(SYSTEM_TABLES_RUNNING_REFETCH_MS);
    expect(systemTablesRefetchInterval(runningSince(0), now)).toBe(4_000);
    expect(systemTablesRefetchInterval(runningSince(60_000), now)).toBe(4_000);
    expect(systemTablesRefetchInterval(runningSince(61_000), now)).toBe(6_000);
    expect(systemTablesRefetchInterval(runningSince(121_000), now)).toBe(9_000);
    expect(systemTablesRefetchInterval(runningSince(181_000), now)).toBe(13_500);
    expect(systemTablesRefetchInterval(runningSince(301_000), now)).toBe(30_000);
    expect(systemTablesRefetchInterval(runningSince(3_600_000), now)).toBe(SYSTEM_TABLES_RUNNING_REFETCH_MAX_MS);
    // 時計のずれで開始が未来でも、最初の間隔にする。
    expect(systemTablesRefetchInterval(runningSince(-30_000), now)).toBe(4_000);
  });

  it("見ていた操作の結果は、同じ epoch の要約を優先し、無ければ epoch の差と種類から求める（#5278）", () => {
    const idle = (epoch: number, extra: Partial<SystemTablesStatusData> = {}) =>
      statusData("ready", { operation_state: { ...statusData("ready").operation_state, schema_epoch: epoch }, ...extra });
    const watched = { kind: "initialize" as const, epoch: 7, schemaStatus: "outdated" as const };
    expect(systemTableCompletedOperation(watched, idle(7))?.operation).toBe("no_op");
    expect(systemTableCompletedOperation(watched, idle(8))?.operation).toBe("migrated");
    expect(systemTableCompletedOperation({ ...watched, schemaStatus: "missing" }, idle(8))?.operation).toBe(
      "initialized",
    );
    expect(systemTableCompletedOperation({ ...watched, kind: "recreate" }, idle(8))?.operation).toBe("recreated");
    const restart = { message: "再起動", processes: ["dispatcher"] };
    expect(
      systemTableCompletedOperation(
        watched,
        idle(8, { last_operation: { operation: "migrated", schema_epoch: 8, restart_required: restart } }),
      ),
    ).toEqual({ operation: "migrated", restartRequired: restart });
    // 別の（古い）操作の要約は使わない。
    expect(
      systemTableCompletedOperation(
        watched,
        idle(9, { last_operation: { operation: "no_op", schema_epoch: 8, restart_required: restart } }),
      ),
    ).toEqual({ operation: "migrated", restartRequired: null });
    const failed = statusData("ready", {
      operation_state: { ...statusData("ready").operation_state, status: "failed", schema_epoch: 7 },
    });
    expect(systemTableCompletedOperation(watched, failed)).toBeNull();
  });

  it("mutation か DB の lease が実行中なら操作を止める（stale は止めない）", () => {
    expect(systemTableControlsBusy(false, "idle")).toBe(false);
    expect(systemTableControlsBusy(false, "stale")).toBe(false);
    expect(systemTableControlsBusy(false, "failed")).toBe(false);
    expect(systemTableControlsBusy(true, "idle")).toBe(true);
    expect(systemTableControlsBusy(false, "running")).toBe(true);
  });

  it("描画に必要な項目が無い payload を弾く（retired_objects は任意）", () => {
    expect(isSystemTablesStatusData(statusData("ready"))).toBe(true);
    expect(isSystemTablesStatusData({ ...statusData("ready"), retired_objects: [] })).toBe(true);
    expect(isSystemTablesStatusData({ ...statusData("ready"), retired_objects: null })).toBe(false);
    expect(isSystemTablesStatusData({ ...statusData("ready"), missing_foreign_keys: [] })).toBe(true);
    expect(isSystemTablesStatusData({ ...statusData("ready"), missing_foreign_keys: {} })).toBe(false);
    expect(isSystemTablesStatusData({ ...statusData("ready"), orphaned_foreign_keys: "x" })).toBe(false);
    expect(isSystemTablesStatusData({ ...statusData("ready"), mismatched_foreign_keys: [] })).toBe(true);
    expect(isSystemTablesStatusData({ ...statusData("ready"), mismatched_foreign_keys: {} })).toBe(false);
    expect(isSystemTablesStatusData({ ...statusData("ready"), disabled_foreign_keys: 1 })).toBe(false);
    expect(isSystemTablesStatusData({ ...statusData("ready"), tables: null })).toBe(false);
    expect(isSystemTablesStatusData({ status: "ready" })).toBe(false);
    expect(isSystemTablesStatusData(null)).toBe(false);
  });

  it("全管理 object が無い応答はテーブルだけを、テーブルの件数で並べる", () => {
    const tablesOnly = statusData("ready");
    expect(systemTableObjects(tablesOnly).map((row) => row.object_type)).toEqual(["TABLE"]);
    expect(systemTableDetailCounts(tablesOnly)).toEqual({ existing: 2, expected: 2 });

    const withObjects = statusData("ready", {
      objects: [{ ...tablesOnly.tables[0], object_type: "INDEX" }],
    });
    expect(systemTableObjects(withObjects).map((row) => row.object_type)).toEqual(["INDEX"]);
    expect(systemTableDetailCounts(withObjects)).toEqual({ existing: 3, expected: 3 });
  });

  it("object の種類の表示名は既知の種類だけ", () => {
    expect(systemObjectTypeMessageKey("PACKAGE BODY")).toBe(
      "settings.database.systemTables.table.objectType.package body",
    );
    expect(systemObjectTypeMessageKey("TEXT_PREFERENCE")).toBeUndefined();
  });
});

describe("SystemTablesCard の取得状態と操作可否の分離", () => {
  it.each([
    ["成功", statusData("ready"), false],
    ["404終了", undefined, true],
    ["再取得失敗・既存データあり", statusData("ready"), true],
    ["不正な応答で終了", { status: "ready" }, false],
  ] as const)("%s の後は処理中を解除する", (_label, data, error) => {
    const html = renderCard(clientWith(data, error));
    expect(html.match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="false"');
    if (error && data) {
      // 取得失敗時は古い状態を根拠に危険な操作を再開しない。
      expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*?作成・更新/s);
      expect(html).toMatch(/<input[^>]*disabled=""/s);
    }
  });

  it("初回取得は処理中を示し、定期の取り直しは処理中にも操作無効にもしない（#5278）", () => {
    expect(renderCard(new QueryClient()).match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="true"');
    const client = clientWith(statusData("ready"));
    client.getQueryCache().find({ queryKey: SYSTEM_TABLES_QUERY_KEY })!.setState({ fetchStatus: "fetching" });
    const html = renderCard(client);
    expect(html.match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="false"');
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>(?:(?!<button).)*?作成・更新/s);
  });

  it("外の操作（CLI・別のタブ）でも、操作中は「作成・更新」を回し、ボタンの行の直下に実行中と経過を出す（#5278）", () => {
    const startedAt = new Date(Date.now() - 3 * 60_000 - 5_000).toISOString();
    const data = statusData("ready", {
      operation_state: {
        ...statusData("ready").operation_state,
        status: "running",
        operation_kind: "initialize",
        started_at: startedAt,
      },
    });
    const html = renderCard(clientWith(data));
    expect(html.match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="true"');
    const initialize = initializeButton(html);
    // loading（Spinner・押せない）で、ネイティブの disabled は付けない（フォーカスを外さない。buttons.md §8）。
    expect(initialize).toContain('aria-busy="true"');
    expect(initialize).toContain('aria-disabled="true"');
    expect(initialize).not.toContain('disabled=""');
    // ラベルは変えず、状態の文言はボタンの行の直下の共有の処理中の表示（灰色の枠）に出す。
    expect(html).toContain('data-testid="system-tables-operation-progress"');
    expect(html).toContain('data-processing-placement="action"');
    expect(html).toContain('data-processing-surface="framed"');
    // ボタンが loading を出すので、処理中の表示は印を出さない（動くスピナーは 1 つ）。
    expect(html).toContain('data-processing-activity-icon="none"');
    expect(html).toMatch(/data-processing-label="true">作成・更新を実行中</);
    // 経過は backend の開始時刻から共有の形式（mm:ss）で数え、開始の時刻は出さない。
    expect(html).toMatch(/data-testid="system-tables-operation-progress-timer"[^>]*>.*03:0[4-6]/);
    expect(html).not.toContain("開始 ");
    // 全再作成は loading にしない（押せないだけ）。
    expect(recreateButton(html)).not.toContain('aria-busy="true"');
    expect(recreateButton(html)).toContain('disabled=""');
  });

  it("全再作成の操作中は「すべて再作成」を回し、「作成・更新」は押せないだけにする（#5278）", () => {
    const data = statusData("ready", {
      operation_state: {
        ...statusData("ready").operation_state,
        status: "running",
        operation_kind: "recreate",
        started_at: new Date(Date.now() - 10_000).toISOString(),
      },
    });
    const html = renderCard(clientWith(data));
    expect(recreateButton(html)).toContain('aria-busy="true"');
    expect(recreateButton(html)).not.toContain('disabled=""');
    expect(initializeButton(html)).toContain('disabled=""');
    expect(initializeButton(html)).not.toContain('aria-busy="true"');
    expect(html).toMatch(/data-processing-label="true">すべて再作成を実行中</);
    expect(html).toMatch(/data-testid="system-tables-operation-progress-timer"[^>]*>.*00:1[0-2]/);
  });

  it("lease の期限を過ぎた操作（stale）は回さず、止まった可能性を出して再実行できる（#5278）", () => {
    const data = statusData("ready", {
      operation_state: {
        ...statusData("ready").operation_state,
        status: "stale",
        operation_kind: "initialize",
        started_at: "2026-11-17T01:00:00Z",
        lease_expires_at: "2026-11-17T01:15:00Z",
      },
    });
    const html = renderCard(clientWith(data));
    expect(html.match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="false"');
    expect(html).toContain("停止の可能性");
    expect(html).toContain('data-testid="system-tables-stale-operation"');
    expect(html).toContain("前回の操作が止まった可能性があります");
    expect(html).not.toContain('data-testid="system-tables-operation-progress"');
    const initialize = initializeButton(html);
    expect(initialize).not.toContain('aria-busy="true"');
    expect(initialize).not.toContain('disabled=""');
  });

  it("閲覧専用の取得完了は処理中でなく、管理操作を追加しない", () => {
    const html = renderCard(clientWith(statusData("ready")), { canManage: false });
    expect(html.match(/<[^>]+id="system-tables"[^>]*>/)?.[0]).toContain('aria-busy="false"');
    expect(html).not.toContain("すべて再作成");
    expect(html).not.toContain("作成・更新</span>");
  });
});

describe("SystemTablesCard の状態ごとの表示", () => {
  it("読み込み中は経過時間付きの読み込み表示と Skeleton だけを出す", () => {
    const html = renderCard(new QueryClient());
    expect(html).toContain('data-testid="system-tables-loading"');
    expect(html).toContain(SYSTEM_TABLES_MESSAGES["settings.database.systemTables.loading"]);
    expect(html).not.toContain(SYSTEM_TABLES_MESSAGES["settings.database.systemTables.action.initialize"]);
  });

  it.each([
    ["missing", "未初期化", "主要テーブルがまだありません"],
    ["partial", "一部不足", "必須オブジェクトが 1 件不足しています"],
    ["outdated", "更新必要", "migration version または checksum が古く"],
  ] as const)("%s は状態のバッジと対処の案内を出す", (status, label, hint) => {
    const html = renderCard(clientWith(statusData(status)));
    expect(html).toContain(label);
    expect(html).toContain(hint);
  });

  it("ready は案内を出さず、要約（件数・head・epoch）と操作を出す", () => {
    const html = renderCard(clientWith(statusData("ready")));
    expect(html).toContain("初期化済み");
    expect(html).not.toContain('role="alert"');
    expect(html).toContain("2 / 2");
    expect(html).toContain("3 / 3");
    expect(html).toContain("v17");
    expect(html).toContain("作成・更新");
    expect(html).toContain("最新情報を取得");
    expect(html).toContain('data-testid="execution-confirmation-field"');
    expect(html).toContain(`入力条件: <span class="font-mono`);
    expect(html).toContain(`placeholder="${PHRASE}"`);
    // 確認語が未入力の間は全再作成を押せない。
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*?すべて再作成/s);
  });

  it("不足している外部キーを更新必要の案内に並べ、参照先のない行の件数を添える（#505）", () => {
    const foreignKey = {
      name: "DEMO_ITEMS_PARENT_FK",
      table_name: "DEMO_ITEMS",
      columns: ["PARENT_ID"],
      referenced_table_name: "DEMO_PARENTS",
      referenced_columns: ["PARENT_ID"],
      delete_rule: "CASCADE",
      orphan_rows: 1240,
    };
    const html = renderCard(
      clientWith(statusData("outdated", { pending_versions: [], missing_foreign_keys: [foreignKey] })),
    );
    expect(html).toContain("更新必要");
    expect(html).toContain('data-testid="system-tables-missing-foreign-keys"');
    expect(html).toContain("既存のテーブルに外部キーが 1 件ありません");
    expect(html).toContain("DEMO_ITEMS (PARENT_ID) → DEMO_PARENTS");
    expect(html).toContain("参照先のない行 1,240 件");
    expect(html).not.toContain('data-testid="system-tables-orphaned-foreign-keys"');
  });

  it("台帳が最新でも既存の表に無い列を一覧で出す（#2450）", () => {
    const html = renderCard(
      clientWith(
        statusData("outdated", {
          pending_versions: [],
          missing_columns: [{ table_name: "DEMO_ITEMS", column_name: "PAYLOAD" }],
        }),
      ),
    );
    expect(html).toContain("更新必要");
    expect(html).toContain('data-testid="system-tables-missing-columns"');
    expect(html).toContain("既存のテーブルに必須の列が 1 件ありません");
    expect(html).toContain("DEMO_ITEMS.PAYLOAD");
    expect(isSystemTablesStatusData({ ...statusData("ready"), missing_columns: {} })).toBe(false);
  });

  it("参照先のない既存の行が残る外部キーは ready でも警告を出す（#505）", () => {
    const html = renderCard(
      clientWith(
        statusData("ready", {
          orphaned_foreign_keys: [
            {
              name: "DEMO_ITEMS_PARENT_FK",
              table_name: "DEMO_ITEMS",
              columns: ["PARENT_ID"],
              referenced_table_name: "DEMO_PARENTS",
              referenced_columns: ["PARENT_ID"],
              delete_rule: "CASCADE",
              orphan_rows: 3,
            },
          ],
        }),
      ),
    );
    expect(html).toContain("初期化済み");
    expect(html).toContain('data-testid="system-tables-orphaned-foreign-keys"');
    expect(html).toContain("参照先のない既存の行があります");
    expect(html).toContain("既存の行は自動では削除しません");
    expect(html).toContain("参照先のない行 3 件");
    expect(html).not.toContain('data-testid="system-tables-missing-foreign-keys"');
  });

  it("削除規則が違う外部キーと無効な外部キーを更新必要の案内に並べる（#511）", () => {
    const base = {
      table_name: "DEMO_ITEMS",
      columns: ["PARENT_ID"],
      referenced_table_name: "DEMO_PARENTS",
      referenced_columns: ["PARENT_ID"],
      delete_rule: "CASCADE",
    };
    const html = renderCard(
      clientWith(
        statusData("outdated", {
          pending_versions: [],
          mismatched_foreign_keys: [
            {
              ...base,
              name: "DEMO_ITEMS_PARENT_FK",
              orphan_rows: 0,
              current_name: "LEGACY_FK",
              current_delete_rule: "NO ACTION",
            },
          ],
          disabled_foreign_keys: [{ ...base, name: "DEMO_ITEMS_SELF_FK", orphan_rows: 4 }],
        }),
      ),
    );
    expect(html).toContain('data-testid="system-tables-mismatched-foreign-keys"');
    expect(html).toContain("削除規則が正本と異なる外部キーが 1 件あります");
    expect(html).toContain("削除規則 NO ACTION → CASCADE");
    expect(html).toContain('data-testid="system-tables-disabled-foreign-keys"');
    expect(html).toContain("無効になっている外部キーが 1 件あります");
    expect(html).toContain("参照先のない行 4 件");
  });

  it("参照先のない行の削除は、権限・API・確認ダイアログがそろったときだけ出す（#511）", () => {
    const data = statusData("ready", {
      orphaned_foreign_keys: [
        {
          name: "DEMO_ITEMS_PARENT_FK",
          table_name: "DEMO_ITEMS",
          columns: ["PARENT_ID"],
          referenced_table_name: "DEMO_PARENTS",
          referenced_columns: ["PARENT_ID"],
          delete_rule: "CASCADE",
          orphan_rows: 3,
        },
      ],
    });
    const withDelete: SystemTablesApi = {
      ...api,
      deleteSystemTableOrphanedRows: () => new Promise<never>(() => undefined),
    };
    const confirm = () => Promise.resolve(true);
    const html = renderCard(clientWith(data), { api: withDelete, confirmDeleteOrphans: confirm });
    expect(html).toContain('aria-label="参照先のない行を削除 DEMO_ITEMS_PARENT_FK"');
    expect(html).toContain("「参照先のない行を削除」で削除できます");

    for (const props of [
      { api: withDelete },
      { confirmDeleteOrphans: confirm },
      { api: withDelete, confirmDeleteOrphans: confirm, canManage: false },
    ]) {
      const hidden = renderCard(clientWith(data), props);
      expect(hidden).toContain("参照先のない行 3 件");
      expect(hidden).not.toContain("参照先のない行を削除 DEMO_ITEMS_PARENT_FK");
    }
  });

  it("名前の head は並び順の番号（v + 番号）で出し、名前は補足に出す。製品の文言で上書きできる（#658）", () => {
    const html = renderCard(
      clientWith(
        statusData("outdated", {
          schema_head: "20260703_002_feedback",
          applied_versions: ["20260701_001_a", "20260702_001_b"],
          pending_versions: ["20260703_002_feedback"],
        }),
      ),
      { messages: { "settings.database.systemTables.title": "RAG システムテーブル" } },
    );
    expect(html).toContain("v3");
    expect(html).toContain("20260703_002_feedback");
    expect(html).toContain("RAG システムテーブル");
  });

  it("適用済みの migration は件数だけを出し、未適用・不一致だけを名前で並べる（#658）", () => {
    const html = renderCard(clientWith(statusData("outdated")));
    expect(html).toContain("適用済みの migration 3 件 / 未適用・不一致 1 件");
    expect(html).toContain("未適用・不一致: v17");
    expect(html).not.toContain("v0, v1, v2");
  });

  it("権限が無い利用者には状態と再取得だけを出す", () => {
    const html = renderCard(clientWith(statusData("missing")), { canManage: false });
    // Banner は文ごとに分けて描くので、文単位で確かめる。
    expect(html).toContain("状態は参照できます。");
    expect(html).toContain("作成・更新するにはシステムテーブル管理権限が必要です。");
    expect(html).toContain("最新情報を取得");
    expect(html).not.toContain("作成・更新</button>");
    expect(html).not.toContain("execution-confirmation-field");
  });

  it("操作中（lease が running）は操作中のバッジを出し、作成・更新を止める", () => {
    const running = statusData("partial", {
      operation_state: { ...statusData("partial").operation_state, status: "running", operation_kind: "initialize" },
    });
    const html = renderCard(clientWith(running));
    expect(html).toContain("操作中");
    expect(html).toContain('aria-busy="true"');
    // 作成・更新は loading（押せない）で、ネイティブの disabled は重ねない（#5278）。
    expect(initializeButton(html)).toContain('aria-disabled="true"');
    expect(initializeButton(html)).not.toContain('disabled=""');
  });

  it("前回の失敗は本文に対処を出し、エラーコードは開いた「詳細」に分ける（messaging.md §10.3。#722）", () => {
    const failed = (code: string | null) =>
      statusData("partial", {
        operation_state: { ...statusData("partial").operation_state, status: "failed", last_error_code: code },
      });
    const generic = renderCard(clientWith(failed("ORA-00600")));
    expect(generic).toContain(">状態を再取得し、「作成・更新」で再試行してください。</p>");
    expect(generic).toMatch(/<details[^>]*open=""[^>]*>[\s\S]*?詳細[\s\S]*?エラーコード: ORA-00600/);
    const lock = renderCard(clientWith(failed("ORA-00054")));
    expect(lock).toContain(">Oracle の対象オブジェクトのロックが待機時間内に解放されませんでした。");
    expect(lock).toContain("エラーコード: ORA-00054");
    // コードが無いときは「詳細」を出さない。
    const unknown = renderCard(clientWith(failed(null)));
    expect(unknown).toContain("前回の操作が完了していません");
    expect(unknown).not.toContain("エラーコード");
  });

  it("前回の失敗の説明を製品が返したときは、汎用の対処の代わりに本文に出す（#5261）", () => {
    const detail = "vector memory の不足（ORA-51995）で HNSW 索引（DEMO_HNSW_IDX）を作れませんでした。";
    const html = renderCard(
      clientWith(
        statusData("partial", {
          operation_state: {
            ...statusData("partial").operation_state,
            status: "failed",
            last_error_code: "ORA-51995",
            last_error_detail: detail,
          },
        }),
      ),
    );
    expect(html).toContain(`<p class="[overflow-wrap:anywhere]">${detail}</p>`);
    expect(html).not.toContain("状態を再取得し、「作成・更新」で再試行してください。");
    expect(html).toContain("エラーコード: ORA-51995");
  });

  it("状態を取得できないときは DB の案内（banner）と再試行を出し、操作を出さない", () => {
    const html = renderCard(clientWith(undefined, true));
    expect(html).toContain("データベースに接続できません");
    expect(html).toContain("再試行");
    expect(html).not.toContain("作成・更新");
  });

  it("想定外の形の payload は描画せず、取得失敗として扱う", () => {
    const html = renderCard(clientWith({ status: "ready" }));
    expect(html).toContain("データベースに接続できません");
    expect(html).not.toContain("作成・更新");
  });

  it("詳細の表は既定で所有者付きの名前と種類を出し、製品の表示に差し替えられる", () => {
    const data = statusData("ready", {
      objects: [
        {
          name: "DEMO_ITEMS",
          owner: "APP",
          qualified_name: "APP.DEMO_ITEMS",
          object_type: "TABLE",
          exists: true,
          estimated_rows: 1200,
          created_at: null,
          last_analyzed_at: null,
        },
        {
          name: "DEMO_IDX",
          object_type: "INDEX",
          exists: false,
          estimated_rows: null,
          created_at: null,
          last_analyzed_at: null,
        },
      ],
    });
    const html = renderCard(clientWith(data));
    expect(html).toContain("管理オブジェクトの詳細を表示（存在 3 / 必須 3）");
    expect(html).toContain("APP.DEMO_ITEMS");
    expect(html).toContain("1,200");
    expect(html).toContain("索引");
    expect(html).toContain("対象外");
    expect(html).toContain('data-testid="system-tables-scroll-region"');
    // 一覧の件数はツールバーの ListCount（#1891）。点検の一覧なのでページ送りは出さない。
    expect(html).toMatch(/data-testid="system-tables-count"[^>]*>全 2 件</);
    expect(html).not.toContain("data-pagination");

    const custom = renderCard(clientWith(data), {
      renderObjectName: (object) => <strong>{`name:${object.name}`}</strong>,
    });
    expect(custom).toContain("<strong>name:DEMO_IDX</strong>");
  });
});

describe("vector memory の見積もり（#5278・#5473）", () => {
  const index = {
    name: "DEMO_EMBEDDING_HNSW_IDX",
    table_name: "DEMO_EMBEDDINGS",
    column_name: "EMBEDDING",
    loaded: false,
    estimated_bytes: 80 * 1048576,
    used_bytes: null,
  };

  it("HNSW 索引ごとに上段のカードを 1 枚足し、索引名と 3 つの値を出す（警告・説明は出さない。#5473）", () => {
    const html = renderCard(
      clientWith(
        statusData("partial", {
          vector_memory: {
            available: true,
            pool_allocated_bytes: 100 * 1048576,
            pool_used_bytes: 60 * 1048576,
            pool_free_bytes: 40 * 1048576,
            indexes: [index],
          },
        }),
      ),
    );
    expect(html).toContain('data-testid="system-tables-vector-memory"');
    expect(html).toContain("DEMO_EMBEDDING_HNSW_IDX");
    // 載っていない索引の使用中は「—」。
    expect(html).toContain("80.0 MB / 40.0 MB / —");
    expect(html).toContain("見込みの必要量 / 今の空き / 使用中");
    expect(html).toContain("今の空きは目安です（Vector Pool は自動で増減）。");
    expect(html).not.toContain("vector memory の見積もり");
    expect(html).not.toContain("超えています");
  });

  it("載っている索引は使用中の量を出す", () => {
    const html = renderCard(
      clientWith(
        statusData("ready", {
          vector_memory: {
            available: true,
            pool_free_bytes: 10 * 1048576,
            indexes: [{ ...index, loaded: true, used_bytes: 75 * 1048576 }],
          },
        }),
      ),
    );
    expect(html).toContain("80.0 MB / 10.0 MB / 75.0 MB");
  });

  it("取れない環境（権限・ビューが無い）では取れない値を「—」にする", () => {
    const html = renderCard(
      clientWith(
        statusData("ready", {
          vector_memory: {
            available: false,
            error_code: "ORA-00942",
            indexes: [{ ...index, loaded: null, estimated_bytes: null, error_code: "ORA-06550" }],
          },
        }),
      ),
    );
    expect(html).toContain("— / — / —");
  });

  it("HNSW 索引を持たない製品（vector_memory が無い）は出さない", () => {
    expect(renderCard(clientWith(statusData("ready")))).not.toContain('data-testid="system-tables-vector-memory"');
  });
});
