import {
  Button,
  FormActionBar,
  FormGrid,
  FormStatus,
  ProcessingIndicator,
  SideSheet,
  StatusBadge,
  TextField,
  type FieldWidth,
} from "@production-ready/ui";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
  type SettingsTestResultTone,
} from "@production-ready/system-settings";
import { Plug, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import {
  ApiError,
  type ExternalParserBackendName,
  type ExternalParserConnectionData,
  type ExternalParserConnectionStatus,
  type ExternalParserConnectionStatusData,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useExternalParserStatus, useUpdateParserAdapterSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

import { FeaturePromptDisclosure } from "./AnswerPromptEditor";
import {
  connectionFormFromSettings,
  connectionFormIsDirty,
  connectionUpdatePayload,
  validateConnectionForm,
  type ConnectionFieldErrors,
  type ExternalParserConnectionForm,
} from "./ExternalConnectionSheet.logic";

const EXTERNAL_PROTOCOL_LABEL = "OpenAI /v1/chat/completions";
const EXTERNAL_ENDPOINT_PLACEHOLDER = "https://api.example.com/v1";

/** 外部 GPU 接続の欄の上限（backend の ExternalParserConnectionUpdate の max_length と同じ。#976）。 */
const EXTERNAL_CONNECTION_MAX_LENGTH = { endpoint: 2048, model: 512, apiKey: 4096 } as const;

export function externalConnectionSheetId(backend: ExternalParserBackendName) {
  return `external-parser-connection-sheet-${backend}`;
}

/**
 * 外部 GPU 解析エンジンの接続の設定（Endpoint・Model・API key・接続の確認・読み取りプロンプト）を右のサイドパネルで開く（#4740）。
 * 形は根拠を表示するパネル（`EvidenceSourceSheet`）と同じ `SideSheet`（右・`wide`・開くとパネルへフォーカス）。
 * 保存はパネルの中で行い、接続だけを送る（画面の使用エンジンの未保存の選択は送らない）。未保存のまま閉じるときは確認する。
 * API key の値は欄にも応答にも出さず、設定済みかどうかだけを示す。
 */
export function ExternalConnectionSheet({
  backend,
  backendLabel,
  connection,
  open,
  onClose,
}: {
  backend: ExternalParserBackendName;
  backendLabel: string;
  connection: ExternalParserConnectionData | undefined;
  open: boolean;
  onClose: () => void;
}) {
  const save = useUpdateParserAdapterSettings();
  const [form, setForm] = useState<ExternalParserConnectionForm>(() =>
    connectionFormFromSettings(backend, connection)
  );
  const [errors, setErrors] = useState<ConnectionFieldErrors>({});
  const [promptDirty, setPromptDirty] = useState(false);
  // 閉じるたびに増やし、読み取りプロンプトの編集中の内容を捨てる。パネルは閉じる動きの間も描くので、本体ごとは作り直さない。
  const [session, setSession] = useState(0);
  // 最初に開くまで中身を描かない（プロンプトの取得などをしない）。
  const [opened, setOpened] = useState(open);
  if (open && !opened) setOpened(true);

  // 保存値が変わったレンダーだけ、未編集なら欄をそろえる（編集中の内容は背景の再取得で上書きしない）。
  const serverForm = connectionFormFromSettings(backend, connection);
  const serverKey = JSON.stringify(serverForm);
  const [base, setBase] = useState(serverKey);
  if (serverKey !== base) {
    setBase(serverKey);
    if (JSON.stringify(form) === base) setForm(serverForm);
  }

  const connectionDirty = connectionFormIsDirty(backend, form, connection);
  const confirmDiscard = useLeaveGuard(connectionDirty || promptDirty, save.isPending, {
    discardDescription: t("settings.parserAdapters.connection.discardDescription"),
    discardConfirm: t("settings.parserAdapters.connection.discardConfirm"),
  });

  function update(change: Partial<ExternalParserConnectionForm>) {
    save.reset();
    setErrors((current) => {
      const next = { ...current };
      for (const field of Object.keys(change)) delete next[`${backend}.${field}`];
      return next;
    });
    setForm((current) => ({ ...current, ...change }));
  }

  function discardConnection() {
    save.reset();
    setErrors({});
    setForm(serverForm);
  }

  async function requestClose() {
    if (!(await confirmDiscard())) return;
    discardConnection();
    setSession((current) => current + 1);
    onClose();
  }

  function submit() {
    if (save.isPending) return;
    const nextErrors = validateConnectionForm(backend, form);
    setErrors(nextErrors);
    const firstError = Object.keys(nextErrors)[0];
    if (firstError) {
      requestAnimationFrame(() => focusConnectionError(firstError));
      return;
    }
    save.mutate(
      { connections: [connectionUpdatePayload(backend, form)] },
      {
        onSuccess: (data) => {
          const next = connectionFormFromSettings(
            backend,
            (data.connections ?? []).find((candidate) => candidate.backend === backend)
          );
          setBase(JSON.stringify(next));
          setForm(next);
          // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
          toast.success(t("settings.parserAdapters.actions.saved"));
        },
      }
    );
  }

  const saving = save.isPending;
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.parserAdapters.saveError");

  return (
    <SideSheet
      id={externalConnectionSheetId(backend)}
      open={open}
      onClose={() => void requestClose()}
      title={t("settings.parserAdapters.connection.settingsLabel", { backend: backendLabel })}
      closeLabel={t("common.dismiss")}
      side="right"
      size="wide"
      initialFocus="dialog"
      bodyClassName="gap-4"
      data-testid={`external-parser-connection-${backend}`}
    >
      {opened ? (
        <>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 flex-1 basis-60">
              <p className="break-all text-xs text-fg-muted">{EXTERNAL_PROTOCOL_LABEL}</p>
              <p className="mt-1 text-xs leading-relaxed text-fg-muted">
                {t("settings.parserAdapters.connection.description")}
              </p>
            </div>
            <ConnectionConfiguredBadge configured={Boolean(connection?.configured)} />
          </div>

          <FormGrid columns={1}>
            <ConnectionTextField
              id={`external-parser-${backend}-endpoint`}
              label={t("settings.parserAdapters.connection.endpoint")}
              width="full"
              type="url"
              maxLength={EXTERNAL_CONNECTION_MAX_LENGTH.endpoint}
              value={form.endpoint}
              placeholder={EXTERNAL_ENDPOINT_PLACEHOLDER}
              error={errors[`${backend}.endpoint`]}
              disabled={saving}
              onChange={(endpoint) => update({ endpoint })}
            />
            <ConnectionTextField
              id={`external-parser-${backend}-model`}
              label={t("settings.parserAdapters.connection.model")}
              width="md"
              maxLength={EXTERNAL_CONNECTION_MAX_LENGTH.model}
              value={form.model}
              placeholder="model-id"
              // Endpoint を入力したときだけ Model が要る（validateConnectionForm と同じ条件。#531）
              required={Boolean(form.endpoint.trim())}
              error={errors[`${backend}.model`]}
              disabled={saving}
              onChange={(model) => update({ model })}
            />
            <ConnectionTextField
              id={`external-parser-${backend}-api-key`}
              label={t("settings.parserAdapters.connection.apiKey")}
              width="lg"
              type="password"
              maxLength={EXTERNAL_CONNECTION_MAX_LENGTH.apiKey}
              value={form.api_key}
              // 任意の欄は placeholder で「任意」と示さない（#531）。保存済みのときだけ保持の説明を出す。
              placeholder={
                connection?.api_key_configured
                  ? t("settings.parserAdapters.connection.apiKeyRetained")
                  : undefined
              }
              disabled={saving || form.clear_api_key}
              onChange={(api_key) => update({ api_key, clear_api_key: false })}
            />
            <label
              htmlFor={`external-parser-${backend}-clear-api-key`}
              className="flex min-h-[var(--control-height-touch)] cursor-pointer items-center gap-2 rounded-md text-xs text-fg"
            >
              <input
                id={`external-parser-${backend}-clear-api-key`}
                type="checkbox"
                checked={form.clear_api_key}
                disabled={saving || !connection?.api_key_configured}
                onChange={(event) => update({ clear_api_key: event.target.checked, api_key: "" })}
                className="h-4 w-4 rounded border-border accent-accent-emphasis"
              />
              {t("settings.parserAdapters.connection.clearApiKey")}
            </label>
          </FormGrid>

          <FormActionBar
            ariaLabel={t("settings.parserAdapters.connection.actionsLabel", { backend: backendLabel })}
            primaryActions={[
              {
                id: "save",
                label: t("settings.parserAdapters.actions.save"),
                icon: Save,
                loading: saving,
                disabled: !connectionDirty,
                onClick: submit,
              },
            ]}
            secondaryActions={[
              {
                id: "reset",
                label: t("settings.parserAdapters.actions.reset"),
                icon: RotateCcw,
                disabled: !connectionDirty || saving,
                onClick: discardConnection,
              },
            ]}
            status={
              save.isError ? (
                <FormStatus tone="danger" message={saveError} />
              ) : connectionDirty ? (
                <FormStatus tone="warning" message={t("settings.parserAdapters.actions.unsaved")} />
              ) : null
            }
          />

          <ConnectionTestSection
            backend={backend}
            backendLabel={backendLabel}
            configured={Boolean(connection?.configured)}
            disabled={saving}
            dirty={connectionDirty}
          />

          <FeaturePromptDisclosure key={session} promptKey="external_ocr" onDirtyChange={setPromptDirty} />
        </>
      ) : null}
    </SideSheet>
  );
}

function ConnectionTestSection({
  backend,
  backendLabel,
  configured,
  disabled,
  dirty,
}: {
  backend: ExternalParserBackendName;
  backendLabel: string;
  configured: boolean;
  disabled: boolean;
  dirty: boolean;
}) {
  const statusQuery = useExternalParserStatus(backend);
  // 接続確認の所要時間（API は返さないため、押してから結果が返るまでを画面で測る。messaging.md §10.3）。
  const [elapsedMs, setElapsedMs] = useState<number | undefined>(undefined);
  const runConnectionTest = async () => {
    const startedAt = Date.now();
    await statusQuery.refetch();
    setElapsedMs(Date.now() - startedAt);
  };
  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="secondary"
        className="w-full sm:w-auto"
        loading={statusQuery.isFetching}
        disabled={disabled || dirty || !configured}
        onClick={() => void runConnectionTest()}
        icon={Plug}
      >
        {t("settings.parserAdapters.connection.test")}
      </Button>
      {statusQuery.isFetching ? (
        // 外部の GPU parser へ実際に接続するため、起動直後や高負荷時は数秒以上かかる。
        <ProcessingIndicator
          active
          label={t("settings.parserAdapters.connection.testing", { backend: backendLabel })}
          operationKey={`external-parser-status-${backend}`}
          placement="action"
          activityIcon="none"
          testId={`external-parser-status-processing-${backend}`}
        />
      ) : null}
      {dirty ? (
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("settings.parserAdapters.connection.saveBeforeTest")}
        </p>
      ) : null}
      {/* 結果は「接続を確認」の直下に全幅で出し、入力を変えたら消す（messaging.md §10）。 */}
      {!dirty && !statusQuery.isFetching && (statusQuery.data || statusQuery.isError) ? (
        <ConnectionTestResult
          backend={backend}
          data={statusQuery.isError ? undefined : statusQuery.data}
          error={statusQuery.isError ? statusQuery.error : null}
          elapsedMs={elapsedMs}
          checkedAt={statusQuery.isError ? statusQuery.errorUpdatedAt : statusQuery.dataUpdatedAt}
        />
      ) : null}
    </div>
  );
}

