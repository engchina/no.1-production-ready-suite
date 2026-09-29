import { ConfirmProvider } from "@engchina/production-ready-ui";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  RolePermissionsPage,
  effectivePermissionCodes,
  normalizeCustomTargetId,
  permissionInheritanceSources,
  targetItemLabel,
  targetItemsWithCustomIds,
  targetLoadRows,
  type PermissionDefinition,
  type PermissionRole,
  type RolePermissionTargetSection,
  type RolePermissionsApi,
} from "../src";
import { RolePermissionTargetOption } from "../src/permissions/RolePermissionsPage";

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

describe("利用できる対象の候補の行（#521）", () => {
  const id = "0123456789abcdef0123456789abcdef";
  const option = (item: Parameters<typeof RolePermissionTargetOption>[0]["item"], reserve?: boolean) =>
    renderToStaticMarkup(
      <RolePermissionTargetOption
        item={item}
        checked={false}
        disabled={false}
        reserveDescription={reserve}
        testId="security-roles-business-views-option"
        onToggle={() => undefined}
      />,
    );

  it("名前と説明を出し、内部の ID は出さない。省略する名前・説明は title に全文を持つ", () => {
    const html = option({ id, name: "人事 FAQ", description: "人事規程の問い合わせ", status: "アーカイブ済み" });
    expect(html).toContain("人事 FAQ");
    expect(html).toContain("人事規程の問い合わせ");
    expect(html).toContain("アーカイブ済み");
    expect(html).not.toContain(id);
    expect(html).toContain('title="人事 FAQ"');
    expect(html).toContain('title="人事規程の問い合わせ"');
    // 名前は 1 行、説明は 2 行で省略し、説明の 2 行分の高さを取る。
    expect(html).toContain("truncate");
    expect(html).toContain("line-clamp-2");
    expect(html).toContain("min-h-[2lh]");
  });

  it("説明が無い候補も、既定では説明の 2 行分を取って行の高さをそろえる", () => {
    const html = option({ id, name: "旧経理", description: "  " });
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("min-h-[2lh]");
    expect(html).not.toContain(id);
  });

  it("説明を持つ候補が無い対象（reserveDescription=false）は名前だけの行にする", () => {
    const html = option({ id, name: id }, false);
    expect(html).not.toContain("min-h-[2lh]");
    // 名前が ID の候補（直接入力）は、名前として ID を出す。
    expect(html).toContain(id);
  });
});

describe("候補にない ID の直接入力（allowCustomIds。#215）", () => {
  it("前後の空白を除き、空・形式に合わない ID は受け付けない", () => {
    const pattern = /^[A-Za-z0-9._:-]{1,64}$/;
    expect(normalizeCustomTargetId("  sales-east  ", pattern)).toBe("sales-east");
    expect(normalizeCustomTargetId("   ", pattern)).toBeNull();
    expect(normalizeCustomTargetId("営業 東日本", pattern)).toBeNull();
    expect(normalizeCustomTargetId("x".repeat(65), pattern)).toBeNull();
    // 形式の指定が無ければ空でない文字列を受け付ける。
    expect(normalizeCustomTargetId(" 営業 ")).toBe("営業");
  });

  it("g フラグ付きの形式でも毎回先頭から判定する", () => {
    const pattern = /^[a-z-]+$/g;
    expect(normalizeCustomTargetId("sales", pattern)).toBe("sales");
    expect(normalizeCustomTargetId("sales", pattern)).toBe("sales");
  });

  it("選択済みで候補にない ID を名前 = ID の候補として末尾に足し、重複させない", () => {
    const items = [{ id: "sales", name: "sales" }];
    expect(targetItemsWithCustomIds(items, ["sales", "hr", "hr", "finance"], "直接入力")).toEqual([
      { id: "sales", name: "sales" },
      { id: "hr", name: "hr", status: "直接入力" },
      { id: "finance", name: "finance", status: "直接入力" },
    ]);
    // 状態の文言が無ければ status を付けない。元の配列は変えない。
    expect(targetItemsWithCustomIds(items, ["hr"])).toEqual([
      { id: "sales", name: "sales" },
      { id: "hr", name: "hr" },
    ]);
    expect(items).toEqual([{ id: "sales", name: "sales" }]);
  });

  it("直接入力を許可する対象を渡しても、NL2SQL / RAG と同じ一覧の構成で描画できる", () => {
    const agentTargets: RolePermissionTargetSection<RagRole>[] = [
      {
        ...ragTargets[0],
        key: "business-view-access",
        allowCustomIds: {
          label: "業務ビュー ID を直接入力",
          addLabel: "追加",
          pattern: /^[A-Za-z0-9._:-]{1,64}$/,
          invalidMessage: "業務ビュー ID の形式が正しくありません。",
          customStatus: "直接入力",
        },
      },
    ];
    const html = render(<RolePermissionsPage api={api} canManage targets={agentTargets} />);
    expect(html).toContain('data-testid="security-permissions-grid"');
    expect(html).toContain("業務ビュー");
  });
});

describe("候補の一部だけ読めた場合（#240）", () => {
  it("配列は警告なし、{ items, warning } は候補を残して警告を出す", () => {
    const items = [{ id: "sales", name: "営業" }];
    expect(targetLoadRows(items)).toEqual({ rows: items, warning: "" });
    expect(targetLoadRows({ items, warning: "  RAG の業務ビューを読めませんでした。  " })).toEqual({
      rows: items,
      warning: "RAG の業務ビューを読めませんでした。",
    });
    expect(targetLoadRows({ items: [] })).toEqual({ rows: [], warning: "" });
  });
});
