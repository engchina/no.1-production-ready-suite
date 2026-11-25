import { readFileSync } from "node:fs";

import { expect, test } from "./fixtures/test";

import { NAV_SECTIONS } from "../src/components/layout/nav-config";
import { t } from "../src/lib/i18n";
import { arrangeAgentPermissions } from "../src/lib/permission-targets";
import { CAPABILITY_PERMISSIONS, MENU_PERMISSIONS } from "../src/lib/permissions";
import { CAPABILITY_PERMISSION_CODES, MENU_PERMISSION_CODES, PERMISSION_CATALOG } from "./fixtures/auth";

// frontend の権限コード（lib/permissions.ts）と e2e の権限カタログ（fixtures/auth.ts）が、
// backend の正本（agent/backend/app/security/permissions.py）と一致することを確かめる（#215）。
// frontend に unit test の基盤がないため、ブラウザを使わない Playwright の test で検査する。

const BACKEND_PERMISSIONS = new URL("../../backend/app/security/permissions.py", import.meta.url);

function backendCodes(prefix: "menu." | "agent."): string[] {
  const source = readFileSync(BACKEND_PERMISSIONS, "utf-8");
  const codes = [...source.matchAll(/^[A-Z0-9_]+ = "((?:menu|agent)\.[a-z0-9_.]+)"$/gm)].map((match) => match[1]);
  return codes.filter((code) => code.startsWith(prefix));
}

test("メニュー権限のコードが backend の権限カタログと一致する", () => {
  const backend = backendCodes("menu.");
  expect(backend.length).toBeGreaterThan(0);
  expect([...Object.values(MENU_PERMISSIONS)].sort()).toEqual([...backend].sort());
  expect([...MENU_PERMISSION_CODES].sort()).toEqual([...backend].sort());
});

test("capability のコードが backend の権限カタログと一致する", () => {
  const backend = backendCodes("agent.");
  expect([...Object.values(CAPABILITY_PERMISSIONS)].sort()).toEqual([...backend].sort());
  expect([...CAPABILITY_PERMISSION_CODES].sort()).toEqual([...backend].sort());
  expect(PERMISSION_CATALOG.map((item) => item.code).sort()).toEqual(
    [...backendCodes("menu."), ...backend].sort()
  );
});

// 権限管理の機能の一覧は、左のナビを正本にしてグループ・並び順・名前をそろえる（Issue 567）。
// backend のカタログの code を、ナビと違うグループ・名前・逆順で渡しても、一覧はサイドナビと同じになる。
test("権限管理の機能の一覧は、左のナビと同じグループ・並び順・名前になる", () => {
  const menu = backendCodes("menu.");
  const capabilities = backendCodes("agent.");
  const scrambled = [...menu, ...capabilities]
    .map((code) => ({
      code,
      group: code.startsWith("menu.") ? "backend のグループ" : "backend の capability",
      label: `backend の名前 ${code}`,
      description: `${code} の説明`,
      implies: [] as string[],
    }))
    .reverse();
  const arranged = arrangeAgentPermissions(scrambled);

  // メニュー権限は、ナビに出さない承認（#5834）を除いてすべてナビの項目で、ナビと同じ見出し・並び・名前
  // （sidebarLabelKey を優先）になる。
  const navPermissions = NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.permission));
  expect(navPermissions).not.toContain(MENU_PERMISSIONS.approvals);
  expect([...navPermissions, MENU_PERMISSIONS.approvals].sort()).toEqual([...menu].sort());
  const groups = new Map<string, string[]>();
  for (const permission of arranged.slice(0, navPermissions.length)) {
    groups.set(permission.group, [...(groups.get(permission.group) ?? []), permission.label]);
  }
  expect([...groups.entries()]).toEqual(
    NAV_SECTIONS.map((section) => [
      t(section.titleKey),
      section.items.map((item) => t(item.sidebarLabelKey ?? item.labelKey)),
    ])
  );

  // ナビに無い権限は承認の画面と capability だけで、ナビの後ろに backend のグループ・名前のまま置く。
  // カタログの権限を落とさない。
  expect(arranged.slice(navPermissions.length).map((item) => item.code).sort()).toEqual(
    [MENU_PERMISSIONS.approvals, ...capabilities].sort()
  );
  expect(arranged.find((item) => item.code === MENU_PERMISSIONS.approvals)?.group).toBe("backend のグループ");
  // 承認の backend のグループはナビの「AI 活用」と同じ名前なので、権限管理の画面では同じグループに出る。
  const source = readFileSync(BACKEND_PERMISSIONS, "utf-8");
  expect(source).toContain(`_GROUP_USE = "${t("nav.section.use")}"`);
  expect(source).toContain('_menu_permission(MENU_APPROVALS, _GROUP_USE, "承認")');
  expect(arranged.map((item) => item.description)).toEqual(arranged.map((item) => `${item.code} の説明`));
});


test("ユーザーとロールの末尾に共通の API キー管理を置く", () => {
  const section = NAV_SECTIONS.find((section) => section.titleKey === "nav.section.userRoles");
  expect(section?.items.map((item) => t(item.labelKey))).toEqual(["ユーザー管理", "ロール管理", "API キー"]);
});
