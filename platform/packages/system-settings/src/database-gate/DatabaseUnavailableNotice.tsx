import type { ReactNode } from "react";
import { ArrowRight, Database, DatabaseZap, RefreshCw, Settings } from "lucide-react";
import { Link } from "react-router-dom";
import { Banner, BlockedPageNotice, Button, buttonVariants } from "@engchina/production-ready-ui";

import { formatMessage } from "../auth/messages";
import {
  DATABASE_GATE_MESSAGES,
  databaseCheckMessageKey,
  type DatabaseGateMessageKey,
  type DatabaseGateMessages,
} from "./messages";
import type { DatabaseGateRoutes, DatabaseNoticeStatus } from "./types";

/** 見出しの id（カードの `aria-labelledby`。3 製品の E2E が参照する）。 */
export const DATABASE_UNAVAILABLE_TITLE_ID = "database-unavailable-title";

const NOTICE_COPY: Record<DatabaseNoticeStatus, { title: DatabaseGateMessageKey; message: DatabaseGateMessageKey }> = {
  not_configured: { title: "dbGate.notConfigured.title", message: "dbGate.notConfigured.message" },
  setup_required: { title: "dbGate.setupRequired.title", message: "dbGate.setupRequired.message" },
  unreachable: { title: "dbGate.unreachable.title", message: "dbGate.unreachable.message" },
  check_failed: { title: "dbGate.checkFailed.title", message: "dbGate.checkFailed.message" },
};

export interface DatabaseUnavailableNoticeProps {
  /** `gate`＝主領域の中央の案内カード、`banner`＝カードやページの先頭の常設の Banner。 */
  mode?: "gate" | "banner";
  status?: DatabaseNoticeStatus;
  /** 診断コード（状態 API の `check` など）。`ok` と空は出さない。 */
  reasonCode?: string | null;
  routes: DatabaseGateRoutes;
  messages?: Partial<DatabaseGateMessages>;
  /** 設定を開くリンクの `state.returnTo`（設定画面から元の画面へ戻るため）。 */
  returnTo?: string;
  /** 省略すると再試行を出さない（banner だけ。gate は常に再試行を出す）。 */
  onRetry?: () => void;
  isRetrying?: boolean;
  /** 見出しの上書き（製品固有の状態の案内・縮退の banner）。 */
  title?: string;
  /** 本文の上書き（banner では本文の中身）。 */
  message?: ReactNode;
  /** banner にデータベース設定へのリンクを添える。 */
  settingsLink?: boolean;
  className?: string;
}

/** 画面に出す診断コード（`ok` と空は出さない）。 */
export function databaseReasonCode(code: string | null | undefined): string | null {
  const value = code?.trim();
  return value && value !== "ok" ? value : null;
}

/**
 * DB が使えないときの案内（#325。NL2SQL の見た目が基準）。
 * 全状態で「設定を開く」（`setup_required` はシステムテーブル）と「再試行」を出す。
 */
export function DatabaseUnavailableNotice({
  mode = "gate",
  status = "unreachable",
  reasonCode,
  routes,
  messages,
  returnTo,
  onRetry,
  isRetrying = false,
  title,
  message,
  settingsLink = false,
  className,
}: DatabaseUnavailableNoticeProps) {
  const m: DatabaseGateMessages = { ...DATABASE_GATE_MESSAGES, ...messages };
  const copy = NOTICE_COPY[status];
  const retryButton = onRetry ? (
    <Button
      type="button"
      size={mode === "banner" ? "sm" : "md"}
      variant="secondary"
      onClick={onRetry}
      loading={isRetrying}
      icon={RefreshCw}
    >
      {m["common.retry"]}
    </Button>
  ) : null;

  if (mode === "banner") {
    return (
      <Banner
        severity="warning"
        title={title ?? m[copy.title]}
        className={className}
        action={
          retryButton || settingsLink ? (
            <>
              {retryButton}
              {settingsLink ? (
                <Link
                  to={routes.databaseSettings}
                  state={returnTo ? { returnTo } : undefined}
                  className={buttonVariants({ variant: "secondary", size: "sm" })}
                >
                  <Settings size={14} aria-hidden />
                  {m["dbGate.openDatabaseSettings"]}
                  <ArrowRight size={14} aria-hidden />
                </Link>
              ) : null}
            </>
          ) : undefined
        }
      >
        {message}
      </Banner>
    );
  }

  const setupRequired = status === "setup_required";
  const action =
    setupRequired && routes.systemTables
      ? { href: routes.systemTables, label: m["dbGate.openSystemTables"], icon: DatabaseZap }
      : { href: routes.databaseSettings, label: m["dbGate.openDatabaseSettings"], icon: Settings };
  const ActionIcon = action.icon;
  const code = databaseReasonCode(reasonCode);
  const codeMessageKey = code ? databaseCheckMessageKey(code) : undefined;

  return (
    <BlockedPageNotice
      title={title ?? m[copy.title]}
      titleId={DATABASE_UNAVAILABLE_TITLE_ID}
      icon={Database}
      tone="warning"
      message={message ?? m[copy.message]}
      className={className}
      testId="database-unavailable-notice"
      details={
        code ? (
          <>
            {codeMessageKey ? <p>{m[codeMessageKey]}</p> : null}
            <p role="status">{formatMessage(m["dbGate.reasonCode"], { code })}</p>
          </>
        ) : undefined
      }
      actions={
        <>
          <Link
            to={action.href}
            state={returnTo ? { returnTo } : undefined}
            className={buttonVariants({ variant: "primary", size: "md" })}
          >
            <ActionIcon size={16} aria-hidden />
            {action.label}
            <ArrowRight size={16} aria-hidden />
          </Link>
          {retryButton}
        </>
      }
      footer={setupRequired ? m["dbGate.setupRequired.settingsHint"] : m["dbGate.settingsHint"]}
    />
  );
}
