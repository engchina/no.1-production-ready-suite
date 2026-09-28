import type { ReactNode } from "react";
import {
  DATABASE_GATE_MESSAGES,
  DatabaseGate as SharedDatabaseGate,
  type DatabaseGateMessages,
  type DatabaseGateRoutes,
} from "@engchina/production-ready-system-settings";

import { api } from "@/lib/api";
import { isI18nKey, t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

/**
 * DB ゲートの導線。ADB の起動と接続情報の確認はデータベース設定の ADB 管理。
 * Agent はシステムテーブルの確認（`schema_probe`）をまだ持たないため、`systemTables` は渡さない
 * （状態 API は `setup_required` を返さない）。
 */
export const DATABASE_GATE_ROUTES: DatabaseGateRoutes = {
  databaseSettings: `${APP_ROUTES.settingsDatabase}#adb-management`,
};

/** 共通の DB ゲートの文言を、Agent の辞書にある値で上書きする（製品名の入る文言など）。 */
export function databaseGateMessages(): Partial<DatabaseGateMessages> {
  return Object.fromEntries(
    Object.keys(DATABASE_GATE_MESSAGES)
      .filter(isI18nKey)
      .map((key) => [key, t(key)])
  );
}

/**
 * 業務画面を開く前にデータベースの利用可否を確認する（3製品共通の部品。#325）。
 * ゲートを通さない画面は共通の既定（システム設定の 5 画面）。ローカル認証（`AGENT_AUTH_MODE=local`）は
 * DB を使わないため、backend の状態 API が常に `ok` を返し、ゲートは出ない。
 */
export function DatabaseGate({ children }: { children: ReactNode }) {
  return (
    <SharedDatabaseGate api={api} routes={DATABASE_GATE_ROUTES} messages={databaseGateMessages()}>
      {children}
    </SharedDatabaseGate>
  );
}
