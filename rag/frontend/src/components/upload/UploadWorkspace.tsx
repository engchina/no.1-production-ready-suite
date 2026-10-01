"use client";

import {
  PageBody,
  PageHeader,
  Button,
  Banner,
  buttonVariants,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FieldError,
  FieldLabel,
  Skeleton,
  TimedLoadingState,
} from "@engchina/production-ready-ui";
import {
  AlertTriangle,
  Cloud,
  Database,
  FileText,
  HardDrive,
  List,
  ListChecks,
  RefreshCw,
  RotateCcw,
  Settings,
  Upload,
} from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { Dropzone } from "./Dropzone";
import { UploadSelectionList } from "./UploadSelectionList";
import { UploadSendingState } from "./UploadProgress";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { KnowledgeBaseMultiSelect } from "@/components/knowledge-bases/KnowledgeBaseMultiSelect";
import { useKnowledgeBaseSelectionHealth } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import { useAuth } from "@/components/security/AuthProvider";
import { ErrorState } from "@/components/StateViews";
import {
  type BatchUploadFailedItem,
  type ParserSourceNotice,
  type UploadResult,
  type UploadStorageSettingsData,
} from "@/lib/api";
import {
  uploadErrorMessage,
  useKnowledgeBaseChoices,
  useBatchUploadDocuments,
  useUploadDocument,
  useUploadStorageSettings,
} from "@/lib/queries";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import {
  DEFAULT_MAX_UPLOAD_BYTES,
  totalUploadBytes,
  type UploadProgress,
} from "@/lib/upload-requests";
import { canSubmitUpload, uploadKnowledgeBaseRequired } from "@/lib/upload-scope";
import { mergeUploadSelection } from "@/lib/upload-selection";
import { APP_ROUTES } from "@/lib/routes";
import {
  parserProfileKey,
  sourceModalityKey,
  sourcePreviewKey,
  sourceWarningKey,
} from "@/lib/source-profile-labels";
import { cn } from "@/lib/utils";

