import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { HardDrive } from "lucide-react";
import { ButtonLink, CombinedBanner, type CombinedBannerItem } from "@production-ready/ui";

import { useAuth } from "@/components/security/AuthProvider";
import { agentApi, type RuntimeStorageStatus } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { canOpenRoute } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";

/** 保存先の状態の query key（実行環境の「状態を再取得」が取り直す）。 */
export const RUNTIME_STORAGE_QUERY_KEY = ["runtime-storage"] as const;

/**
 * 保存先の状態（#839）。保存先はバックエンドの起動時に決まり、画面の操作では変わらないので、
 * 画面を移るたびには取り直さない。
 */
export function useRuntimeStorage() {
  return useQuery({
    queryKey: RUNTIME_STORAGE_QUERY_KEY,
    queryFn: agentApi.getRuntimeStorage,
    staleTime: 5 * 60_000,
  });
}

/** 保存されない理由の 1 文目（保存先にメモリを指定・再起動が必要・DB が未設定・checkpoint が読めない）。 */
export function storageReasonKey(status: RuntimeStorageStatus): I18nKey {
  if (status.reason === "checkpoint_invalid") return "storage.notice.checkpointInvalid";
  if (status.reason === "database_not_configured") return "storage.notice.databaseNotConfigured";
  if (status.reason === "restart_required") return "storage.notice.restartRequired";
  return "storage.notice.memoryBackend";
}

/** 直し方（保存先の既定は auto。DB を設定して再起動すれば DB に保存する。#839）。 */
export function storageFixKey(status: RuntimeStorageStatus): I18nKey {
  if (status.reason === "checkpoint_invalid") return "storage.fix.checkpointInvalid";
  if (status.reason === "restart_required") return "storage.fix.restartRequired";
  if (status.reason === "database_not_configured") {
    return status.configured_backend === "auto"
      ? "storage.fix.databaseNotConfigured"
      : "storage.fix.databaseNotConfiguredExplicit";
  }
  return "storage.fix.memoryBackend";
}

/**
 * 保存先がメモリのとき、作成・変更した内容が再起動で消えることを伝える案内の項目（`CombinedBanner` の 1 項目）。
 * 直し方の詳細は実行環境の「保存先」に 1 か所だけ書き、ここからはそこへ案内する（実行環境を開けない利用者には
 * 管理者への依頼を出す）。保存されている・状態を取得できない・取得中は null（業務の画面を止めない）。
 * 同じ区画に他の案内がある画面（システムテーブルのカード）は、この項目を区画の枠に入れる（#5183）。
 */
export function useNonPersistentStorageNoticeItem(): CombinedBannerItem | null {
  const storage = useRuntimeStorage();
  const { hasPermission } = useAuth();
  const data = storage.data;
  // 保存しないと分かったときだけ出す（不明な応答では出さない）。
  if (data?.persistent !== false) return null;
  const canOpenRuntime = canOpenRoute(APP_ROUTES.runtimes, hasPermission);
  return {
    id: "storage-not-persistent",
    severity: "warning",
    title: t("storage.notice.title"),
    testId: "storage-not-persistent-notice",
    action: canOpenRuntime ? (
      <ButtonLink
        to={APP_ROUTES.runtimes}
        linkComponent={Link}
        size="sm"
        icon={HardDrive}
        testId="storage-notice-open-runtime"
      >
        {t("storage.notice.openRuntime")}
      </ButtonLink>
    ) : undefined,
    children: (
      <div className="space-y-1">
        <p>{t(storageReasonKey(data))}</p>
        {canOpenRuntime ? null : <p>{t("storage.notice.askAdmin")}</p>}
      </div>
    ),
  };
}

/**
 * 保存先がメモリのとき、作成・変更した内容が再起動で消えることを、定義・実行を作る画面の先頭に出す
 * （UX 契約 messaging.md §3.4 の Banner。状況を出し続ける）。
 */
export function NonPersistentStorageNotice({ className }: { className?: string }) {
  const item = useNonPersistentStorageNoticeItem();
  return item ? <CombinedBanner items={[item]} className={className} /> : null;
}
