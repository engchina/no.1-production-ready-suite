import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { isWidePage, WIDE_PAGE_ROUTES } from "../src/lib/page-layout.ts";
import { APP_ROUTES } from "../src/lib/routes.ts";

// 作業画面（SQL エディタ + 結果、一覧 + 詳細、グラフ）は画面幅いっぱい、読む・入力する画面は 1440px。
// PageHeader と PageBody の wide がずれると、広い画面でタイトルと本文の左端がずれる。
const pageFiles: Record<string, string> = {
  [APP_ROUTES.query]: "features/nl2sql/Nl2SqlWorkbench.tsx",
  [APP_ROUTES.directSql]: "features/nl2sql/pages/DirectSqlPage.tsx",
  [APP_ROUTES.sqlToQuestion]: "features/nl2sql/pages/SqlToQuestionPage.tsx",
  [APP_ROUTES.history]: "features/nl2sql/pages/HistoryPage.tsx",
  [APP_ROUTES.adminSql]: "features/nl2sql/pages/AdminSqlPage.tsx",
  [APP_ROUTES.tableManagement]: "features/nl2sql/pages/TableManagementPage.tsx",
  [APP_ROUTES.viewManagement]: "features/nl2sql/pages/ViewManagementPage.tsx",
  [APP_ROUTES.dataManagement]: "features/nl2sql/pages/DataManagementPage.tsx",
  [APP_ROUTES.commentManagement]: "features/nl2sql/pages/MetadataSqlManagementPage.tsx",
  [APP_ROUTES.annotationManagement]: "features/nl2sql/pages/MetadataSqlManagementPage.tsx",
  [APP_ROUTES.sampleData]: "features/nl2sql/pages/SampleDataPage.tsx",
  [APP_ROUTES.ontologyBuild]: "features/nl2sql/pages/OntologyBuildPage.tsx",
  [APP_ROUTES.feedbackManagement]: "features/nl2sql/pages/FeedbackManagementPage.tsx",
  [APP_ROUTES.profiles]: "features/nl2sql/pages/ProfileManagementPage.tsx",
  [APP_ROUTES.glossaryRules]: "features/nl2sql/pages/GlossaryRulesPage.tsx",
  [APP_ROUTES.globalRules]: "features/nl2sql/pages/GlobalRulesPage.tsx",
  [APP_ROUTES.evaluation]: "features/nl2sql/pages/EvaluationPage.tsx",
  [APP_ROUTES.questionClassifierModels]: "features/nl2sql/pages/QuestionLearningPage.tsx",
  [APP_ROUTES.securityUsers]: "features/security/SecurityUsersPage.tsx",
  [APP_ROUTES.securityRoles]: "features/security/SecurityRolesPage.tsx",
  [APP_ROUTES.securityDeepSec]: "features/security/SecurityDeepSecPage.tsx",
  [APP_ROUTES.settingsModel]: "components/settings/ModelSettingsClient.tsx",
  [APP_ROUTES.settingsAppearance]: "components/settings/AppearanceSettings.tsx",
};

const layoutTags = (path: string) =>
  [...readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8").matchAll(/<(PageHeader|PageBody)\b[^>]*>/gu)].map(
    (match) => match[0],
  );

test("作業画面だけが PageHeader / PageBody の両方に wide を渡し、それ以外は 1440px のまま", () => {
  for (const [route, path] of Object.entries(pageFiles)) {
    const tags = layoutTags(path);
    assert.ok(tags.length >= 2, `${path} に PageHeader と PageBody がある`);
    for (const tag of tags) {
      assert.equal(/\swide\b/u.test(tag), isWidePage(route), `${route}（${path}）: ${tag}`);
    }
  }
  for (const route of WIDE_PAGE_ROUTES) assert.ok(pageFiles[route], `${route} の画面ファイルを検査対象に含める`);
});

test("メモリモードの警告バナーは表示中の画面と同じ幅にそろえる", () => {
  const gate = readFileSync(new URL("../src/components/system/DatabaseGate.tsx", import.meta.url), "utf8");
  assert.match(gate, /<PageBody wide=\{isWidePage\(location\.pathname\)\}/u);
});

test("AI要件確認の未入力案内は操作前に赤いエラー（role=alert）で出さない", () => {
  const workbench = readFileSync(new URL("../src/features/nl2sql/Nl2SqlWorkbench.tsx", import.meta.url), "utf8");
  assert.match(workbench, /<p id="nl2sql-guided-query-required" className="text-xs leading-5 text-fg-muted">/u);
  assert.doesNotMatch(workbench, /<FieldError[\s\S]{0,80}nl2sql-guided-query-required/u);
  assert.match(workbench, /aria-describedby=\{!question\.trim\(\) \? "nl2sql-guided-query-required" : undefined\}/u);
});
