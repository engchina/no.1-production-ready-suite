import assert from "node:assert/strict";
import test from "node:test";

import {
  presentAgentAssetRebuild,
  profileAgentAssetRebuildKey,
  profileAgentAssetRebuildUrl,
  type ProfileAgentAssetRebuildData,
} from "../src/features/nl2sql/agentAssetRebuild.ts";
import { formatDateTime } from "../src/lib/format.ts";

// 業務プロファイルの Select AI Agent アセットの最後の再構築の日時と結果（#1691）。

const base: ProfileAgentAssetRebuildData = {
  profile_id: "sales",
  status: "never",
  rebuilt_at: "",
  job_id: "",
  profile_etag: "",
  profile_changed_since: false,
  team_name: "",
  error_code: "",
  error_message_ja: "",
};

test("まだ再構築していなければ、未再構築（neutral）と作り方の案内を出す", () => {
  const view = presentAgentAssetRebuild(base);
  assert.equal(view.variant, "neutral");
  assert.equal(view.badge, "未再構築");
  assert.match(view.summary, /^まだ再構築していません。/);
  assert.equal(view.failure, "");
  assert.equal(view.changedSince, "");
});

test("成功は再構築済み（success）と、既存の画面と同じ書式の日時を出す", () => {
  const rebuiltAt = "2026-10-13T09:05:00+00:00";
  const view = presentAgentAssetRebuild({
    ...base,
    status: "succeeded",
    rebuilt_at: rebuiltAt,
    job_id: "profile_sync_1",
  });
  assert.equal(view.variant, "success");
  assert.equal(view.badge, "再構築済み");
  assert.equal(view.summary, `日時: ${formatDateTime(rebuiltAt)}`);
  assert.equal(view.failure, "");
});

test("失敗は再構築に失敗（danger）と、backend の利用者向けの要約を出す", () => {
  const view = presentAgentAssetRebuild({
    ...base,
    status: "failed",
    rebuilt_at: "2026-10-13T09:05:00+00:00",
    error_code: "AGENT_ASSETS_REBUILD_FAILED",
    error_message_ja: "Select AI Agent アセットを再構築できませんでした。再試行してください。",
  });
  assert.equal(view.variant, "danger");
  assert.equal(view.badge, "再構築に失敗");
  assert.equal(view.failure, "Select AI Agent アセットを再構築できませんでした。再試行してください。");
  // 要約が空でも、失敗だけは文で分かるようにする（色だけで示さない）。
  assert.equal(
    presentAgentAssetRebuild({ ...base, status: "failed", rebuilt_at: "2026-10-13T09:05:00Z" }).failure,
    "Select AI Agent アセットを再構築できませんでした。",
  );
});

test("最後の再構築の後に保存していれば、反映の仕方を案内する", () => {
  const view = presentAgentAssetRebuild({
    ...base,
    status: "succeeded",
    rebuilt_at: "2026-10-13T09:05:00Z",
    profile_changed_since: true,
  });
  assert.match(view.changedSince, /^最後の再構築の後に業務プロファイルが保存されています。/);
});

test("想定外の状態は未再構築として扱い、画面を落とさない", () => {
  const view = presentAgentAssetRebuild({
    ...base,
    status: "unknown" as ProfileAgentAssetRebuildData["status"],
  });
  assert.equal(view.variant, "neutral");
});

test("取得の URL とキャッシュのキーは業務プロファイルごと", () => {
  assert.equal(
    profileAgentAssetRebuildUrl("sales/profile"),
    "/api/nl2sql/profiles/sales%2Fprofile/agent-asset-rebuild",
  );
  assert.deepEqual(profileAgentAssetRebuildKey("sales"), [
    "nl2sql",
    "profile-agent-asset-rebuild",
    "sales",
  ]);
});
