/**
 * 3 製品共通の「システム設定 › データベース」の Oracle AI Data Platform の区画（#5931）を、製品の e2e が同じ
 * 合成の応答と同じ期待で確かめるための値。platform に Playwright の依存を持たせないため、値だけを置く。
 */

/** `GET /api/settings/database/aidp` の合成の応答（設定あり・DB ユーザーが在る）。 */
export const AIDP_INTEGRATION = {
  configured: true,
  url: "https://aidp.example.invalid/?ocid=ocid1.aidataplatform.oc1.ap-osaka-1.example",
  ocid: "ocid1.aidataplatform.oc1.ap-osaka-1.example",
  region: "ap-osaka-1",
  adb_db_name: "appdb",
  adb_service_name: "appdb_high",
  db_user: "ADB_AIDP_USER",
  db_user_status: "exists",
} as const;

/** 区画の読み取り専用の欄（id と値）。DB 名は ADB の情報が取れていればその値になるので、ここでは確かめない。 */
export const AIDP_READONLY_FIELDS = [
  ["aidp-url", AIDP_INTEGRATION.url],
  ["aidp-ocid", AIDP_INTEGRATION.ocid],
  ["aidp-region", AIDP_INTEGRATION.region],
  ["aidp-catalog-service-name", AIDP_INTEGRATION.adb_service_name],
  ["aidp-catalog-db-user", AIDP_INTEGRATION.db_user],
] as const;

export const AIDP_CARD_TEST_ID = "settings-aidp-card";
export const AIDP_OPEN_LINK_TEST_ID = "settings-aidp-open-link";
export const AIDP_DB_USER_EXISTS_BADGE = "DB ユーザー: 作成済み";
export const AIDP_DB_USER_UNKNOWN_BADGE = "DB ユーザー: 確認できません";
export const AIDP_CATALOG_GUIDE =
  "External Catalog の登録は Oracle AI Data Platform の画面で行います";
