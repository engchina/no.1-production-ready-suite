import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

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
