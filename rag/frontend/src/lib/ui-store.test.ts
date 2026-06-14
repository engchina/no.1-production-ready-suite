import { afterEach, describe, expect, it } from "vitest";

import { UI_STORAGE_KEY, useUiStore } from "./ui-store";

afterEach(() => {
  useUiStore.getState().setSidebarCollapsed(false);
});

describe("useUiStore", () => {
  it("sidebar の折りたたみ状態を切り替える", () => {
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);

    useUiStore.getState().toggleSidebarCollapsed();

    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
  });

  it("sidebar の折りたたみ状態を明示的に設定する", () => {
    useUiStore.getState().setSidebarCollapsed(true);

    expect(useUiStore.getState().sidebarCollapsed).toBe(true);

    useUiStore.getState().setSidebarCollapsed(false);

    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("ブラウザ storage がなくても persist middleware を初期化できる", () => {
    expect(UI_STORAGE_KEY).toBe("production-ready-rag.ui");
    expect(useUiStore.persist.hasHydrated()).toBe(true);
  });
});
