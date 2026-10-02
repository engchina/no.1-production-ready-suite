import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw, ShieldAlert, Upload } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ExecutionConfirmationField,
  FormStatus,
  FormSkeleton,
  PageHeader,
  ProcessingIndicator,
  StatusBadge,
  toast,
  useConfirm,
  PageBody,
  TextareaField,
  TextField,
  useActionPending,
} from "@engchina/production-ready-ui";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
  type SettingsTestResultDetail,
  type SettingsTestResultTone,
} from "@engchina/production-ready-system-settings";
import {
  agentApi,
  ApiError,
  type RuntimeSnapshot,
  type RuntimeSnapshotImportResult,
  type RuntimeSnapshotSummary,
} from "@/lib/api";
import { QueryState } from "@/components/ListViews";
import { parseJsonField } from "@/lib/field-validation";
import { formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";
import { useSettingsLeaveGuard } from "@/lib/leave-guard";
import { MetricPill, focusField } from "@/pages/shared/page-helpers";

/** 実行時スナップショットの置換の確認語（入力の完全一致で置換を許す）。 */
const SNAPSHOT_REPLACE_CONFIRMATION = "REPLACE";

/** 確認語欄の入力欄の id と、その説明（置換を使えない理由）の id（`${id}-helper` は ExecutionConfirmationField の決まり）。 */
const SNAPSHOT_REPLACE_CONFIRM_ID = "runtime-snapshot-confirm";

const SNAPSHOT_REPLACE_HELPER_ID = `${SNAPSHOT_REPLACE_CONFIRM_ID}-helper`;

export function RuntimeSnapshotSettingsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const snapshot = useQuery({
    queryKey: ["runtime", "snapshot"],
    queryFn: agentApi.exportRuntimeSnapshot,
  });
  // 「表示を更新」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();
  // 検証（dry run）と置換は別の操作なので、pending も結果も分ける（messaging.md §3.7「1 つの状態を複数のボタンで共有しない」）。
  const validateStartedRef = useRef(0);
  const [validateElapsedMs, setValidateElapsedMs] = useState<number | undefined>(undefined);
  const validateSnapshot = useMutation({
    mutationFn: agentApi.importRuntimeSnapshot,
    onMutate: () => {
      validateStartedRef.current = performance.now();
    },
    // 結果は「検証」の直下の結果パネル 1 か所に出す。成功の Toast は重ねない（messaging.md §10.1）。
    onSettled: () => setValidateElapsedMs(Math.round(performance.now() - validateStartedRef.current)),
  });
  const replaceSnapshot = useMutation({
    mutationFn: agentApi.importRuntimeSnapshot,
    onSuccess: () => {
      toast.success(t("settings.snapshot.imported"));
      void queryClient.invalidateQueries();
      // 置換が済んだ入力は下書きではなくなるので空に戻す（確認語も解除する）。#87
      setImportText("");
      setReason("");
      setConfirmText("");
      validateSnapshot.reset();
    },
  });
  const [exportText, setExportText] = useState("");
  const [importText, setImportText] = useState("");
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const importPending = validateSnapshot.isPending || replaceSnapshot.isPending;
  // インポート JSON と理由は未保存の下書き。確認語は保存も復元もしない（離脱で state ごと消える）。#87
  useSettingsLeaveGuard(importText.trim() !== "" || reason.trim() !== "", replaceSnapshot.isPending);

  // 取得したスナップショットが変わったレンダーで、エクスポート欄を取り直す（effect で setState しない）。
  const snapshotChanged = useValuesChanged([snapshot.data]);
  if (snapshotChanged && snapshot.data) {
    setExportText(JSON.stringify(snapshot.data, null, 2));
  }

  function parseImportSnapshot(): RuntimeSnapshot | null {
    // 未入力・JSON の形式のエラーは、どちらもインポート JSON の欄の直下に出す（#541）。
    const parsed = parseJsonField<RuntimeSnapshot>(importText, t("settings.snapshot.importJson"), {
      required: true,
      expect: "object",
    });
    if (!parsed.ok || !parsed.value) {
      setImportError(parsed.ok ? t("settings.snapshot.importJsonRequired") : parsed.error);
      focusField("runtime-snapshot-import");
      return null;
    }
    setImportError(null);
    return parsed.value;
  }

  function copyCurrentSnapshotToImport() {
    setImportText(exportText);
    // 入力が変わったら、前の入力の検証の結果は消す（messaging.md §10.4）。
    validateSnapshot.reset();
    replaceSnapshot.reset();
    setImportError(null);
  }

  function downloadSnapshot() {
    if (!exportText) {
      return;
    }
    const blob = new Blob([exportText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `agent-runtime-snapshot-${new Date().toISOString()}.json`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success(t("settings.snapshot.downloaded"));
  }

  function dryRunImport() {
    const parsed = parseImportSnapshot();
    if (!parsed) {
      return;
    }
    validateSnapshot.mutate({
      snapshot: parsed,
      dry_run: true,
      confirm_replace: false,
      reason: reason.trim() || null,
    });
  }

  async function replaceRuntimeSnapshot() {
    const parsed = parseImportSnapshot();
    if (!parsed) {
      return;
    }
    // 確認語が一致するまで置換のボタンは押せず、理由は確認語の欄の説明（helper）が示す。
    if (confirmText !== SNAPSHOT_REPLACE_CONFIRMATION) {
      return;
    }
    const ok = await confirm({
      title: t("settings.snapshot.replaceTitle"),
      description: t("settings.snapshot.replaceDescription"),
      confirmLabel: t("common.replace"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) {
      return;
    }
    replaceSnapshot.mutate({
      snapshot: parsed,
      dry_run: false,
      confirm_replace: true,
      reason: reason.trim() || null,
    });
  }

  const currentSummary = snapshot.data ? summarizeSnapshot(snapshot.data) : null;

  return (
    <>
      <PageHeader
        wide
        title={t("nav.settingsRuntimeSnapshot")}
        subtitle={t("page.settings.runtimeSnapshot.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => snapshot.refetch()),
          },
        ]}
      />
      <PageBody wide className="grid min-w-0 grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <QueryState query={snapshot} loadingLabel={t("loading.snapshot")} skeleton={<FormSkeleton fields={2} />}>
          <Card className="min-w-0">
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <CardTitle>{t("settings.snapshot.export")}</CardTitle>
                <CardDescription>{t("settings.snapshot.current")}</CardDescription>
              </div>
              {currentSummary ? <SnapshotSummaryBadge summary={currentSummary} /> : null}
            </CardHeader>
            <CardContent className="space-y-4">
              {currentSummary ? <SnapshotSummaryGrid summary={currentSummary} /> : null}
              <TextareaField
                id="runtime-snapshot-export"
                label={t("settings.snapshot.current")}
                value={exportText}
                readOnly
                monospace
                spellCheck={false}
                rows={16}
              />
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={downloadSnapshot} icon={Download}>
                  {t("common.download")}
                </Button>
                <Button variant="secondary" onClick={copyCurrentSnapshotToImport} icon={Upload}>
                  {t("settings.snapshot.copyCurrent")}
                </Button>
              </div>
            </CardContent>
          </Card>
        </QueryState>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("settings.snapshot.import")}</CardTitle>
            <CardDescription>{t("page.settings.runtimeSnapshot.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <TextareaField
              id="runtime-snapshot-import"
              label={t("settings.snapshot.importJson")}
              required
              error={importError ?? undefined}
              value={importText}
              onValueChange={(value) => {
                setImportText(value);
                validateSnapshot.reset();
                replaceSnapshot.reset();
                setImportError(null);
              }}
              monospace
              spellCheck={false}
              rows={16}
            />
            <TextField
              id="runtime-snapshot-reason"
              label={t("settings.snapshot.reason")}
              width="full"
              value={reason}
              onValueChange={setReason}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={dryRunImport}
                loading={validateSnapshot.isPending}
                disabled={replaceSnapshot.isPending}
                icon={ShieldAlert}
                data-testid="runtime-snapshot-validate"
              >
                {t("common.validate")}
              </Button>
            </div>
            {/* 検証の結果は「検証」の直下にカードの全幅で 1 つだけ出す（messaging.md §10）。 */}
            {validateSnapshot.isPending ? (
              <ProcessingIndicator
                active
                label={t("settings.snapshot.validating")}
                operationKey="runtime-snapshot-validate"
                placement="result"
                // スピナーは押した「検証」のボタンが担う（messaging.md §3.7）。
                activityIcon="none"
                testId="runtime-snapshot-validating"
              />
            ) : validateSnapshot.data ? (
              <SnapshotValidationPanel result={validateSnapshot.data} elapsedMs={validateElapsedMs} />
            ) : validateSnapshot.error ? (
              <SnapshotValidationFailure error={validateSnapshot.error} elapsedMs={validateElapsedMs} />
            ) : null}
            <ExecutionConfirmationField
              id={SNAPSHOT_REPLACE_CONFIRM_ID}
              value={confirmText}
              onChange={setConfirmText}
              confirmed={confirmText === SNAPSHOT_REPLACE_CONFIRMATION}
              expectedLabel={SNAPSHOT_REPLACE_CONFIRMATION}
              placeholder={t("settings.snapshot.confirmPlaceholder")}
              helper={t("settings.snapshot.confirmRequired")}
              labels={{ label: t("settings.snapshot.confirmText") }}
              disabled={importPending}
              actions={
                <Button
                  variant="danger"
                  size="lg"
                  className="w-full sm:w-auto"
                  onClick={() => void replaceRuntimeSnapshot()}
                  loading={replaceSnapshot.isPending}
                  disabled={confirmText !== SNAPSHOT_REPLACE_CONFIRMATION || validateSnapshot.isPending}
                  // 使えない間は、理由（確認語欄の説明。id は ExecutionConfirmationField の `${id}-helper`）を
                  // ボタンの説明として読み上げる。一致したら外す（#426。#379 の置き換えで外れていた）。
                  aria-describedby={confirmText !== SNAPSHOT_REPLACE_CONFIRMATION ? SNAPSHOT_REPLACE_HELPER_ID : undefined}
                  icon={Upload}>
                  {t("common.replace")}
                </Button>
              }
            />
            {/* 置換の失敗は置換の操作の行の直下に出す（messaging.md §3.3 / §10.2）。成功は Toast。 */}
            <FormStatus
              tone="danger"
              message={
                replaceSnapshot.error
                  ? t("settings.snapshot.replaceFailed", { reason: replaceSnapshot.error.message })
                  : null
              }
            />
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}

function SnapshotSummaryBadge({ summary }: { summary: RuntimeSnapshotSummary }) {
  return (
    // 件数の表示。保留中の承認・tool call の有無は隣の集計（SnapshotSummaryGrid）が数値で示すので、
    // ここで色だけで状態を表さない。
    <StatusBadge variant="neutral" label={t("settings.snapshot.runCount", { count: formatNumber(summary.runs) })} icon={false} />
  );
}

function SnapshotSummaryGrid({ summary }: { summary: RuntimeSnapshotSummary }) {
  const items: Array<[I18nKey, number]> = [
    ["settings.snapshot.count.runs", summary.runs],
    ["settings.snapshot.count.agents", summary.agents],
    ["settings.snapshot.count.events", summary.events],
    ["settings.snapshot.count.steps", summary.steps],
    ["settings.snapshot.count.approvals", summary.approvals],
    ["settings.snapshot.count.artifacts", summary.artifacts],
    ["settings.snapshot.count.pendingToolCalls", summary.pending_tool_calls],
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label={t("settings.snapshot.summary")}>
      {items.map(([label, value]) => (
        <MetricPill key={label} label={t(label)} value={formatNumber(value)} />
      ))}
    </div>
  );
}

function snapshotSummaryDetails(summary: RuntimeSnapshotSummary): SettingsTestResultDetail[] {
  const items: Array<[I18nKey, number]> = [
    ["settings.snapshot.count.runs", summary.runs],
    ["settings.snapshot.count.agents", summary.agents],
    ["settings.snapshot.count.events", summary.events],
    ["settings.snapshot.count.steps", summary.steps],
    ["settings.snapshot.count.approvals", summary.approvals],
    ["settings.snapshot.count.artifacts", summary.artifacts],
    ["settings.snapshot.count.pendingToolCalls", summary.pending_tool_calls],
  ];
  return items.map(([label, value]) => ({ label: t(label), value: formatNumber(value) }));
}

/**
 * スナップショットの検証の結果（messaging.md §10。#814）。1 つの結果パネルに、何が起きたか（有効 / 警告あり / 無効）、
 * 所要時間、エラー・警告の一覧を出し、件数の集計は「詳細」に畳む（無効のときだけ開く）。
 */
function SnapshotValidationPanel({ result, elapsedMs }: { result: RuntimeSnapshotImportResult; elapsedMs?: number }) {
  const { valid, errors, warnings, summary } = result.validation;
  const tone: SettingsTestResultTone = !valid ? "danger" : warnings.length ? "warning" : "success";
  const message = !valid
    ? t("settings.snapshot.result.invalid", { count: formatNumber(errors.length) })
    : warnings.length
      ? t("settings.snapshot.result.validWithWarnings", { count: formatNumber(warnings.length) })
      : t("settings.snapshot.result.valid");
  return (
    <SettingsTestResultPanel
      tone={tone}
      message={message}
      elapsedMs={elapsedMs}
      details={snapshotSummaryDetails(summary)}
      testId="runtime-snapshot-validation"
    >
      <SnapshotIssueList title={t("settings.snapshot.errors")} items={errors} />
      <SnapshotIssueList title={t("settings.snapshot.warnings")} items={warnings} />
    </SettingsTestResultPanel>
  );
}

function SnapshotIssueList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-semibold text-fg">{title}</p>
      <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed">
        {items.map((item) => (
          <li key={item} className="min-w-0 break-words [overflow-wrap:anywhere]">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 検証の要求そのものが失敗したとき（サーバーの障害・形式の拒否など）。 */
function SnapshotValidationFailure({ error, elapsedMs }: { error: Error; elapsedMs?: number }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <SettingsTestResultPanel
      tone="danger"
      message={t("settings.snapshot.result.failed")}
      elapsedMs={elapsedMs}
      troubleshooting={[error.message, t("settings.snapshot.result.failedHint")]}
      details={toSettingsTestResultDetails({
        status_code: apiError?.status,
        error_code: apiError?.errorCode,
        request_id: apiError?.requestId,
      })}
      testId="runtime-snapshot-validation"
    />
  );
}

function summarizeSnapshot(snapshot: RuntimeSnapshot): RuntimeSnapshotSummary {
  return {
    runs: snapshot.runs.length,
    agents: snapshot.agents.length,
    events: snapshot.runs.reduce((sum, run) => sum + run.events.length, 0),
    steps: snapshot.runs.reduce((sum, run) => sum + run.steps.length, 0),
    approvals: snapshot.runs.reduce((sum, run) => sum + run.approvals.length, 0),
    artifacts: snapshot.runs.reduce((sum, run) => sum + run.artifacts.length, 0),
    pending_tool_calls: snapshot.runs.reduce((sum, run) => sum + run.pending_tool_calls.length, 0),
  };
}
