import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  FormStatus,
  PageBody,
  SelectField,
  Skeleton,
  TextField,
  cn,
  toast,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, HardDrive, Save, Settings2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useSettingsDraftGuard, type DraftGuardMessages } from "../guards/useSettingsDraftGuard";
import { UPLOAD_STORAGE_MESSAGES, type UploadStorageMessages } from "./messages";
import {
  UPLOAD_STORAGE_QUERY_KEY,
  type UploadStorageApi,
  type UploadStorageBackend,
  type UploadStorageSettingsData,
  type UploadStorageSettingsUpdate,
} from "./types";

interface UploadStorageForm {
  backend: UploadStorageBackend;
  localStorageDir: string;
  objectStorageRegion: string;
  objectStorageNamespace: string;
  objectStorageBucket: string;
}

type FieldErrors = Partial<Record<keyof UploadStorageForm, string>>;

const EMPTY_FORM: UploadStorageForm = {
  backend: "local",
  localStorageDir: "",
  objectStorageRegion: "",
  objectStorageNamespace: "",
  objectStorageBucket: "",
};

const OBJECT_STORAGE_NAME_PATTERN = /^[A-Za-z0-9._-]+$/;

/** 既定のリージョン候補（NL2SQL と同じ）。 */
export const DEFAULT_OCI_REGION_OPTIONS: readonly SelectFieldOption<string>[] = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
];

export interface UploadStorageSettingsPageProps {
  /** 製品の API 関数（GET / PATCH /api/settings/upload-storage）。 */
  api: UploadStorageApi;
  /** 「OCI 認証設定を開く」で呼ぶ。未保存の変更の確認は画面側で済ませてから呼ぶ。 */
  onOpenOciSettings: () => void;
  /** 入力欄の placeholder（製品ごとの既定の保存先）。 */
  placeholders?: { localStorageDir?: string; objectStorageBucket?: string };
  /** リージョンの選択肢。 */
  regionOptions?: readonly SelectFieldOption<string>[];
  /** API エラーから画面に出すメッセージを取り出す（製品の ApiError など）。undefined なら既定の文言。 */
  errorMessage?: (error: unknown) => string | undefined;
  /** 既定の文言の上書き。 */
  messages?: Partial<UploadStorageMessages>;
  /** 離脱確認ダイアログの文言の上書き。 */
  draftGuardMessages?: Partial<DraftGuardMessages>;
}

/**
 * アップロード原本の保存先設定（3製品共通。NL2SQL の画面を基準に移設。#97）。
 *
 * - 背景の再取得では、利用者が編集していない項目だけを更新する（NL2SQL #378）
 * - 保存中は入力と再送信を止める
 * - OCI を選んだときは region / namespace / bucket を保存前に検証する（backend も 422 で拒否する）
 */
