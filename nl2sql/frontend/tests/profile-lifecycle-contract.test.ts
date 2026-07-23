import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const profilePage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);
const ontologyPage = readFileSync(
  new URL("../src/features/nl2sql/pages/OntologyBuildPage.tsx", import.meta.url),
  "utf8",
);
const messages = readFileSync(new URL("../src/lib/i18n.ts", import.meta.url), "utf8");

test("profile save queues Oracle sync without touching Ontology or detail refresh", () => {
  assert.match(profilePage, /\/oracle-sync-jobs/u);
  assert.match(profilePage, /"Idempotency-Key"/u);
  assert.match(profilePage, /BUSINESS_SELECT_AI_DB_PROFILES_URL/u);
  assert.doesNotMatch(profilePage, /BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL/u);
  assert.doesNotMatch(profilePage, /\/ontology-view/u);
  assert.doesNotMatch(profilePage, /\/select-ai-profile/u);
  assert.doesNotMatch(profilePage, /select-ai-agent\/assets\/refresh/u);
});

test("profile Oracle sync exposes progress, failure recovery and retry", () => {
  assert.match(profilePage, /refetchInterval/u);
  assert.match(profilePage, /aria-live="polite"/u);
  assert.match(profilePage, /\/oracle-sync-jobs\/\$\{oracleSyncJob\.job_id\}\/retry/u);
  assert.match(messages, /業務 Profile は保存されましたが、Oracle 反映に失敗しました/u);
});

test("Ontology view materialization belongs to the Ontology page", () => {
  assert.match(ontologyPage, /\/ontology-view\/materialize/u);
  assert.match(ontologyPage, /ontologyViewStale/u);
  assert.match(ontologyPage, /data-testid="ontology-view-lifecycle"/u);
  assert.match(messages, /業務 Profile の保存では更新されません/u);
});

test("delete confirmation names Ontology cleanup and retained Oracle audit state", () => {
  assert.match(messages, /そのすべての Ontology view を完全に削除/u);
  assert.match(messages, /Oracle DBMS_CLOUD_AI Profile と監査履歴は削除されません/u);
  assert.match(profilePage, /profile\.id !== "default"/u);
});