function ConnectionTextField({
  id,
  label,
  value,
  width,
  type = "text",
  maxLength,
  placeholder,
  required = false,
  error,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  /** 値の種類の幅（URL は full、モデル名は md、API キーは lg。#1891）。 */
  width: FieldWidth;
  type?: "text" | "url" | "password";
  /** backend（ExternalParserConnectionUpdate）と同じ上限（#976）。 */
  maxLength: number;
  placeholder?: string;
  required?: boolean;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <TextField
      id={id}
      className="min-w-0"
      label={label}
      width={width}
      type={type}
      maxLength={maxLength}
      value={value}
      placeholder={placeholder}
      required={required}
      disabled={disabled}
      autoComplete="off"
      error={error}
      onValueChange={onChange}
    />
  );
}

/** 接続設定の有無（リソースの状態）。共有の StatusBadge で出す（messaging.md §10.2）。 */
export function ConnectionConfiguredBadge({ configured }: { configured: boolean }) {
  return (
    <StatusBadge
      variant={configured ? "success" : "warning"}
      label={
        configured
          ? t("settings.parserAdapters.connection.configured")
          : t("settings.parserAdapters.serviceBackend.unconfigured")
      }
    />
  );
}

const CONNECTION_TEST_TONE: Record<ExternalParserConnectionStatus, SettingsTestResultTone> = {
  available: "success",
  unconfigured: "warning",
  unreachable: "danger",
  model_missing: "danger",
  invalid_response: "danger",
};

