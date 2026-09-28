import type { ReactNode } from "react";
import {
  DATABASE_GATE_MESSAGES,
  DatabaseGate as SharedDatabaseGate,
  isDatabaseGateExemptPath,
  type DatabaseGateMessages,
  type DatabaseGateRoutes,
} from "@engchina/production-ready-system-settings";

import { api } from "@/lib/api";
import { ja, t, type I18nKey } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

/**
 * DB ゲートの導線。ADB の起動はデータベース設定の ADB 管理、システムテーブルの作成・更新は
 * データベース設定の中のシステムテーブル（RAG は独立した画面を持たない）。
 */
export const DATABASE_GATE_ROUTES: DatabaseGateRoutes = {
  databaseSettings: `${APP_ROUTES.settingsDatabase}#adb-management`,
  systemTables: `${APP_ROUTES.settingsDatabase}#system-tables`,
};

/**
 * ゲートを通さない画面（3製品共通。#325）。システム設定の 5 画面だけで、RAG 固有の設定
 * （取込・検索・回答の設定など）やユーザー・ロール・権限管理は DB を使うのでゲートを通す。
 */
export const isDatabaseGateExempt = isDatabaseGateExemptPath;

/** 共通の DB ゲートの文言を、RAG の辞書にある値で上書きする（製品名の入る文言など）。 */
export function databaseGateMessages(): Partial<DatabaseGateMessages> {
  return Object.fromEntries(
    Object.keys(DATABASE_GATE_MESSAGES)
      .filter((key): key is I18nKey => key in ja)
      .map((key) => [key, t(key)])
  );
}

/** 設定ページ以外を開く前にデータベースの利用可否を確認する（3製品共通の部品。#325）。 */
export function DatabaseGate({ children }: { children: ReactNode }) {
  return (
    <SharedDatabaseGate api={api} routes={DATABASE_GATE_ROUTES} messages={databaseGateMessages()}>
      {children}
    </SharedDatabaseGate>
  );
}
