import { describe, expect, it } from "vitest";

import {
  RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION,
  isSystemTableRecreateConfirmationValid,
  isSystemTablesStatusData,
  systemTableControlsBusy,
} from "./system-tables";

const statusPayload = {
  status: "ready",
  schema_version: "0003",
  schema_head: "0003",
  applied_versions: [],
  pending_versions: [],
  expected_object_count: 96,
  existing_object_count: 96,
  expected_table_count: 24,
  existing_table_count: 24,
  missing_objects: [],
  retired_objects: [],
  tables: [],
  operation_state: {
    status: "idle",
    operation_kind: null,
    lease_expires_at: null,
    last_error_code: null,
    schema_epoch: 3,
    updated_at: null,
  },
};

describe("system table controls", () => {
  it("mutation または DB lease 中は重複操作を止める", () => {
    expect(systemTableControlsBusy(true, "idle")).toBe(true);
    expect(systemTableControlsBusy(false, "running")).toBe(true);
    expect(systemTableControlsBusy(false, "failed")).toBe(false);
    expect(systemTableControlsBusy(false, "idle")).toBe(false);
  });

  it("全再作成の確認語は完全一致だけを許可する", () => {
    expect(
      isSystemTableRecreateConfirmationValid(
        RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION
      )
    ).toBe(true);
    expect(
      isSystemTableRecreateConfirmationValid(
        ` ${RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION} `
      )
    ).toBe(true);
    expect(isSystemTableRecreateConfirmationValid("RECREATE_RAG_TABLES")).toBe(
      false
    );
    expect(isSystemTableRecreateConfirmationValid("")).toBe(false);
  });
});

describe("system table status payload guard", () => {
  it("必須フィールドが揃った payload だけを受け入れる", () => {
    expect(isSystemTablesStatusData(statusPayload)).toBe(true);
  });

  it("operation_state 欠落の payload を弾く", () => {
    const withoutOperationState: Record<string, unknown> = { ...statusPayload };
    delete withoutOperationState.operation_state;
    expect(isSystemTablesStatusData(withoutOperationState)).toBe(false);
  });

  it("別 API の payload（データベース設定）を弾く", () => {
    expect(
      isSystemTablesStatusData({
        user: "rag",
        dsn: "ragdb_high",
        readiness: "ok",
        config_source: "runtime",
      })
    ).toBe(false);
  });

  it("null / undefined / 非オブジェクトを弾く", () => {
    expect(isSystemTablesStatusData(null)).toBe(false);
    expect(isSystemTablesStatusData(undefined)).toBe(false);
    expect(isSystemTablesStatusData("ready")).toBe(false);
  });

  it("配列フィールドが配列でない payload を弾く", () => {
    expect(
      isSystemTablesStatusData({ ...statusPayload, tables: null })
    ).toBe(false);
  });
});
