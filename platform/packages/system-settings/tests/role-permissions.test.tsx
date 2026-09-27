import { ConfirmProvider } from "@engchina/production-ready-ui";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  RolePermissionsPage,
  effectivePermissionCodes,
  permissionInheritanceSources,
  targetItemLabel,
  type PermissionDefinition,
  type PermissionRole,
  type RolePermissionTargetSection,
  type RolePermissionsApi,
} from "../src";

const pending = () => new Promise<never>(() => undefined);

interface RagRole extends PermissionRole {
  business_view_ids: string[];
  knowledge_base_ids: string[];
}

const api: RolePermissionsApi<RagRole> = { roles: pending, permissions: pending, save: pending };

function targetMessages(title: string) {
  return {
    title,
    all: `すべての${title}`,
    searchLabel: `${title}を検索`,
    searchPlaceholder: "名前・説明で絞り込み",
    empty: `${title}がありません。`,
    noResults: `条件に一致する${title}がありません。`,
    loadWarning: `${title}の候補を読み込めませんでした。詳細: {message}`,
    grantsAllByPermission: `管理権限により、すべての${title}を利用できます。`,
    grantsAllSystemAdmin: `SYSTEM_ADMIN はすべての${title}を利用できます。`,
  };
}

// RAG の業務ビュー・ナレッジベースを targets として渡す例。
const ragTargets: RolePermissionTargetSection<RagRole>[] = [
  {
    key: "business-views",
    messages: targetMessages("業務ビュー"),
    load: pending,
    selectedIds: (role) => role.business_view_ids,
    grantsAll: (codes) => codes.has("rag.business_views.manage"),
  },
  {
    key: "knowledge-bases",
    messages: targetMessages("ナレッジベース"),
    load: pending,
    selectedIds: (role) => role.knowledge_base_ids,
  },
];

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={["/settings/security/permissions?role=role-1"]}>
      <ConfirmProvider>{node}</ConfirmProvider>
    </MemoryRouter>,
  );
}

const catalog: PermissionDefinition[] = [
  { code: "menu.search", group: "メニュー", label: "検索", description: "", implies: ["rag.read"] },
  { code: "rag.read", group: "参照", label: "参照", description: "", implies: ["rag.meta.read"] },
  { code: "rag.meta.read", group: "参照", label: "メタ参照", description: "", implies: [] },
];

describe("RolePermissionsPage", () => {
  it("読み込み中は状態表示を出し、NL2SQL と同じテスト ID と対象ごとの列を持つ", () => {
    const html = render(<RolePermissionsPage api={api} canManage targets={ragTargets} />);
    expect(html).toContain("権限管理");
    expect(html).toContain('data-testid="security-permissions-loading"');
    expect(html).toContain('data-testid="security-permissions-actions"');
    expect(html).toContain('data-testid="security-permissions-search"');
    expect(html).toContain("業務ビュー");
    expect(html).toContain("ナレッジベース");
  });

  it("文言は messages で上書きできる", () => {
    const html = render(
      <RolePermissionsPage api={api} canManage messages={{ subtitle: "RAG のロール権限を設定します。" }} />,
    );
    expect(html).toContain("RAG のロール権限を設定します。");
  });
});

describe("権限の継承", () => {
  const byCode = new Map(catalog.map((permission) => [permission.code, permission]));

  it("implies を推移的にたどり、継承元の権限名を記録する", () => {
    const sources = permissionInheritanceSources(["menu.search"], byCode);
    expect(sources.get("rag.read")).toEqual(["検索"]);
    expect(sources.get("rag.meta.read")).toEqual(["検索"]);
    expect([...effectivePermissionCodes(["menu.search"], byCode)].sort()).toEqual([
      "menu.search",
      "rag.meta.read",
      "rag.read",
    ]);
  });

  it("対象の表示名は補足を括弧で添える", () => {
    expect(targetItemLabel({ id: "p1", name: "財務", secondary: "会計" })).toBe("財務 (会計)");
    expect(targetItemLabel({ id: "p1", name: "財務" })).toBe("財務");
  });
});
