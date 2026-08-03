import { describe, expect, it } from "vitest";

import {
  RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION,
  isSystemTableRecreateConfirmationValid,
  systemTableControlsBusy,
} from "./system-tables";

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
