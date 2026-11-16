import { FileKey } from "lucide-react";
import { Link } from "react-router-dom";

import {
  PreconditionBanner,
  preconditionBannerItem,
  type CombinedBannerItem,
  type PreconditionBannerProps,
} from "@production-ready/ui";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

/**
 * Select AI の Credential が無いときの事前条件の案内（#1683 / #1688）。共有の `PreconditionBanner`
 * （messaging.md §3.4 / §3.4.1）に、NL2SQL の文言と導線（運用設定の「Select AI Credential」。ナビと同じ
 * `FileKey`）を渡す。操作（保存・実行・送信・生成・評価）の区画の先頭に置き、操作は `disabled` にして
 * `id` を `aria-describedby` で結ぶ。Credential の画面を開けない利用者には導線を出さず、
 * システム管理者への依頼を案内する。同じ区画にほかの案内があるときは、`selectAiCredentialPreconditionItem` で
 * 同じ枠の項目にする（messaging.md §3.4.3）。
 */
type SelectAiCredentialPreconditionOptions = {
  id: string;
  testId: string;
  /** 何の操作に要るか（翻訳済み。例「業務プロファイルの保存（DBMS_CLOUD_AI Profile への反映）」）。 */
  operation: string;
  /** 押せなくした操作の名前（翻訳済み。ボタンのラベルと同じ語）。 */
  action: string;
  credentialName: string;
  canOpenSettings: boolean;
  rechecking: boolean;
  onRecheck: () => void;
};

function selectAiCredentialPreconditionProps({
  id,
  testId,
  operation,
  action,
  credentialName,
  canOpenSettings,
  rechecking,
  onRecheck,
}: SelectAiCredentialPreconditionOptions): PreconditionBannerProps {
  return {
    id,
    testId,
    title: t("selectAiCredential.precondition.title"),
    message: t(
      canOpenSettings
        ? "selectAiCredential.precondition.message"
        : "selectAiCredential.precondition.contactAdmin",
      { operation, action, name: credentialName || "NL2SQL_OCI_CRED" },
    ),
    settingsLink: canOpenSettings
      ? {
          to: APP_ROUTES.settingsSelectAiCredential,
          label: t("selectAiCredential.precondition.open"),
          icon: FileKey,
          linkComponent: Link,
          testId: `${testId}-open-settings`,
        }
      : undefined,
    onRetry: onRecheck,
    retryLabel: t("common.retry"),
    retrying: rechecking,
    retryTestId: `${testId}-retry`,
  };
}

export function SelectAiCredentialPrecondition(props: SelectAiCredentialPreconditionOptions) {
  return <PreconditionBanner {...selectAiCredentialPreconditionProps(props)} />;
}

/**
 * 同じ区画（操作のフォーム）にほかの案内があるときの、同じ枠（`CombinedBanner`）の項目（messaging.md §3.4.3。#5226）。
 * `id` は項目の要素に付くので、押せなくした操作の `aria-describedby` はそのまま結べる。
 */
export function selectAiCredentialPreconditionItem(
  props: SelectAiCredentialPreconditionOptions,
): CombinedBannerItem {
  return preconditionBannerItem({ ...selectAiCredentialPreconditionProps(props), itemId: "select-ai-credential" });
}
