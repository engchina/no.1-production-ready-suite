import assert from "node:assert/strict";
import test from "node:test";

import {
  isSelectAiCredentialMissing,
  isSelectAiCredentialMissingError,
  selectAiCredentialSettingsHref,
} from "../src/features/nl2sql/profileSyncReadiness.ts";
import { ApiError } from "../src/lib/api.ts";

// 業務プロファイルの保存（Oracle 反映）の事前条件（#1683）。

test("Credential が要り、無いと確かめられたときだけ事前条件が足りない", () => {
  assert.equal(
    isSelectAiCredentialMissing({
      credential_required: true,
      credential_name: "OCI_CRED",
      credential_exists: false,
    }),
    true,
  );
  assert.equal(
    isSelectAiCredentialMissing({
      credential_required: true,
      credential_name: "OCI_CRED",
      credential_exists: true,
    }),
    false,
  );
  // 確かめられない（DB に接続できない）・不要（deterministic runtime）・未取得のときは止めない。
  assert.equal(
    isSelectAiCredentialMissing({
      credential_required: true,
      credential_name: "OCI_CRED",
      credential_exists: null,
    }),
    false,
  );
  assert.equal(
    isSelectAiCredentialMissing({
      credential_required: false,
      credential_name: "",
      credential_exists: null,
    }),
    false,
  );
  assert.equal(isSelectAiCredentialMissing(undefined), false);
});

test("反映の開始の 409 SELECT_AI_CREDENTIAL_MISSING を見分ける", () => {
  assert.equal(
    isSelectAiCredentialMissingError(
      new ApiError(409, ["Select AI の Credential を先に作成してください"], "SELECT_AI_CREDENTIAL_MISSING"),
    ),
    true,
  );
  assert.equal(isSelectAiCredentialMissingError(new ApiError(409, ["競合"], "CONFLICT")), false);
  assert.equal(isSelectAiCredentialMissingError(new Error("SELECT_AI_CREDENTIAL_MISSING")), false);
});

test("Credential の設定の画面へのリンクは戻り先に業務プロファイルとジョブを付ける", () => {
  const href = selectAiCredentialSettingsHref("profile-1", "job-1");
  const url = new URL(href, "http://localhost");
  assert.equal(url.pathname, "/settings/select-ai-credential");
  assert.equal(url.searchParams.get("returnTo"), "/profiles?profile=profile-1&syncJobId=job-1");
  const bare = new URL(selectAiCredentialSettingsHref(), "http://localhost");
  assert.equal(bare.searchParams.get("returnTo"), "/profiles");
});