/**
 * 接続確認の結果パネル（システム設定の接続テストと同じ部品。messaging.md §10）。
 * 1 文目に何が起きたか、次に所要時間。backend・状態・version・警告コードは「詳細」に畳む。
 */
function ConnectionTestResult({
  backend,
  data,
  error,
  elapsedMs,
  checkedAt,
}: {
  backend: ExternalParserBackendName;
  data: ExternalParserConnectionStatusData | undefined;
  error: unknown;
  elapsedMs: number | undefined;
  checkedAt: number;
}) {
  const checkedAtText = checkedAt ? formatDateTime(new Date(checkedAt).toISOString()) : undefined;
  const testId = `external-parser-test-result-${backend}`;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : null;
    return (
      <SettingsTestResultPanel
        tone="danger"
        message={apiError?.message ?? t("settings.parserAdapters.connection.testFailed")}
        elapsedMs={elapsedMs}
        checkedAt={checkedAtText}
        details={toSettingsTestResultDetails({
          backend,
          status_code: apiError?.status,
          error_code: apiError?.errorCode,
          request_id: apiError?.requestId,
        })}
        testId={testId}
      />
    );
  }
  return (
    <SettingsTestResultPanel
      tone={CONNECTION_TEST_TONE[data.status]}
      message={t(`settings.parserAdapters.connection.status.${data.status}` as I18nKey)}
      elapsedMs={elapsedMs}
      checkedAt={checkedAtText}
      details={toSettingsTestResultDetails({
        backend: data.backend,
        status: data.status,
        version: data.version,
        warning_code: data.warning_code,
      })}
      testId={testId}
    />
  );
}

function focusConnectionError(key: string) {
  const [backend, field] = key.split(".");
  document.getElementById(`external-parser-${backend}-${field}`)?.focus();
}
