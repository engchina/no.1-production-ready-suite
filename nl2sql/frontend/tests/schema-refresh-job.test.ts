import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  schemaRefreshHeaderPresentation,
  schemaRefreshJobIsActive,
  schemaRefreshProcessingLabel,
} from "../src/features/nl2sql/schemaRefreshPresentation";
import type { SchemaRefreshJob } from "../src/features/nl2sql/types";

function job(overrides: Partial<SchemaRefreshJob> = {}): SchemaRefreshJob {
  return {
    job_id: "schema-refresh-1",
    status: "running",
    mode: "full",
    created_at: "2026-08-29T00:00:00Z",
    started_at: "2026-08-29T00:00:01Z",
    phase: "persisting",
    processed_objects: 218,
    total_objects: 218,
    scanned_objects: 218,
    changed_objects: 12,
    deleted_objects: 0,
    catalog_version: 4,
    error_code: "",
    ...overrides,
  };
}

function source(path: string) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("schema refresh formatter centralizes phase, mode, and known progress", () => {
  assert.deepEqual(schemaRefreshHeaderPresentation(job()), {
    label: "DB 構造再取得: 保存中 218/218",
    announcementLabel: "DB 構造再取得: 保存中",
    variant: "info",
  });
  assert.deepEqual(
    schemaRefreshHeaderPresentation(
      job({ mode: "targeted", phase: "fetching", processed_objects: 3, total_objects: 10 }),
    ),
    {
      label: "DB 構造差分同期: 構造取得中 3/10",
      announcementLabel: "DB 構造差分同期: 構造取得中",
      variant: "info",
    },
  );
  assert.equal(schemaRefreshProcessingLabel(job()), "DB 構造を再取得しています");
  assert.equal(
    schemaRefreshProcessingLabel(job({ mode: "targeted" })),
    "DB 構造の差分を同期しています",
  );
});

test("schema refresh formatter never invents unknown totals and clears success history", () => {
  const unknown = schemaRefreshHeaderPresentation(
    job({ phase: "scanning", processed_objects: 7, total_objects: 0 }),
  );
  assert.equal(unknown?.label, "DB 構造再取得: 変更確認中");
  assert.doesNotMatch(unknown?.label ?? "", /7\/0/u);
  assert.equal(schemaRefreshHeaderPresentation(job({ status: "done", phase: "done" })), null);
  assert.deepEqual(schemaRefreshHeaderPresentation(null, { starting: true }), {
    label: "DB 構造再取得: 開始中",
    announcementLabel: "DB 構造再取得: 開始中",
    variant: "info",
  });
  assert.equal(schemaRefreshJobIsActive(job({ status: "pending" })), true);
  assert.equal(schemaRefreshJobIsActive(job({ status: "error" })), false);
});

test("active discovery and durable job hooks use focus recovery and one-second active polling", () => {
  const hooks = source("../src/features/nl2sql/incrementalQueries.ts");
  assert.match(hooks, /\/api\/schema\/refresh-jobs\/active/u);
  assert.match(hooks, /refetchOnMount: "always"/u);
  assert.match(hooks, /refetchOnReconnect: "always"/u);
  assert.match(hooks, /refetchOnWindowFocus: "always"/u);
  assert.match(
    hooks,
    /return status === "done" \|\| status === "error" \? false : 1_000/u,
  );
});

test("coordinator owns start/track, discovery, terminal notification, and invalidation once", () => {
  const coordinator = source("../src/features/nl2sql/SchemaRefreshCoordinator.tsx");
  assert.match(coordinator, /useActiveSchemaRefreshJob\(discoveryEnabled\)/u);
  assert.match(coordinator, /const track = useCallback/u);
  assert.match(coordinator, /const start = useCallback/u);
  assert.match(coordinator, /reportedTerminal\.current === reportKey/u);
  assert.match(coordinator, /invalidateQueries\(\{ queryKey: \["schema"\] \}\)/u);
  assert.match(coordinator, /invalidateQueries\(\{ queryKey: \["nl2sql", "db-admin"\] \}\)/u);
  assert.match(coordinator, /activeSchemaRefreshJob[\s\S]{0,120}active_job: null/u);
  assert.match(coordinator, /toast\.success\(t\("common\.action\.schemaRefreshed"\)\)/u);
  // 失敗は各ページの固定面(context の error)を正本とし、Toast と二重表示しない
  // (messaging spec §0.6)。coordinator は toastError を呼ばない。
  assert.match(coordinator, /setError\(message\)/u);
  assert.match(coordinator, /setError\(schemaRefreshJobErrorMessage\(job\)\)/u);
  assert.doesNotMatch(coordinator, /toastError/u);
  assert.match(coordinator, /Boolean\(trackedJobId\) && !job/u);
  assert.doesNotMatch(coordinator, /:query-error/u);
});

