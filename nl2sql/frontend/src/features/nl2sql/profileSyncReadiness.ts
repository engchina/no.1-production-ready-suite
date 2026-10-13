/**
 * 業務プロファイルの保存（Oracle 反映）の事前条件（#1683）。
 *
 * DBMS_CLOUD_AI Profile は Select AI の Credential を参照するため、Credential が無いと反映できない。
 * backend の `GET /api/nl2sql/oracle-sync-readiness` が Credential の有無を返し、無いときは
 * 反映の開始（`POST .../oracle-sync-jobs`）が 409 `SELECT_AI_CREDENTIAL_MISSING` を返す。
 */
import { ApiError } from "../../lib/api";
import { APP_ROUTES } from "../../lib/routes";

import type { ProfileSyncReadinessData } from "./types";

export const PROFILE_ORACLE_SYNC_READINESS_URL = "/api/nl2sql/oracle-sync-readiness";
export const PROFILE_ORACLE_SYNC_READINESS_KEY = ["nl2sql", "oracle-sync-readiness"] as const;
export const SELECT_AI_CREDENTIAL_MISSING_CODE = "SELECT_AI_CREDENTIAL_MISSING";
/** 「保存」の aria-describedby が指す事前条件の案内の id。 */
export const PROFILE_SAVE_CREDENTIAL_PRECONDITION_ID = "profile-save-credential-precondition";

/** Credential が要り、無いと確かめられたときだけ true（確かめられない・不要なら止めない）。 */
export function isSelectAiCredentialMissing(
  readiness: ProfileSyncReadinessData | null | undefined,
): boolean {
  return Boolean(readiness?.credential_required && readiness.credential_exists === false);
}

export function isSelectAiCredentialMissingError(error: unknown): boolean {
  return error instanceof ApiError && error.errorCode === SELECT_AI_CREDENTIAL_MISSING_CODE;
}

/** 運用設定の Select AI Credential 管理（#658）。戻り先に編集中の業務プロファイルとジョブを付ける。 */
export function selectAiCredentialSettingsHref(profileId = "", jobId = ""): string {
  const returnParams = new URLSearchParams();
  if (profileId) returnParams.set("profile", profileId);
  if (jobId) returnParams.set("syncJobId", jobId);
  const query = returnParams.toString();
  const returnTo = query ? `${APP_ROUTES.profiles}?${query}` : APP_ROUTES.profiles;
  const settingsParams = new URLSearchParams({ returnTo });
  return `${APP_ROUTES.settingsSelectAiCredential}?${settingsParams.toString()}`;
}