export function UploadStorageSettingsPage({
  api,
  onOpenOciSettings,
  placeholders,
  regionOptions = DEFAULT_OCI_REGION_OPTIONS,
  errorMessage,
  messages,
  draftGuardMessages,
}: UploadStorageSettingsPageProps) {
  const m = { ...UPLOAD_STORAGE_MESSAGES, ...messages };
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: UPLOAD_STORAGE_QUERY_KEY,
    queryFn: ({ signal }) => api.get({ signal }),
  });
  const save = useMutation({
    mutationFn: (payload: UploadStorageSettingsUpdate) => api.update(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: UPLOAD_STORAGE_QUERY_KEY });
    },
  });
  const [form, setForm] = useState<UploadStorageForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});

  const baseline = useRef(EMPTY_FORM);
  const confirmLeave = useSettingsDraftGuard(
    JSON.stringify(form) !== JSON.stringify(baseline.current),
    save.isPending,
    draftGuardMessages,
  );

  useEffect(() => {
    if (!query.data) return;
    const next = formFromSettings(query.data);
    const previous = baseline.current;
    baseline.current = next;
    setForm(
      (current) =>
        Object.fromEntries(
          Object.keys(next).map((key) => {
            const field = key as keyof UploadStorageForm;
            return [field, current[field] === previous[field] ? next[field] : current[field]];
          }),
        ) as unknown as UploadStorageForm,
    );
  }, [query.data]);

  function updateForm(update: Partial<UploadStorageForm>) {
    if (save.isPending) return;
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(update) as Array<keyof UploadStorageForm>) {
        delete next[key];
      }
      if ("backend" in update) delete next.objectStorageNamespace;
      return next;
    });
    save.reset();
  }

  function submit() {
    if (save.isPending || !query.data) return;
    const validationErrors = validateUploadStorageForm(form, m);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      save.reset();
      return;
    }

    setErrors({});
    save.mutate(payloadFromForm(form), {
      onSuccess: (data) => {
        baseline.current = formFromSettings(data);
        setForm(baseline.current);
        setErrors({});
        toast.success(m.saved);
      },
    });
  }

  if (query.isPending) {
    return (
      <PageBody wide>
        <div
          role="status"
          aria-busy="true"
          className="space-y-4"
          data-testid="settings-upload-storage-loading"
        >
          <p className="text-sm text-fg-muted">{m.loading}</p>
          <Skeleton className="h-64 w-full rounded-lg" />
          <Skeleton className="h-72 w-full rounded-lg" />
        </div>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={errorMessage?.(query.error) ?? m.loadError}
          onRetry={() => void query.refetch()}
          retryLabel={m.retry}
        />
      </PageBody>
    );
  }

  const saveError = (save.error && errorMessage?.(save.error)) || m.saveError;
  const ociSettingsMissing =
    form.backend === "oci" &&
    (!form.objectStorageRegion.trim() || !form.objectStorageNamespace.trim());

  return (
    <PageBody wide>
      <form
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset
          disabled={save.isPending}
          aria-busy={save.isPending}
          className="min-w-0 space-y-5"
        >
          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
                  {form.backend === "oci" ? (
                    <Cloud size={20} aria-hidden />
                  ) : (
                    <HardDrive size={20} aria-hidden />
                  )}
                </div>
                <div>
                  <CardTitle>{m.destinationTitle}</CardTitle>
                  <CardDescription>{m.destinationDescription}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-fg">{m.fieldBackend}</legend>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <BackendOption
                    id="upload-storage-local"
                    value="local"
                    checked={form.backend === "local"}
                    icon={<HardDrive size={20} aria-hidden />}
                    title={m.backendLocal}
                    description={m.backendLocalDescription}
                    onChange={(backend) => updateForm({ backend })}
                  />
                  <BackendOption
                    id="upload-storage-oci"
                    value="oci"
                    checked={form.backend === "oci"}
                    icon={<Cloud size={20} aria-hidden />}
                    title={m.backendOci}
                    description={m.backendOciDescription}
                    onChange={(backend) => updateForm({ backend })}
                  />
                </div>
              </fieldset>

              {form.backend === "local" ? (
                <TextField
                  id="upload-storage-local-dir"
                  label={m.fieldLocalStorageDir}
                  value={form.localStorageDir}
                  onValueChange={(value) => updateForm({ localStorageDir: value })}
                  helper={m.helperLocalStorageDir}
                  placeholder={placeholders?.localStorageDir}
                  error={errors.localStorageDir}
                  required
                  requiredLabel={m.required}
                />
              ) : (
                <div className="space-y-4">
                  {/* リージョン・ネームスペース・バケットは短い値なので、広い画面（2xl）では 1 行に並べる。 */}
                  <div className="grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-2 2xl:grid-cols-3">
                    <SelectField
                      id="upload-storage-object-storage-region"
                      label={m.fieldObjectStorageRegion}
                      value={form.objectStorageRegion}
                      options={regionOptions}
                      onValueChange={(value) => updateForm({ objectStorageRegion: value })}
                      helper={m.helperObjectStorageRegion}
                      placeholder={m.regionPlaceholder}
                      error={errors.objectStorageRegion}
                      required
                      requiredLabel={m.required}
                      buttonClassName="h-11"
                    />
                    <TextField
                      id="upload-storage-object-storage-namespace"
                      label={m.fieldObjectStorageNamespace}
                      value={form.objectStorageNamespace}
                      onValueChange={() => undefined}
                      helper={m.helperObjectStorageNamespace}
                      placeholder="mytenancynamespace"
                      error={errors.objectStorageNamespace}
                      readOnly
                      required
                      requiredLabel={m.required}
                    />
                    <TextField
                      id="upload-storage-bucket"
                      label={m.fieldObjectStorageBucket}
                      value={form.objectStorageBucket}
                      onValueChange={(value) => updateForm({ objectStorageBucket: value })}
                      helper={m.helperObjectStorageBucket}
                      placeholder={placeholders?.objectStorageBucket}
                      error={errors.objectStorageBucket}
                      required
                      requiredLabel={m.required}
                      className="lg:col-span-full 2xl:col-span-1"
                    />
                  </div>
                  {ociSettingsMissing ? (
                    <div className="flex flex-wrap items-center gap-3 rounded-md border border-warning-border bg-warning-subtle p-3">
                      <FormStatus tone="warning" message={m.ociSettingsIncomplete} />
                      <Button
                        type="button"
                        variant="secondary"
                        size="lg"
                        onClick={async () => {
                          if (await confirmLeave()) onOpenOciSettings();
                        }}
                        icon={Settings2}
                      >
                        {m.openOciSettings}
                      </Button>
                    </div>
                  ) : null}
                </div>
              )}
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" loading={save.isPending} icon={Save}>
              {m.save}
            </Button>
            {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
          </div>
        </fieldset>
      </form>
    </PageBody>
  );
}

