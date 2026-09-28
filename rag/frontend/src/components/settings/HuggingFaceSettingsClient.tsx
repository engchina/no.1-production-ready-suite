"use client";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormStatus,
  PageBody,
  Skeleton,
  StatusBadge,
  TextField,
  TimedLoadingState,
} from "@engchina/production-ready-ui";
import { Eye, EyeOff, HardDriveDownload, Save } from "lucide-react";
import { useRef, useState } from "react";

import { ErrorState } from "@/components/StateViews";
import {
  ApiError,
  type HuggingFaceSettingsData,
  type HuggingFaceSettingsUpdate,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { useHuggingFaceSettings, useUpdateHuggingFaceSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

interface HuggingFaceForm {
  endpoint: string;
  token: string;
  clearToken: boolean;
}

const EMPTY_FORM: HuggingFaceForm = {
  endpoint: "",
  token: "",
  clearToken: false,
};

/**
 * HuggingFace モデルダウンロード（token / ミラー）の設定（#287）。
 *
 * 共通のシステム設定の画面（`@engchina/production-ready-system-settings` のモデル設定・アップロード保存先）と
 * 同じ構成にそろえる。
 * - 読み込み中は TimedLoadingState + Skeleton、取得の失敗は ErrorState（再試行）
 * - 保存中は入力と再送信を止め、成功は Toast、失敗は操作行の FormStatus に出す
 * - 背景の再取得では、利用者が編集していない項目だけを更新する（UX 契約 workspace-state.md）
 * - 未保存の変更と保存中の離脱は共通の離脱ガードで確認する
 */
export function HuggingFaceSettingsClient() {
  const query = useHuggingFaceSettings();
  const save = useUpdateHuggingFaceSettings();
  const formRef = useRef<HTMLFormElement>(null);

  const [server, setServer] = useState<HuggingFaceSettingsData | null>(null);
  const [form, setForm] = useState<HuggingFaceForm>(EMPTY_FORM);
  const [tokenVisible, setTokenVisible] = useState(false);

  // server 値が変わったレンダーで、編集していない項目だけを server 値に合わせる（effect で setState しない）。
  const serverChanged = useValuesChanged([query.data]);
  if (serverChanged && query.data) {
    const next = formFromSettings(query.data);
    const merged = server ? keepEditedFields(form, formFromSettings(server), next) : next;
    // 保存済み token がなくなったら削除の指定を外す（token 欄が無効のまま残らないように）。
    setForm(query.data.token_configured ? merged : { ...merged, clearToken: false });
    setServer(query.data);
  }
  const settings = server ?? query.data ?? null;

  const isDirty = Boolean(
    settings && (form.endpoint !== settings.endpoint || form.token !== "" || form.clearToken)
  );
  // token は保存しない。入力中の endpoint / token / 削除の指定があるときと保存中は離脱を確認する。
  useLeaveGuard(isDirty, save.isPending);

  function updateForm(update: Partial<HuggingFaceForm>) {
    if (save.isPending) return;
    setForm((current) => ({ ...current, ...update }));
    save.reset();
  }

  function submit() {
    if (save.isPending || !settings) return;
    save.mutate(payloadFromForm(form), {
      onSuccess: (data) => {
        setServer(data);
        setForm(formFromSettings(data));
        setTokenVisible(false);
        toast.success(t("settings.huggingface.actions.saved"));
      },
    });
  }

  if (!settings) {
    if (query.isError) {
      return (
        <PageBody wide>
          <ErrorState
            message={
              query.error instanceof ApiError
                ? query.error.message
                : t("settings.huggingface.loadError")
            }
            onRetry={() => void query.refetch()}
          />
        </PageBody>
      );
    }
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.huggingface.loading")}
          operationKey="settings-huggingface-load"
          placement="page"
          testId="settings-huggingface-loading"
        >
          {/* 設定カード（見出し・入力 2 項目・操作行・補足）の寸法を予約する。 */}
          <Skeleton className="h-96 w-full rounded-lg" />
        </TimedLoadingState>
      </PageBody>
    );
  }

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.huggingface.saveError");

  return (
    <PageBody wide>
      <form
        ref={formRef}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset
          disabled={save.isPending}
          aria-busy={save.isPending}
          className="min-w-0 space-y-6"
        >
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <HardDriveDownload size={16} className="text-accent-fg" aria-hidden />
                {t("settings.huggingface.cardTitle")}
              </CardTitle>
              <CardDescription>{t("settings.huggingface.cardDescription")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* ミラー endpoint と token は意味のペアなので、広い画面（lg）では同じ行に置く。 */}
              <div className="grid gap-x-6 gap-y-5 lg:grid-cols-2">
                <TextField
                  id="hf-endpoint"
                  label={t("settings.huggingface.field.endpoint")}
                  value={form.endpoint}
                  onValueChange={(value) => updateForm({ endpoint: value })}
                  placeholder={t("settings.huggingface.placeholder.endpoint")}
                  helper={t("settings.huggingface.helper.endpoint")}
                  autoComplete="off"
                />
                <SecretField
                  id="hf-token"
                  label={t("settings.huggingface.field.token")}
                  value={form.token}
                  visible={tokenVisible}
                  disabled={form.clearToken}
                  hasSavedSecret={settings.token_configured}
                  onToggleVisible={() => setTokenVisible((current) => !current)}
                  onChange={(value) => updateForm({ token: value })}
                />
                {settings.token_configured ? (
                  <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-surface-sunken px-4 py-3 text-sm transition-colors hover:bg-info-subtle lg:col-span-full">
                    <input
                      type="checkbox"
                      checked={form.clearToken}
                      onChange={(event) =>
                        updateForm({
                          clearToken: event.target.checked,
                          token: event.target.checked ? "" : form.token,
                        })
                      }
                      className="mt-0.5 h-4 w-4 cursor-pointer accent-[var(--color-accent-emphasis)]"
                    />
                    <span className="text-fg">{t("settings.huggingface.secrets.clearToken")}</span>
                  </label>
                ) : null}
              </div>

              <FormActionBar
                ariaLabel={t("settings.huggingface.actions.label")}
                primaryActions={[
                  {
                    id: "save",
                    label: t("settings.huggingface.actions.save"),
                    icon: Save,
                    loading: save.isPending,
                    testId: "settings-huggingface-save",
                    onClick: () => formRef.current?.requestSubmit(),
                  },
                ]}
                status={save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
              />

              <div className="space-y-1 text-xs leading-relaxed text-fg-muted">
                <p>{t("settings.huggingface.hint")}</p>
                <p>{t("settings.huggingface.hintCache")}</p>
              </div>
            </CardContent>
          </Card>
        </fieldset>
      </form>
    </PageBody>
  );
}

/**
 * 保存済みの値を表示しない secret の入力欄（共通のモデル設定の API キー欄と同じ構成）。
 * 入力欄は共通 TextField と同じ高さ・枠線・フォーカス表示にそろえ、表示の切り替えは共通 Button を使う。
 */
function SecretField({
  id,
  label,
  value,
  visible,
  disabled,
  hasSavedSecret,
  onChange,
  onToggleVisible,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  disabled: boolean;
  hasSavedSecret: boolean;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const hintId = `${id}-hint`;

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex min-h-5 flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
        {/* 隣の TextField とラベル行の高さ（20px）をそろえ、2 列の入力欄の上端を一致させる。 */}
        <StatusBadge
          className="py-0"
          variant={hasSavedSecret ? "success" : "neutral"}
          label={
            hasSavedSecret
              ? t("settings.huggingface.secrets.saved")
              : t("settings.huggingface.secrets.notSet")
          }
        />
      </div>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
          placeholder={
            hasSavedSecret
              ? t("settings.huggingface.placeholder.tokenSaved")
              : t("settings.huggingface.placeholder.token")
          }
          aria-describedby={hintId}
          className={cn(
            "w-full min-h-[var(--field-height)] rounded-md border border-border-control bg-surface px-3 pr-11 text-sm text-fg outline-none transition-colors",
            "placeholder:text-fg-muted placeholder:opacity-100",
            "focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
            "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
            "forced-colors:border-[CanvasText]"
          )}
        />
        <Button
          type="button"
          variant="ghost"
          iconOnly
          icon={visible ? EyeOff : Eye}
          disabled={disabled}
          onClick={onToggleVisible}
          aria-label={
            visible ? t("settings.huggingface.secrets.hide") : t("settings.huggingface.secrets.show")
          }
          className="absolute inset-y-0 right-0 h-full min-h-0 rounded-l-none"
        />
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
        {hasSavedSecret
          ? t("settings.huggingface.helper.tokenSaved")
          : t("settings.huggingface.helper.token")}
      </p>
    </div>
  );
}

function formFromSettings(settings: HuggingFaceSettingsData): HuggingFaceForm {
  return {
    endpoint: settings.endpoint,
    token: "",
    clearToken: false,
  };
}

/** 前回の server 値から変えていない項目だけを新しい server 値にする。 */
function keepEditedFields(
  current: HuggingFaceForm,
  previous: HuggingFaceForm,
  next: HuggingFaceForm
): HuggingFaceForm {
  return {
    endpoint: current.endpoint === previous.endpoint ? next.endpoint : current.endpoint,
    token: current.token === previous.token ? next.token : current.token,
    clearToken: current.clearToken === previous.clearToken ? next.clearToken : current.clearToken,
  };
}

function payloadFromForm(form: HuggingFaceForm): HuggingFaceSettingsUpdate {
  const payload: HuggingFaceSettingsUpdate = {
    endpoint: form.endpoint,
  };
  if (form.clearToken) payload.clear_token = true;
  else if (form.token !== "") payload.token = form.token;
  return payload;
}
