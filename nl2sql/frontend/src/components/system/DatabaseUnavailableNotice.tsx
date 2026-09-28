import {
  DATABASE_GATE_MESSAGES,
  DatabaseUnavailableNotice as SharedDatabaseUnavailableNotice,
  type DatabaseGateMessages,
  type DatabaseGateRoutes,
  type DatabaseUnavailableNoticeProps,
} from "@engchina/production-ready-system-settings";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

/** DB ゲートの導線（ADB の起動はデータベース設定の ADB 管理、初期化はシステムテーブル）。 */
export const DATABASE_GATE_ROUTES: DatabaseGateRoutes = {
  databaseSettings: `${APP_ROUTES.settingsDatabase}#adb-management`,
  systemTables: APP_ROUTES.settingsSystemTables,
};

/** 共通の DB ゲートの文言を、NL2SQL の辞書にある値で上書きする（製品名の入る文言など）。 */
export function databaseGateMessages(): Partial<DatabaseGateMessages> {
  return Object.fromEntries(
    Object.keys(DATABASE_GATE_MESSAGES).flatMap((key) => {
      const value = t(key);
      return value === key ? [] : [[key, value]];
    })
  );
}

/** NL2SQL の導線と文言を束ねた、共通の DB の案内（全画面 / banner。#325）。 */
export function DatabaseUnavailableNotice(
  props: Omit<DatabaseUnavailableNoticeProps, "routes" | "messages">
) {
  return (
    <SharedDatabaseUnavailableNotice
      routes={DATABASE_GATE_ROUTES}
      messages={databaseGateMessages()}
      {...props}
    />
  );
}
