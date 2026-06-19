import { afterEach, describe, expect, it } from "vitest";

import { UI_STORAGE_KEY, useUiStore } from "./ui-store";

afterEach(() => {
  useUiStore.getState().setSidebarCollapsed(false);
  useUiStore.setState({ collapsedSections: {} });
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

  it("セクションの折りたたみは既定で空（=全展開）", () => {
    expect(useUiStore.getState().collapsedSections).toEqual({});
  });

  it("toggleSection でセクション開閉を切り替える", () => {
    const key = "nav.section.pipeline";

    useUiStore.getState().toggleSection(key);
    expect(useUiStore.getState().collapsedSections[key]).toBe(true);

    useUiStore.getState().toggleSection(key);
    expect(useUiStore.getState().collapsedSections[key]).toBe(false);
  });

  it("setSectionCollapsed で他セクションを保ったまま明示設定する", () => {
    useUiStore.getState().setSectionCollapsed("nav.section.rag", true);
    useUiStore.getState().setSectionCollapsed("nav.section.settings", false);

    expect(useUiStore.getState().collapsedSections).toEqual({
      "nav.section.rag": true,
      "nav.section.settings": false,
    });
  });

  it("collapsedSections を persist の対象に含める", () => {
    useUiStore.getState().setSectionCollapsed("nav.section.pipeline", true);

    const persisted = useUiStore.getState();
    expect(persisted.collapsedSections["nav.section.pipeline"]).toBe(true);
  });
});