function BackendOption({
  id,
  value,
  checked,
  icon,
  title,
  description,
  onChange,
}: {
  id: string;
  value: UploadStorageBackend;
  checked: boolean;
  icon: ReactNode;
  title: string;
  description: string;
  onChange: (value: UploadStorageBackend) => void;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex min-h-32 cursor-pointer items-start gap-3 rounded-md border bg-surface p-4 text-left transition-colors",
        checked
          ? "border-accent-emphasis bg-info-subtle"
          : "border-border hover:border-accent-emphasis hover:bg-surface-hover",
      )}
    >
      <input
        id={id}
        type="radio"
        name="upload-storage-backend"
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="mt-1 h-4 w-4 cursor-pointer accent-[var(--color-accent-emphasis)]"
      />
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-surface-sunken text-accent-fg">
        {icon}
      </span>
      <span>
        <span className="block text-sm font-semibold text-fg">{title}</span>
        <span className="mt-1 block text-xs leading-relaxed text-fg-muted">{description}</span>
      </span>
    </label>
  );
}

function formFromSettings(settings: UploadStorageSettingsData): UploadStorageForm {
  return {
    backend: settings.backend,
    localStorageDir: settings.local_storage_dir,
    objectStorageRegion: settings.object_storage_region,
    objectStorageNamespace: settings.object_storage_namespace,
    objectStorageBucket: settings.object_storage_bucket,
  };
}

function payloadFromForm(form: UploadStorageForm): UploadStorageSettingsUpdate {
  const payload: UploadStorageSettingsUpdate = {
    backend: form.backend,
    local_storage_dir: form.localStorageDir,
    object_storage_bucket: form.objectStorageBucket,
  };
  if (form.backend === "oci") {
    payload.object_storage_region = form.objectStorageRegion;
    payload.object_storage_namespace = form.objectStorageNamespace;
  }
  return payload;
}

/** 保存前の検証（backend の pr_system_settings と同じ規則）。 */
export function validateUploadStorageForm(
  form: UploadStorageForm,
  m: UploadStorageMessages = UPLOAD_STORAGE_MESSAGES,
): FieldErrors {
  const errors: FieldErrors = {};
  if (form.backend === "local") {
    if (!form.localStorageDir.trim()) {
      errors.localStorageDir = m.validationLocalStorageDir;
    }
    return errors;
  }

  const region = form.objectStorageRegion.trim();
  const namespace = form.objectStorageNamespace.trim();
  const bucket = form.objectStorageBucket.trim();
  if (!region) {
    errors.objectStorageRegion = m.validationObjectStorageRegion;
  }
  if (!namespace) {
    errors.objectStorageNamespace = m.validationObjectStorageNamespace;
  } else if (!OBJECT_STORAGE_NAME_PATTERN.test(namespace)) {
    errors.objectStorageNamespace = m.validationObjectStorageName;
  }
  if (!bucket) {
    errors.objectStorageBucket = m.validationRequired;
  } else if (!OBJECT_STORAGE_NAME_PATTERN.test(bucket)) {
    errors.objectStorageBucket = m.validationObjectStorageName;
  }
  return errors;
}
