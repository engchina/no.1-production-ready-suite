import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { extname, relative } from "node:path";
import test from "node:test";

const sourceRoot = new URL("../src/", import.meta.url);

function tsxFiles(directoryUrl: URL): URL[] {
  return readdirSync(directoryUrl, { withFileTypes: true }).flatMap((entry) => {
    const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directoryUrl);
    if (entry.isDirectory()) return tsxFiles(child);
    return extname(entry.name) === ".tsx" ? [child] : [];
  });
}

const sources = tsxFiles(sourceRoot).map((url) => ({
  path: relative(new URL("..", sourceRoot).pathname, url.pathname),
  source: readFileSync(url, "utf8"),
}));

test("business pages do not create raw alert live regions", () => {
  const violations = sources.flatMap(({ path, source }) =>
    /role\s*=\s*(?:"alert"|\{[^}]*["']alert["'][^}]*\})/gu.test(source) ? [path] : []
  );
  assert.deepEqual(
    violations,
    [],
    "FieldError/FormStatus/Banner/ErrorState 等の標準チャネルを使用してください。"
  );
});

test("handwritten danger surfaces stay limited to structured state and field controls", () => {
  const allowedOccurrenceCounts = new Map<string, number>([
    ["src/components/ExecutionActivityPanel.tsx", 1],
    ["src/components/settings/DatabaseSettingsClient.tsx", 1],
    ["src/components/ui/file-dropzone.tsx", 1],
    ["src/components/ui/status-badge.tsx", 1],
    ["src/features/nl2sql/components/DbAdminShared.tsx", 2],
    ["src/features/nl2sql/components/WorkflowProgressStrip.tsx", 1],
    ["src/features/nl2sql/pages/DataManagementPage.tsx", 1],
    ["src/features/nl2sql/pages/EvaluationPage.tsx", 3],
    ["src/features/security/SecurityDeepSecPage.tsx", 2],
  ]);
  const dangerSurface = /(?:border-danger[^"'\n]*bg-danger|bg-danger[^"'\n]*border-danger)/gu;
  const actual = new Map<string, number>();
  for (const { path, source } of sources) {
    const count = [...source.matchAll(dangerSurface)].length;
    if (count > 0) actual.set(path, count);
  }
  assert.deepEqual(
    [...actual.entries()].sort(),
    [...allowedOccurrenceCounts.entries()].sort(),
    "新しい通知面は semantic token の手書きではなく標準メッセージコンポーネントを使用してください。"
  );
});

test("security create forms bind server field errors without parsing Japanese strings", () => {
  const users = readFileSync(new URL("../src/features/security/SecurityUsersPage.tsx", import.meta.url), "utf8");
  const roles = readFileSync(new URL("../src/features/security/SecurityRolesPage.tsx", import.meta.url), "utf8");

  assert.match(users, /"\/login_user_id": "loginUserId"/u);
  assert.match(users, /aria-describedby=\{fieldErrors\.loginUserId/u);
  assert.match(users, /<FieldError id="security-user-login-user-id-error"/u);
  assert.match(roles, /"\/role_code": "roleCode"/u);
  assert.match(roles, /<FieldError id="security-role-code-error"/u);
  assert.doesNotMatch(users, /このログインユーザーIDは既に使用されています/u);
  assert.doesNotMatch(roles, /このロールコードは既に使用されています/u);
});

test("danger toast fallback is durable and goes through one wrapper", () => {
  const toastSource = readFileSync(new URL("../src/lib/toast.ts", import.meta.url), "utf8");
  const directDangerCalls = sources.flatMap(({ path, source }) =>
    /toast\.error\(/u.test(source) ? [path] : []
  );

  assert.deepEqual(directDangerCalls, []);
  assert.match(toastSource, /duration: options\?\.duration \?\? 0/u);
});