test("all ten DB refresh routes use the shared header and workspace feedback", () => {
  const routeSources = [
    source("../src/features/nl2sql/Nl2SqlWorkbench.tsx"),
    source("../src/features/nl2sql/pages/TableManagementPage.tsx"),
    source("../src/features/nl2sql/pages/ViewManagementPage.tsx"),
    source("../src/features/nl2sql/pages/DataManagementPage.tsx"),
    source("../src/features/nl2sql/pages/SampleDataPage.tsx"),
    source("../src/features/nl2sql/pages/AdminSqlPage.tsx"),
    source("../src/features/nl2sql/pages/ProfileManagementPage.tsx"),
    source("../src/features/nl2sql/pages/OntologyBuildPage.tsx"),
  ];
  const metadata = source("../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx");

  for (const routeSource of routeSources) {
    assert.match(routeSource, /SchemaRefreshHeaderStatus/u);
    assert.match(routeSource, /useSchemaRefreshCoordinator/u);
  }
  for (const routeSource of routeSources.slice(1)) {
    assert.match(routeSource, /SchemaRefreshProcessing/u);
  }
  assert.match(metadata, /export function CommentManagementPage/u);
  assert.match(metadata, /export function AnnotationManagementPage/u);
  assert.match(metadata, /SchemaRefreshHeaderStatus/u);
  assert.match(metadata, /SchemaRefreshProcessing/u);
});

test("shared schema feedback limits live announcements to the phase badge", () => {
  const feedback = source("../src/features/nl2sql/components/SchemaRefreshFeedback.tsx");
  const header = source("../src/components/PageHeader.tsx");
  const processing = source("../src/components/ProcessingState.tsx");

  assert.match(header, /role="status"[\s\S]{0,80}aria-live="polite"/u);
  assert.match(header, /<span aria-hidden="true">[\s\S]{0,120}className="sr-only"/u);
  assert.match(feedback, /announceActivity=\{false\}/u);
  assert.match(feedback, /announceSlow=\{false\}/u);
  assert.match(feedback, /activityIcon="none"/u);
  assert.match(processing, /role="timer"/u);
  assert.match(processing, /aria-live="off"/u);
});

test("DDL mutation pages stay non-blocking and hand auto-refresh jobs to the coordinator", () => {
  const ddlSources = [
    source("../src/features/nl2sql/pages/TableManagementPage.tsx"),
    source("../src/features/nl2sql/pages/ViewManagementPage.tsx"),
    source("../src/features/nl2sql/pages/MetadataSqlManagementPage.tsx"),
  ];

  for (const page of ddlSources) {
    assert.doesNotMatch(page, /reloadAfterMutation\s*=\s*async/u);
    assert.doesNotMatch(page, /waitForSchemaRefreshJob/u);
    assert.doesNotMatch(page, /:query-error/u);
    assert.match(page, /sharedSchemaRefresh\.track\(/u);
    assert.match(page, /setSchemaRefreshJobId\(/u);
    assert.match(page, /void refreshObjects\(\)/u);
  }
});

test("view create renders the shared schema progress in its task panel", () => {
  const view = source("../src/features/nl2sql/pages/ViewManagementPage.tsx");
  assert.match(view, /footerProcessing=/u);
  assert.match(view, /SchemaRefreshProcessing testId="view-create-schema-refresh-processing"/u);
});