/** アップロード → 取込 → RAG 索引化を1画面で進めるワークスペース。 */
export function UploadWorkspace() {
  const [uploaded, setUploaded] = useState<UploadResult | null>(null);
  const [batchItems, setBatchItems] = useState<UploadResult[]>([]);
  const [batchFailedItems, setBatchFailedItems] = useState<BatchUploadFailedItem[]>([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState<string[]>([]);
  // 送る前に選んだファイル（#701）。送り終えるまで残し、失敗したファイルは選び直せる。
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [sentFiles, setSentFiles] = useState<File[]>([]);
  const { user, hasPermission } = useAuth();
  // KB が制限された利用者は、登録先の KB を選ばないとアップロードできない（#214）。
  const knowledgeBaseRequired = uploadKnowledgeBaseRequired(user);
  const [knowledgeBaseMissing, setKnowledgeBaseMissing] = useState(false);
  const [sendingCount, setSendingCount] = useState(0);
  // 送信済み / 合計のバイト数（#306）。送信を始めるたびに 0 から数え直す。
  const [sendProgress, setSendProgress] = useState<UploadProgress | null>(null);
  // 送るファイル（送る順。上限を超えて送らないファイルは除く）。ファイルごとの進み具合に使う（#306）。
  const [sendingFiles, setSendingFiles] = useState<File[]>([]);
  const upload = useUploadDocument();
  const batchUpload = useBatchUploadDocuments();
  // 1 ファイルの上限。送信前の確認と、一括アップロードを分けて送る基準に使う（#280）。
  const maxUploadBytes = useUploadStorageSettings().data?.max_upload_bytes ?? DEFAULT_MAX_UPLOAD_BYTES;
  const isBusy = upload.isPending || batchUpload.isPending;
  const mutationError = upload.error ?? batchUpload.error;

  const reset = () => {
    setSelectedFiles([]);
    setUploaded(null);
    setBatchItems([]);
    setBatchFailedItems([]);
    upload.reset();
    batchUpload.reset();
  };

  const handleKnowledgeBaseChange = (ids: string[]) => {
    setKnowledgeBaseIds(ids);
    if (ids.length > 0) setKnowledgeBaseMissing(false);
  };

  const handleFiles = (files: File[]) => {
    if (files.length === 0) return;
    if (!canSubmitUpload(knowledgeBaseRequired, knowledgeBaseIds)) {
      // 送信前に案内し、選択欄へ移動する（backend の 400 を待たない）。
      setKnowledgeBaseMissing(true);
      // 欄の直下にエラーを出し、選択欄（先頭の入力）へフォーカスを移す（UX 契約 messaging.md §3.2.1。#541）。
      const picker = document.getElementById(UPLOAD_KNOWLEDGE_BASE_PICKER_ID);
      picker?.scrollIntoView({ block: "center" });
      picker?.querySelector<HTMLElement>("input, button")?.focus({ preventScroll: true });
      return;
    }
    setKnowledgeBaseMissing(false);
    setSentFiles(files);
    setUploaded(null);
    setBatchItems([]);
    setBatchFailedItems([]);
    upload.reset();
    batchUpload.reset();
    // 上限を超えて送らないファイルは数えない（hook の最初の通知と同じ合計）。一括アップロードは
    // 選んだ順を保ったまとまりに分けて順に送るため、この順がそのまま送る順になる。
    const sendable = files.filter((file) => file.size <= maxUploadBytes);
    setSendingCount(files.length);
    setSendingFiles(sendable);
    setSendProgress({ sentBytes: 0, totalBytes: totalUploadBytes(sendable) });
    // 上限を超える 1 ファイルは、一括アップロードの経路で送らずに失敗として示す。
    if (files.length === 1 && files[0].size <= maxUploadBytes) {
      upload.mutate(
        { file: files[0], knowledgeBaseIds, onProgress: setSendProgress },
        {
          onSuccess: (result) => {
            setSelectedFiles([]);
            setBatchItems([result]);
            setBatchFailedItems([]);
            setUploaded(result);
          },
        }
      );
      return;
    }
    batchUpload.mutate(
      { files, knowledgeBaseIds, maxUploadBytes, onProgress: setSendProgress },
      {
        onSuccess: (result) => {
          setSelectedFiles([]);
          setBatchItems(result.items);
          setBatchFailedItems(result.failed_items);
          setUploaded(result.items[0] ?? null);
        },
      }
    );
  };

  return (
    <div>
      <PageHeader wide title={t("nav.upload")} subtitle={t("upload.subtitle")} />
      <PageBody wide>
        {!uploaded ? (
          <>
            <UploadStorageNotice
              canOpenSettings={hasPermission(MENU_PERMISSIONS.settingsUploadStorage)}
            />
            <UploadKnowledgeBasePicker
              selectedIds={knowledgeBaseIds}
              onChange={handleKnowledgeBaseChange}
              disabled={isBusy}
              required={knowledgeBaseRequired}
              missing={knowledgeBaseMissing}
              canManageKnowledgeBases={hasPermission(MENU_PERMISSIONS.knowledgeBases)}
            />
            <Dropzone
              onFiles={(files) => setSelectedFiles((current) => mergeUploadSelection(current, files))}
              disabled={isBusy}
              maxUploadBytes={maxUploadBytes}
            />
            <UploadSelectionList
              files={selectedFiles}
              maxUploadBytes={maxUploadBytes}
              busy={isBusy}
              onRemove={(file) => setSelectedFiles((current) => current.filter((item) => item !== file))}
              onClear={() => setSelectedFiles([])}
              onStart={handleFiles}
            />
            {isBusy ? (
              <UploadSendingState
                fileCount={sendingCount}
                files={sendingFiles}
                progress={sendProgress}
              />
            ) : null}
            {mutationError ? <ErrorState message={uploadErrorMessage(mutationError)} /> : null}
            {batchFailedItems.length > 0 ? (
              <BatchUploadFailureList failedItems={batchFailedItems} />
            ) : null}
          </>
        ) : (
          <>
            {/* 1 件だけ保存できて残りが失敗したときも、失敗したファイルを示す（#280）。 */}
            {batchItems.length > 1 || batchFailedItems.length > 0 ? (
              <BatchUploadSummary
                items={batchItems}
                failedItems={batchFailedItems}
                selectedId={uploaded.id}
                onSelect={setUploaded}
              />
            ) : null}
            <UploadParserNotice notice={uploaded.parser_notice} />
            <DocumentWorkspace
              documentId={uploaded.id}
              watchProcessing={uploaded.ingestion_started}
              initialSourceProfile={uploaded.source_profile}
            />
            <div className="flex flex-wrap items-center gap-2">
              {batchFailedItems.length > 0 ? (
                <Button
                  variant="secondary"
                  icon={RotateCcw}
                  onClick={() => {
                    const failedNames = new Set(batchFailedItems.map((item) => item.file_name));
                    const failedFiles = sentFiles.filter((file) => failedNames.has(file.name));
                    reset();
                    setSelectedFiles(failedFiles);
                  }}
                >
                  {t("upload.reselectFailed", { count: batchFailedItems.length })}
                </Button>
              ) : null}
              <Button variant="secondary" icon={Upload} onClick={reset}>
                {t("upload.uploadAnother")}
              </Button>
              <Link to={APP_ROUTES.fileList} className={buttonVariants({ variant: "ghost" })}>
                <List size={16} aria-hidden />
                {t("upload.openFileList")}
              </Link>
            </div>
          </>
        )}
      </PageBody>
    </div>
  );
}

/**
 * 既定の文書解析エンジン（Docling）で扱えない形式の案内（#286）。取込は始めていないので warning で、
 * 対処（処理レシピで Unstructured を選ぶ・サービスを起動する）を backend の文言のまま出す。
 */
function UploadParserNotice({ notice }: { notice: ParserSourceNotice | null | undefined }) {
  if (!notice) return null;
  return (
    <Banner severity="warning" title={t("upload.parserNotice.title")}>
      <p className="text-sm" data-testid="upload-parser-notice">
        {notice.message}
      </p>
    </Banner>
  );
}

function BatchUploadSummary({
  items,
  failedItems,
  selectedId,
  onSelect,
}: {
  items: UploadResult[];
  failedItems: BatchUploadFailedItem[];
  selectedId: string;
  onSelect: (item: UploadResult) => void;
}) {
  // アップロードは取込ジョブを作らない（取込は文書ごとに明示して始める）。
  // そのため「処理待ち」「スキップ」ではなく、保存できた件数と重複の可能性を示す（#280）。
  const duplicateCount = items.filter((item) => item.duplicate_of_document_id).length;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListChecks size={20} className="text-accent-fg" aria-hidden />
          {t("upload.batch.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <BatchMetric label={t("upload.batch.total")} value={items.length + failedItems.length} />
          <BatchMetric label={t("upload.batch.uploaded")} value={items.length} />
          <BatchMetric label={t("upload.batch.duplicates")} value={duplicateCount} />
          <BatchMetric label={t("upload.batch.failed")} value={failedItems.length} />
        </div>
        <div className="bounded-scroll-area divide-y divide-border rounded-md border border-border bg-surface-sunken">
          {items.map((item) => {
            const selected = item.id === selectedId;
            return (
              <div
                key={item.id}
                className={cn(
                  "flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between",
                  selected && "bg-info-subtle"
                )}
              >
                <div className="flex min-w-0 items-start gap-2">
                  <FileText size={16} className="mt-0.5 shrink-0 text-accent-fg" aria-hidden />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-fg" title={item.file_name}>
                      {item.file_name}
                    </p>
                    <p className="mt-1 text-xs text-fg-muted">
                      {t("sourceProfile.parser")}: {t(parserProfileKey(item.source_profile.parser_profile))}
                    </p>
                    {item.parser_notice ? (
                      <p className="mt-1 flex items-center gap-1 text-xs text-warning-fg">
                        <AlertTriangle size={14} className="shrink-0" aria-hidden />
                        {t("upload.parserNotice.short")}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant={selected ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => onSelect(item)}
                    aria-label={t("upload.batch.open", { name: item.file_name })}
                  >
                    {selected ? t("upload.batch.current") : t("upload.batch.openShort")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        {failedItems.length > 0 ? <BatchUploadFailureList failedItems={failedItems} /> : null}
      </CardContent>
    </Card>
  );
}

function BatchUploadFailureList({
  failedItems,
}: {
  failedItems: BatchUploadFailedItem[];
}) {
  return (
    <Banner severity="warning" title={t("upload.batch.failedTitle")}>
      <ul className="bounded-scroll-area space-y-2 pr-1 text-sm">
        {failedItems.map((item, index) => (
          // 同じ名前・同じ理由で失敗したファイルが並んでも key が重ならないよう、位置を含める。
          <li key={`${index}-${item.file_name}`} className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{item.file_name}</span>
              <span className="tnum text-fg-muted">{item.status_code}</span>
              <span>{item.message}</span>
            </div>
            {item.source_profile ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-fg-muted">
                  {t(sourceModalityKey(item.source_profile.modality))}
                </span>
                <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-fg-muted">
                  {t("sourceProfile.parser")}:{" "}
                  {t(parserProfileKey(item.source_profile.parser_profile))}
                </span>
                <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-fg-muted">
                  {t("sourceProfile.previewKind")}:{" "}
                  {t(sourcePreviewKey(item.source_profile.preview_kind))}
                </span>
                {item.source_profile.quality_warnings.slice(0, 2).map((warning) => (
                  <span
                    key={warning}
                    className="rounded-full border border-warning-border bg-warning-subtle px-2 py-0.5 text-warning-fg"
                  >
                    {t(sourceWarningKey(warning))}
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Banner>
  );
}

function BatchMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface-sunken px-3 py-2">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="tnum mt-1 text-lg font-semibold text-fg">{value}</p>
    </div>
  );
}

const UPLOAD_KNOWLEDGE_BASE_PICKER_ID = "upload-knowledge-base-picker";
const UPLOAD_KNOWLEDGE_BASE_INPUT_ID = "upload-knowledge-base-input";
const UPLOAD_KNOWLEDGE_BASE_LABEL_ID = "upload-knowledge-base-label";

function UploadKnowledgeBasePicker({
  selectedIds,
  onChange,
  disabled,
  required,
  missing,
  canManageKnowledgeBases,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled: boolean;
  /** 登録先の KB の選択が必須か（KB が制限された利用者。#214）。 */
  required: boolean;
  /** 必須なのに未選択のままファイルを選んだ。 */
  missing: boolean;
  /** ナレッジベース管理の画面を開けるか（開けない利用者には導線を出さない）。 */
  canManageKnowledgeBases: boolean;
}) {
  // 200 件以下は全件を手元で絞り込み、超えるとサーバー側で検索する（全件を読まない。#578）。
  const [q, setQ] = useState("");
  const query = useKnowledgeBaseChoices({ status: "ACTIVE", q });
  const hasKnowledgeBases = query.total > 0;
  const selection = useKnowledgeBaseSelectionHealth(selectedIds);
  const errorId = useId();

  return (
    <Card id={UPLOAD_KNOWLEDGE_BASE_PICKER_ID} data-testid="upload-knowledge-base-picker">
      <CardHeader>
        <CardTitle>
          {/* 必須は入力欄（combobox）の aria-required で伝える。タグは FieldLabel が読み上げから外す */}
          <FieldLabel
            id={UPLOAD_KNOWLEDGE_BASE_LABEL_ID}
            htmlFor={UPLOAD_KNOWLEDGE_BASE_INPUT_ID}
            label={t("upload.knowledgeBases.title")}
            required={required}
            className="font-semibold"
          />
        </CardTitle>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <TimedLoadingState
            label={t("upload.knowledgeBases.loading")}
            placement="panel"
            testId="upload-knowledge-base-loading"
          >
            <Skeleton className="h-8 w-full" />
          </TimedLoadingState>
        ) : query.isError ? (
          // 取得に失敗したまま（必須の利用者は）送れないため、案内とその場の再読み込みを出す。
          <Banner severity="warning" title={t("upload.knowledgeBases.loadWarning")}>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <p>
                {required
                  ? t("upload.knowledgeBases.loadWarningRequiredHint")
                  : t("upload.knowledgeBases.loadWarningHint")}
              </p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={RefreshCw}
                loading={query.isFetching}
                onClick={() => void query.refetch()}
              >
                {t("upload.knowledgeBases.reload")}
              </Button>
            </div>
          </Banner>
        ) : hasKnowledgeBases ? (
          <KnowledgeBaseMultiSelect
            id={UPLOAD_KNOWLEDGE_BASE_INPUT_ID}
            label={t("upload.knowledgeBases.title")}
            labelHidden
            labelledBy={UPLOAD_KNOWLEDGE_BASE_LABEL_ID}
            required={required}
            invalid={missing}
            describedBy={missing ? errorId : undefined}
            choices={query}
            onQueryChange={setQ}
            selectedIds={selectedIds}
            onChange={onChange}
            selectedItems={selection.items}
            disabled={disabled}
          />
        ) : required ? (
          <p className="rounded-md border border-border bg-surface-sunken p-4 text-sm text-fg-muted">
            {t("upload.knowledgeBases.emptyRestrictedHint")}
          </p>
        ) : (
          <div className="flex flex-col gap-3 rounded-md border border-border bg-surface-sunken p-4 text-sm text-fg-muted sm:flex-row sm:items-center sm:justify-between">
            <span>{t("upload.knowledgeBases.emptyHint")}</span>
            {canManageKnowledgeBases ? (
              <Link
                to={APP_ROUTES.knowledgeBases}
                className={buttonVariants({ variant: "secondary", size: "sm" })}
              >
                <Database size={14} aria-hidden />
                <span>{t("upload.knowledgeBases.manage")}</span>
              </Link>
            ) : null}
          </div>
        )}
        {/* 未選択のエラーを出している間は、同じ内容の案内を重ねない。 */}
        {hasKnowledgeBases && !missing ? (
          <p className="mt-3 text-xs text-fg-muted">
            {selectedIds.length > 0
              ? t("upload.knowledgeBases.selected", { count: selectedIds.length })
              : required
                ? t("upload.knowledgeBases.requiredHint")
                : t("upload.knowledgeBases.defaultHint")}
          </p>
        ) : null}
        {missing ? (
          <FieldError
            id={errorId}
            className="mt-2"
            message={t("upload.knowledgeBases.requiredError")}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

function UploadStorageNotice({ canOpenSettings }: { canOpenSettings: boolean }) {
  const query = useUploadStorageSettings();

  if (query.isPending || query.isError || !query.data) return null;

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-surface px-4 py-3 text-sm text-fg md:flex-row md:items-center md:justify-between">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
          {query.data.backend === "oci" ? (
            <Cloud size={20} aria-hidden />
          ) : (
            <HardDrive size={20} aria-hidden />
          )}
        </div>
        <div>
          <p className="font-medium">
            {t("upload.storageNotice.title")}: {storageBackendLabel(query.data.backend)}
          </p>
          <p className="mt-1 break-all text-xs text-fg-muted">
            {storageTarget(query.data)}
          </p>
        </div>
      </div>
      {/* 保存先の設定画面を開けない利用者（アップロードだけ許可）には導線を出さない。 */}
      {canOpenSettings ? (
        <Link
          to={APP_ROUTES.settingsUploadStorage}
          className={buttonVariants({ variant: "secondary", size: "sm" })}
        >
          <Settings size={14} aria-hidden />
          <span>{t("upload.storageNotice.settings")}</span>
        </Link>
      ) : null}
    </div>
  );
}

function storageBackendLabel(backend: UploadStorageSettingsData["backend"]): string {
  return backend === "oci"
    ? t("settings.uploadStorage.backend.oci")
    : t("settings.uploadStorage.backend.local");
}

function storageTarget(settings: UploadStorageSettingsData): string {
  if (settings.backend === "oci") {
    return settings.object_storage_namespace && settings.object_storage_bucket
      ? `${settings.object_storage_namespace}/${settings.object_storage_bucket}`
      : t("upload.storageNotice.unset");
  }
  return settings.local_storage_dir || t("upload.storageNotice.unset");
}
