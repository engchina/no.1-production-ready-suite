/**
 * DB ゲートの既定の文言（NL2SQL の文言が基準。#325）。
 * key は製品の i18n key と同じにして、製品は自分の辞書の値で上書きする（製品名が入る文言など）。
 */
export const DATABASE_GATE_MESSAGES = {
  "common.retry": "再試行",
  "dbGate.checking": "データベースの状態を確認しています…",
  "dbGate.notConfigured.title": "データベースの接続情報が未設定です",
  "dbGate.notConfigured.message":
    "この画面を利用するには、まずデータベースの接続情報を設定してください。設定が完了すると、この画面は自動的に利用できるようになります。",
  "dbGate.unreachable.title": "データベースを起動してください",
  "dbGate.unreachable.message":
    "データベースが起動していないか、ネットワーク経由で到達できません。データベースを起動してから再試行してください。接続情報の確認・変更もデータベース設定から行えます。",
  "dbGate.setupRequired.title": "データベース接続済み・初期化が必要です",
  "dbGate.setupRequired.message":
    "データベースへの接続は確認できましたが、システムテーブルが初期化されていません。システムテーブルを作成・更新してください。",
  "dbGate.checkFailed.title": "データベースの状態を確認できません",
  "dbGate.checkFailed.message": "バックエンドの起動状態を確認して再試行してください。",
  "dbGate.openDatabaseSettings": "データベース設定を開く",
  "dbGate.openSystemTables": "システムテーブルを開く",
  "dbGate.settingsHint":
    "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。",
  "dbGate.setupRequired.settingsHint":
    "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。",
  "dbGate.reasonCode": "診断コード: {code}",
  // 診断コード（`check`）ごとの補足。無いコードは診断コードだけを出す。
  "dbGate.check.missing": "接続ユーザーまたはサービス名（DSN）が設定されていません。",
  "dbGate.check.missing_credentials": "DB パスワードが設定されていません。",
  "dbGate.check.wallet_not_found":
    "Wallet の保存先に接続用のファイルがそろっていません。Wallet をアップロードし直してください。",
  "dbGate.check.wallet_password_invalid":
    "暗号化された Wallet を現在の Wallet パスワードで復号できません。Wallet パスワードを確認してください。",
  "dbGate.check.invalid":
    "サービス名（DSN）が現在の Wallet の tnsnames.ora にありません。サービス名を選び直してください。",
  "dbGate.check.walletless_tls_dsn_required":
    "Walletless TLS では、Wallet のサービス名ではなく ADB の TCPS 接続文字列または Easy Connect の DSN を指定してください。",
  "dbGate.check.invalid_configuration":
    "接続設定の組み合わせがこの製品で使えません。バックエンドの設定を確認してください。",
  "dbGate.check.schema_check_failed":
    "システムテーブルの状態を確認できませんでした。再試行しても解消しない場合は、バックエンドのログを確認してください。",
  "dbGate.check.migration_required": "システムテーブルの作成・更新（migration の適用）が必要です。",
  "dbGate.check.migration_check_failed":
    "システムテーブルの状態を確認できませんでした。再試行しても解消しない場合は、バックエンドのログを確認してください。",
} as const;

export type DatabaseGateMessageKey = keyof typeof DATABASE_GATE_MESSAGES;
export type DatabaseGateMessages = Record<DatabaseGateMessageKey, string>;

/** 診断コードの補足の key（無いコードは undefined）。 */
export function databaseCheckMessageKey(code: string): DatabaseGateMessageKey | undefined {
  const key = `dbGate.check.${code}`;
  return Object.prototype.hasOwnProperty.call(DATABASE_GATE_MESSAGES, key)
    ? (key as DatabaseGateMessageKey)
    : undefined;
}
