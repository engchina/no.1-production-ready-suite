import { WarningsBanner } from "@/components/WarningsBanner";
import { useWorkspaceState, useWorkspaceRevalidation, useWorkspaceActivation } from "@/components/WorkspaceState";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Database,
  Code2,
  FileText,
  RefreshCw,
  Table2,
  Wand2,
} from "lucide-react";

import {
  Button,
  Banner,
  EmptyState,
  toast,
  StatusBadge,
  PageHeader,
  PageBody,
  ContentActionBar,
  ListPicker,
  type ListPickerItem,
  ProcessingIndicator,
  TextareaField,
  TextField,
  useActionPending,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiGet, apiPost, isTimeoutError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useValuesChanged } from "@/lib/render-sync";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import {
  DbManagementLoadingSkeleton,
  DbManagementSelectField,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectSelectorToolbar,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
  type DbObjectTab,
  formatDbObjectName,
  parseDbAdminObjectTarget,
} from "../components/DbObjectManagementShared";
import { DbObjectName } from "../components/DbObjectName";
import { StatementRunnerCard } from "../components/DbAdminShared";
import { buildMetadataInputTexts } from "../metadataSql";
import { useDbAdminObjects, useSchemaRefreshJob } from "../incrementalQueries";
import { dbAdminObjectCountsFromPage } from "../dbAdminObjectCounts";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";

const METADATA_TARGET_LIMIT = 100;
const METADATA_DETAIL_FETCH_BATCH_SIZE = 10;
import type {
  DbAdminObjectDetail,
  DbAdminExecuteData,
  DbAdminObjectSummary,
  DbAdminStatementPolicy,
  DomainInventoryData,
  DomainInventoryPayload,
  DomainOperation,
  MetadataSqlGenerateData,
  MetadataSqlGeneratePayload,
  MetadataSqlSampleData,
  MetadataSqlSamplePayload,
  MetadataSqlTarget,
  SchemaRefreshJob,
} from "../types";

/** ヘッダーの「スキーマを更新」の起点（SchemaRefreshCoordinator の start(origin)。#821）。 */
const METADATA_SCHEMA_REFRESH_HEADER = "metadata-sql-header";

type MetadataMode = "comment" | "annotation" | "domain";
type MetadataPanel = "targets" | "input" | "execute";
type TargetFilter = "all" | "table" | "view";
interface MetadataTargetItem extends MetadataSqlTarget {
  key: string;
  qualifiedName: string;
  owner: string;
  row_count?: number | null;
  comment: string;
}

const ANNOTATION_EXTRA_TEXT =
  "ANNOTATIONSの安全な適用ガイド:\n" +
  "- 語彙は \"DESCRIPTION\"(意味)、\"ALIASES\"(英語・日本語の同義語)、\"VALUES\"(コード値の意味)、\"UNITS\"(単位)、\"JOIN COLUMN\"(結合先)。名前は二重引用符で囲む\n" +
  "- COMMENT: は入力項目名であり、annotation名には使わない。data_type / nullable は生成しない\n" +
  "- VALUESはコメント・サンプルに根拠がある場合だけ付け、推測しない\n" +
  "- DROPとADDは同一文で混在させず、別々のALTER文に分割。既存値の更新はADD OR REPLACE、新規のみはADD IF NOT EXISTS\n" +
  "- DOMAIN=が付いた列はドメインから継承されるため \"JOIN COLUMN\" など表固有の情報だけを付ける\n" +
  "- Select AIで使うには業務プロファイルの「アノテーションを利用」を有効にする\n" +
  "例(表): ALTER TABLE CUST_MST ANNOTATIONS (ADD OR REPLACE \"DESCRIPTION\" 'Customer master. One row represents one customer.', ADD OR REPLACE \"ALIASES\" 'customers, 顧客');\n" +
  "例(列): ALTER TABLE CUST_MST MODIFY (STAT_CD ANNOTATIONS (ADD OR REPLACE \"VALUES\" 'A = active (有効); I = inactive (休眠).'));\n" +
  "例(ビュー列): ALTER VIEW SALES_V MODIFY (AMT ANNOTATIONS (ADD OR REPLACE \"UNITS\" 'Japanese yen (JPY).'));";

const DOMAIN_EXTRA_TEXT =
  "SQLドメインの安全な適用ガイド:\n" +
  "- 1ドメイン = 1業務値(顧客ID、地域、状態、金額など)。複数テーブルで同じ意味の列は同じドメインを共有する\n" +
  "- ドメインの型は列と同じ基本型にし、長さ・精度は列以下にする(STRICTは付けない)。文字型は長さ必須\n" +
  "- 既存データが違反しうるCHECK制約は付けない(サンプルから値集合が明確な場合のみ)\n" +
  "- ANNOTATIONSは \"DESCRIPTION\"(意味)、\"ALIASES\"(英語・日本語の同義語)、\"VALUES\"(コード値の意味)、\"UNITS\"(単位)で付ける。annotation名COMMENTは使わない\n" +
  "- ビュー/MVの列には関連付けない。既にドメインが付いた列は MODIFY (<列>) DROP DOMAIN で外してから付け替える\n" +
  "- 継承されるannotationをSelect AIで使うには業務プロファイルの「アノテーションを利用」を有効にする\n" +
  "例(定義): CREATE DOMAIN IF NOT EXISTS CUSTOMER_ID_D AS NUMBER(10) ANNOTATIONS (\"DESCRIPTION\" 'Unique identifier for a customer.', \"ALIASES\" 'customer id, 顧客ID, 顧客番号');\n" +
  "例(関連付け): ALTER TABLE ORD_TXN MODIFY (CUST_ID) ADD DOMAIN CUSTOMER_ID_D;";

const MODE_CONFIG = {
  comment: {
    pageId: "comment-management",
    policy: "comment_sql",
    generatePath: "/api/nl2sql/comments/generate-sql",
    titleKey: "nav.commentManagement",
    subtitleKey: "metadataSql.comment.subtitle",
    runnerKey: "metadataSql.comment.runner",
    placeholderKey: "metadataSql.comment.placeholder",
    extraText: "",
  },
  annotation: {
    pageId: "annotation-management",
    policy: "annotation_sql",
    generatePath: "/api/nl2sql/annotations/generate-sql",
    titleKey: "nav.annotationManagement",
    subtitleKey: "metadataSql.annotation.subtitle",
    runnerKey: "metadataSql.annotation.runner",
    placeholderKey: "metadataSql.annotation.placeholder",
    extraText: ANNOTATION_EXTRA_TEXT,
  },
  domain: {
    pageId: "domain-management",
    policy: "domain_sql",
    generatePath: "/api/nl2sql/domains/generate-sql",
    titleKey: "nav.domainManagement",
    subtitleKey: "metadataSql.domain.subtitle",
    runnerKey: "metadataSql.domain.runner",
    placeholderKey: "metadataSql.domain.placeholder",
    extraText: DOMAIN_EXTRA_TEXT,
  },
} as const satisfies Record<
  MetadataMode,
  {
    pageId: string;
    policy: DbAdminStatementPolicy;
    generatePath: string;
    titleKey: Parameters<typeof t>[0];
    subtitleKey: Parameters<typeof t>[0];
    runnerKey: Parameters<typeof t>[0];
    placeholderKey: Parameters<typeof t>[0];
    extraText: string;
  }
>;

export function CommentManagementPage() {
  return <MetadataSqlManagementPage mode="comment" />;
}

export function AnnotationManagementPage() {
  return <MetadataSqlManagementPage mode="annotation" />;
}

export function DomainManagementPage() {
  return <MetadataSqlManagementPage mode="domain" />;
}

function schemaRefreshRequiresFull(job: SchemaRefreshJob | null) {
  if (!job) return false;
  return (
    Boolean(job.requires_full_refresh) ||
    job.error_code === "schema_refresh_full_required" ||
    job.error_code === "schema_refresh_target_unresolved"
  );
}

function schemaRefreshRequiredMessage(reasonCode = "") {
  if (reasonCode === "schema_refresh_target_unresolved") {
    return t("dataMgmt.schemaJob.targetUnresolved");
  }
  return t("dataMgmt.schemaJob.fullRequired");
}

function schemaRefreshErrorMessage(job: SchemaRefreshJob) {
  if (schemaRefreshRequiresFull(job)) {
    return schemaRefreshRequiredMessage(job.error_code);
  }
  return job.error_code
    ? `${t("dataMgmt.schemaJob.error")} (${job.error_code})`
    : t("dataMgmt.schemaJob.error");
}

function objectListErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("dataMgmt.objectList.timeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

function objectListLoadMoreErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("objectSelector.loadMoreTimeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

function MetadataSqlManagementPage({ mode }: { mode: MetadataMode }) {
  const { pageId, policy, generatePath } = MODE_CONFIG[mode];
  useWorkspaceRevalidation();
  const [activePanel, setActivePanel] = useWorkspaceState<MetadataPanel>("activePanel", "targets");
  const [selectedKeys, setSelectedKeys] = useWorkspaceState<string[]>("selectedKeys", []);
  const [details, setDetails] = useState<DbAdminObjectDetail[]>([]);
  const [domainInventory, setDomainInventory] = useState<DomainInventoryData | null>(null);
  const [domainOperation, setDomainOperation] = useWorkspaceState<DomainOperation>("domainOperation", "create");
  const [sampleLimit, setSampleLimit] = useWorkspaceState("sampleLimit", 10);
  const [refreshedSampleText, setRefreshedSampleText] = useState<string | null>(null);
  const [extraText, setExtraText] = useWorkspaceState("extraText", MODE_CONFIG[mode].extraText);
  const [generated, setGenerated] = useState<MetadataSqlGenerateData | null>(null);
  const [generationResetSignal, setGenerationResetSignal] = useWorkspaceState("generationResetSignal", 0);
  const [targetSearch, setTargetSearch] = useWorkspaceState("targetSearch", "");
  const [targetOwnerPrefix, setTargetOwnerPrefix] = useWorkspaceState("targetOwnerPrefix", "");
  const [targetFilter, setTargetFilter] = useWorkspaceState<TargetFilter>("targetFilter", "all");
  // 検索語・所有者の接頭辞は SearchField が確定した値（入力が止まって 300ms・Enter・消去。IME の変換中は
  // 確定しない）なので、ここでは遅延させずにそのまま問い合わせに使う（#535）。
  const objectsQuery = useDbAdminObjects(
    targetSearch,
    targetFilter,
    "all",
    targetOwnerPrefix,
    "name_comment"
  );
  const objectItems = useMemo(
    () => (objectsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [objectsQuery.data]
  );
  const firstObjectPage = objectsQuery.data?.pages[0];
  const totalTargetCount = dbAdminObjectCountsFromPage(firstObjectPage, objectItems).totalCount;
  const validationSequence = useRef(0);
  const selectionSignature = JSON.stringify([...selectedKeys].sort());
  const currentSelection = useRef(selectionSignature);
  // 最新の選択を commit 時に入れる（render 中に ref を書かない）。
  useLayoutEffect(() => { currentSelection.current = selectionSignature; });
  // unmount 時に sequence を進める cleanup は置かない。開発モードの StrictMode は mount → 疑似 unmount → 再 mount で
  // effect を二重実行するため、cleanup で sequence が進むと再活性化時の唯一の応答が「古い」と判定されて破棄され、
  // loading が解除されない(#675)。画面は keep-alive で実 unmount はアプリ終了時だけなので不要。
  const [validated, setValidated] = useState(false);
  const [checkedAt, setCheckedAt] = useState("");
  const [loading, setLoading] = useState("");
  // 情報の取得をどこから始めたか。スピナーは押したボタンだけが出し、実行の後・画面へ戻ったときの取り直し
  // （"auto"）は入力の領域の読込表示が出す（#819）。
  const [detailsOrigin, setDetailsOrigin] = useState<"fetch" | "banner" | "auto">("auto");
  // ヘッダーの「表示を更新」を押した取り直しの間だけ true（検索・絞り込み・他の操作の後の取り直しでは回さない。#819）。
  const manualRefresh = useActionPending();
  const [message, setMessage] = useState("");
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  // 通知済みの job（`${job_id}:${status}`）。render 中に比べるため state で持つ。
  const [reportedSchemaRefreshJob, setReportedSchemaRefreshJob] = useState("");
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const headerSchemaRefreshStarting = sharedSchemaRefresh.startingOrigin === METADATA_SCHEMA_REFRESH_HEADER;
  const visibleSchemaRefreshError = schemaRefreshError || sharedSchemaRefresh.error;

  const allTargets = useMemo(
    () => targetItemsFromObjects(objectItems),
    [objectItems]
  );
  const selectedTargets = useMemo(
    () => selectedKeys.map((key) => targetFromKey(key)).filter(Boolean) as MetadataSqlTarget[],
    [selectedKeys]
  );
  const inputTexts = useMemo(
    () => buildMetadataInputTexts(details, sampleLimit),
    [details, sampleLimit]
  );
  const generationSequence = useRef(0);
  const generationSignature = JSON.stringify([
    selectionSignature,
    inputTexts,
    sampleLimit,
    extraText,
    mode === "domain" ? [domainOperation, domainInventory?.domain_text ?? ""] : null,
  ]);
  const currentGenerationSignature = useRef(generationSignature);
  // 最新の生成条件を commit 時に入れる（render 中に ref を書かない）。
  useLayoutEffect(() => { currentGenerationSignature.current = generationSignature; });
  // 生成条件が変わったら、生成中の表示を render 中に解除し、実行中の生成の応答は effect で捨てる。
  if (useValuesChanged([generationSignature]) && loading === "generate") setLoading("");
  useEffect(() => {
    generationSequence.current += 1;
  }, [generationSignature]);
  const panels = useMemo(
    () =>
      [
        { id: "targets", label: t("metadataSql.tabs.targets"), icon: Table2 },
        { id: "input", label: t("metadataSql.tabs.input"), icon: FileText },
        { id: "execute", label: t("metadataSql.tabs.execute"), icon: Code2 },
      ] satisfies Array<DbObjectTab<MetadataPanel>>,
    []
  );
  const activePanelIndex = Math.max(
    0,
    panels.findIndex((panel) => panel.id === activePanel)
  );
  const renderStepIndicator = (panel: MetadataPanel) =>
    activePanel === panel ? (
      <DbObjectStepIndicator
        steps={panels.map((item) => item.label)}
        activeIndex={activePanelIndex}
        ariaLabel={t("metadataSql.steps.label")}
        dataTestId={`${pageId}-steps`}
      />
    ) : null;

  const filteredTargets = useMemo(() => {
    const q = targetSearch.trim().toLowerCase();
    const ownerPrefixKey = targetOwnerPrefix.trim().toUpperCase();
    return allTargets
      .filter((item) => {
        if (ownerPrefixKey && !item.owner.toUpperCase().startsWith(ownerPrefixKey)) return false;
        if (targetFilter === "view" && !isViewLikeTarget(item.object_type)) return false;
        if (targetFilter === "table" && item.object_type !== "table") return false;
        if (!q) return true;
        return (
          item.object_name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q)
        );
      })
      // 候補の一覧（ListPicker）は名前の順に並べる（列の並べ替えは持たない。#608）。
      .sort((left, right) => left.qualifiedName.localeCompare(right.qualifiedName));
  }, [allTargets, targetFilter, targetOwnerPrefix, targetSearch]);

  // 応答の反映は then の callback で行う（effect から呼んでも同期の setState にしない）。
  // refetch は TanStack Query の observer に束縛された安定した参照なので、reloadObjects も作り直されない。
  const { refetch: refetchObjects } = objectsQuery;
  const reloadObjects = useCallback((announce = false) =>
    refetchObjects().then((result) => {
      if (result.error) {
        setMessage(result.error instanceof Error ? result.error.message : t("metadataSql.error.load"));
        return;
      }
      if (announce) {
        toast.success(t("common.action.refreshed"));
      }
    }), [refetchObjects]);
  const refreshObjects = async (announce = false) => {
    setMessage("");
    await reloadObjects(announce);
  };

  // origin: 押したボタン（ヘッダー）。案内の「スキーマを更新」などは起点を持たず、送信の間は進行の表示が
  // スピナーを出す（#821）。
  const refreshSchema = async (origin = "") => {
    setLoading("schema-refresh");
    setMessage("");
    setSchemaRefreshError("");
    setSchemaRefreshNeedsFull(false);
    try {
      const job = await sharedSchemaRefresh.start(origin);
      if (job.job_id) {
        setReportedSchemaRefreshJob("");
        setSchemaRefreshJobId(job.job_id);
      }
    } catch (err) {
      setMessage(
        err instanceof Error
          ? err.message
          : t("dataMgmt.schemaJob.submitError")
      );
    } finally {
      setLoading("");
    }
  };

  // job の完了・失敗を render 中に一度だけ state へ反映し、完了時の再読込は effect で行う。
  const schemaRefreshJob = schemaRefreshJobQuery.data;
  if (useValuesChanged([schemaRefreshJob]) && schemaRefreshJob) {
    const reportKey = `${schemaRefreshJob.job_id}:${schemaRefreshJob.status}`;
    if (reportedSchemaRefreshJob !== reportKey) {
      if (schemaRefreshJob.status === "done") {
        setReportedSchemaRefreshJob(reportKey);
        setSchemaRefreshError("");
        setSchemaRefreshNeedsFull(false);
        setMessage("");
      } else if (schemaRefreshJob.status === "error") {
        setReportedSchemaRefreshJob(reportKey);
        const needsFull = schemaRefreshRequiresFull(schemaRefreshJob);
        setSchemaRefreshNeedsFull(needsFull);
        setSchemaRefreshError(schemaRefreshErrorMessage(schemaRefreshJob));
      }
    }
  }
  // reloadObjects は安定した参照なので、完了を記録した job（reportedSchemaRefreshJob）が変わったときだけ動く。
  useEffect(() => {
    if (reportedSchemaRefreshJob.endsWith(":done")) void reloadObjects();
  }, [reloadObjects, reportedSchemaRefreshJob]);

  const reloadAfterMutation = (result: DbAdminExecuteData) => {
    if (result.schema_refresh_job_id) {
      sharedSchemaRefresh.track(result.schema_refresh_job_id);
      setReportedSchemaRefreshJob("");
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      setSchemaRefreshJobId(result.schema_refresh_job_id);
    } else if (result.schema_refresh_required) {
      setSchemaRefreshError(schemaRefreshRequiredMessage(result.schema_refresh_reason_code));
      setSchemaRefreshNeedsFull(true);
    }
    void refreshObjects();
    // 実行で列のコメント・annotation・ドメインが変わるため、構造と既存ドメインを取り直す。
    // 古いままだと続けて更新/削除を生成したときに実行前の関連付けを材料にしてしまう。
    if (selectedTargets.length > 0) void fetchDetails(true, "auto");
  };

  const toggleTarget = (target: MetadataSqlTarget) => {
    const key = targetKey(target);
    if (!selectedKeys.includes(key) && selectedKeys.length >= METADATA_TARGET_LIMIT) {
      setMessage(t("metadataSql.error.targetLimit", { limit: METADATA_TARGET_LIMIT }));
      return;
    }
    setMessage("");
    setSelectedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
    setValidated(false);
    setDetails([]);
    setDomainInventory(null);
    setRefreshedSampleText(null);
    setGenerated(null);
  };

  const bulkSelectTargets = (targets: MetadataSqlTarget[], selected: boolean) => {
    const targetKeys = targets.map((target) => targetKey(target));
    const targetKeySet = new Set(targetKeys);
    if (selected) {
      const currentSet = new Set(selectedKeys);
      const additions = targetKeys.filter((key) => !currentSet.has(key));
      const available = Math.max(0, METADATA_TARGET_LIMIT - selectedKeys.length);
      const limitedAdditions = additions.slice(0, available);
      setSelectedKeys([...selectedKeys, ...limitedAdditions]);
      setMessage(
        additions.length > available
          ? t("metadataSql.error.targetLimit", { limit: METADATA_TARGET_LIMIT })
          : ""
      );
    } else {
      setSelectedKeys(selectedKeys.filter((key) => !targetKeySet.has(key)));
      setMessage("");
    }
    setValidated(false);
    setDetails([]);
    setDomainInventory(null);
    setRefreshedSampleText(null);
    setGenerated(null);
  };

  const clearTargets = () => {
    setSelectedKeys([]);
    setMessage("");
    setValidated(false);
    setDetails([]);
    setDomainInventory(null);
    setRefreshedSampleText(null);
    setGenerated(null);
  };

  const fetchDetails = async (
    preserveWork = false,
    origin: "fetch" | "banner" | "auto" = "fetch"
  ) => {
    generationSequence.current += 1;
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    if (selectedTargets.length > METADATA_TARGET_LIMIT) {
      setMessage(t("metadataSql.error.targetLimit", { limit: METADATA_TARGET_LIMIT }));
      return;
    }
    const sequence = ++validationSequence.current;
    const validatingSelection = selectionSignature;
    if (!preserveWork) setActivePanel("input");
    setValidated(false);
    setLoading("details");
    setDetailsOrigin(origin);
    setMessage("");
    try {
      const nextDetails: DbAdminObjectDetail[] = [];
      for (let index = 0; index < selectedTargets.length; index += METADATA_DETAIL_FETCH_BATCH_SIZE) {
        const batch = selectedTargets.slice(index, index + METADATA_DETAIL_FETCH_BATCH_SIZE);
        const batchDetails = await Promise.all(batch.map((target) => {
          const params = new URLSearchParams();
          if (target.owner) params.set("owner", target.owner);
          const suffix = params.toString() ? `?${params.toString()}` : "";
          return apiGet<DbAdminObjectDetail>(
            isViewLikeTarget(target.object_type)
              ? `/api/nl2sql/db-admin/views/${encodeURIComponent(target.object_name)}${suffix}`
              : `/api/nl2sql/db-admin/tables/${encodeURIComponent(target.object_name)}${suffix}`
          );
        }));
        nextDetails.push(...batchDetails);
      }
      let nextInventory: DomainInventoryData | null = null;
      if (mode === "domain") {
        // 既存ドメイン(定義と関連付け先)は更新/再作成/削除の判断材料。取得失敗は warning として残し、作成は続行できる。
        const inventoryPayload: DomainInventoryPayload = {
          targets: selectedTargets.filter((target) => target.object_type === "table"),
        };
        try {
          nextInventory = await apiPost<DomainInventoryData>("/api/nl2sql/domains/inventory", inventoryPayload);
        } catch (err) {
          nextInventory = {
            domains: [],
            domain_text: "",
            runtime: "",
            warnings: [err instanceof Error ? err.message : t("metadataSql.error.details")],
          };
        }
      }
      if (sequence !== validationSequence.current || validatingSelection !== currentSelection.current) return;
      setDetails(nextDetails);
      setDomainInventory(nextInventory);
      setValidated(true);
      setCheckedAt(new Date().toISOString());
      if (!preserveWork) { setRefreshedSampleText(null); setGenerated(null); }
      if (!preserveWork) toast.success(t("metadataSql.toast.detailsLoaded", { count: nextDetails.length }));
    } catch (err) {
      if (sequence !== validationSequence.current || validatingSelection !== currentSelection.current) return;
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.details"));
    } finally {
      if (sequence === validationSequence.current) setLoading("");
    }
  };

  useWorkspaceActivation(() => {
    if (selectedTargets.length > 0) void fetchDetails(true, "auto");
  });

  const generateSql = async () => {
    if (loading || !validated) return;
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    if (selectedTargets.length > METADATA_TARGET_LIMIT || details.length > METADATA_TARGET_LIMIT) {
      setMessage(t("metadataSql.error.targetLimit", { limit: METADATA_TARGET_LIMIT }));
      return;
    }
    const sequence = ++generationSequence.current;
    const submittedSignature = generationSignature;
    const isCurrent = () => sequence === generationSequence.current &&
      submittedSignature === currentGenerationSignature.current;
    setActivePanel("execute");
    setLoading("generate");
    setMessage("");
    try {
      const samplePayload: MetadataSqlSamplePayload = {
        targets: details.map((detail) => ({
          owner: detail.owner,
          object_name: detail.name,
          object_type: normalizeMetadataTargetType(detail.object_type),
          columns: detail.columns.map((column) => column.column_name),
        })),
        sample_limit: sampleLimit,
      };
      const samples = await apiPost<MetadataSqlSampleData>("/api/nl2sql/metadata-samples", samplePayload);
      if (!isCurrent()) return;
      setRefreshedSampleText(samples.sample_text);
      const payload: MetadataSqlGeneratePayload = {
        targets: selectedTargets,
        structure_text: inputTexts.structureText,
        primary_key_text: inputTexts.primaryKeyText,
        foreign_key_text: inputTexts.foreignKeyText,
        sample_text: samples.sample_text,
        extra_text: extraText,
        ...(mode === "domain"
          ? {
              operation: domainOperation,
              domain_text: domainInventory?.domain_text ?? "",
              domains: domainInventory?.domains ?? [],
            }
          : {}),
      };
      const generatedSql = await apiPost<MetadataSqlGenerateData>(generatePath, payload);
      if (!isCurrent()) return;
      setGenerated({
        ...generatedSql,
        warnings: [...(domainInventory?.warnings ?? []), ...samples.warnings, ...generatedSql.warnings],
      });
      setGenerationResetSignal((value) => value + 1);
      toast.success(t("metadataSql.toast.generated"));
    } catch (err) {
      if (isCurrent()) setMessage(err instanceof Error ? err.message : t("metadataSql.error.generate"));
    } finally {
      if (sequence === generationSequence.current) setLoading("");
    }
  };

  return (
    <>
      <PageHeader wide
        title={t(MODE_CONFIG[mode].titleKey)}
        subtitle={t(MODE_CONFIG[mode].subtitleKey)}
        meta={
          firstObjectPage?.refreshed_at
            ? t("common.schemaRefreshedAt", {
                date: formatDateTime(firstObjectPage.refreshed_at),
              })
            : undefined
        }
        status={<SchemaRefreshHeaderStatus testId={`${pageId}-schema-refresh-status`} />}
        actions={[
            {
              id: "refresh",
              kind: "utility",
              label: t("common.action.refresh"),
              icon: RefreshCw,
              onClick: () => void manualRefresh.track(() => refreshObjects(true)),
              // 初回の読込は対象の一覧の Skeleton がスピナーを出す（このボタンは狭い画面では「その他の操作」の中で
              // 見えない）。ボタンは押した再読込の間だけ回す（同じ処理のスピナーは 1 つ。messaging §3.7、#416）。
              // 検索・絞り込み・他の操作の後の取り直しでは回さず、一覧の処理中の表示がスピナーを出す（#819）。
              loading: manualRefresh.pending,
              disabled: !objectsQuery.data && objectsQuery.isFetching,
            },
          {
            id: "schema-refresh",
            kind: "utility",
            label: t("common.action.schemaRefresh"),
            icon: RefreshCw,
            onClick: () => void refreshSchema(METADATA_SCHEMA_REFRESH_HEADER),
            // 押したときの送信の間だけ回し、job の間は無効にするだけ（スピナーは進行の表示。#821）。
            loading: headerSchemaRefreshStarting,
            disabled: schemaRefreshing && !headerSchemaRefreshStarting,
          },
        ]}
      />
      {selectedTargets.length > 0 && (checkedAt || activePanel !== "targets") ? (
        <PageBody wide className="pb-0">
          <Banner severity={validated ? "info" : "warning"} action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              icon={RefreshCw}
              // 押した「再取得」の間だけ回す。「情報を取得」・実行の後の取り直しでは無効にするだけ（#819）。
              loading={loading === "details" && detailsOrigin === "banner"}
              disabled={Boolean(loading) && !(loading === "details" && detailsOrigin === "banner")}
              onClick={() => void fetchDetails(true, "banner")}
            >
              {t("workspace.refresh")}
            </Button>
          }>
            {t(checkedAt ? "workspace.snapshot" : "workspace.unverified")}{checkedAt ? ` (${formatDateTime(checkedAt)})` : ""}
          </Banner>
        </PageBody>
      ) : null}
      <PageBody wide className="grid gap-4">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("metadataSql.error.retryHint")}` }
              : visibleSchemaRefreshError
                ? { tone: "danger", message: visibleSchemaRefreshError }
                : null
          }
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={
                schemaRefreshNeedsFull || Boolean(sharedSchemaRefresh.error)
                  ? () => void refreshSchema()
                  : () => void refreshObjects()
              } icon={RefreshCw}>
              <span>
                {schemaRefreshNeedsFull || Boolean(sharedSchemaRefresh.error)
                  ? t("common.action.schemaRefresh")
                  : t("tableMgmt.action.refresh")}
              </span>
            </Button>
          }
        />
        {schemaRefreshing ? (
          <SchemaRefreshProcessing testId={`${pageId}-workspace-processing`} />
        ) : objectsQuery.isFetching && !objectsQuery.isFetchingNextPage && Boolean(objectsQuery.data) ? (
          <ProcessingIndicator
            active
            label={t("common.processing.refreshing")}
            operationKey="metadata-objects-refresh"
            placement="workspace"
            className="rounded-md border border-border bg-surface px-3 py-2 shadow-sm"
            testId={`${pageId}-workspace-processing`}
            activityIcon={manualRefresh.pending ? "none" : "spinner"}
          />
        ) : null}

        <DbObjectManagementTabs
          activeView={activePanel}
          tabs={panels}
          idPrefix={pageId}
          ariaLabel={t("metadataSql.tabs.label")}
          onViewChange={setActivePanel}
        />

        <DbObjectManagementPanelShell
          id={`${pageId}-panel-targets`}
          labelledBy={`${pageId}-tab-targets`}
          idPrefix={pageId}
          ariaLabel={t("metadataSql.workspace.targets")}
          className={activePanel === "targets" ? "" : "hidden"}
          topContent={renderStepIndicator("targets")}
        >
          <MetadataTargetGrid
            pageId={pageId}
            items={filteredTargets}
            totalCount={totalTargetCount}
            selectedKeys={selectedKeys}
            loading={objectsQuery.isPending && !objectsQuery.data}
            refreshing={objectsQuery.isFetching && !objectsQuery.isFetchingNextPage && Boolean(objectsQuery.data)}
            error={
              objectsQuery.error && !objectsQuery.data
                ? objectListErrorMessage(objectsQuery.error, "metadataSql.error.load")
                : ""
            }
            search={targetSearch}
            ownerPrefix={targetOwnerPrefix}
            filter={targetFilter}
            hasNextPage={Boolean(objectsQuery.hasNextPage)}
            loadingNextPage={objectsQuery.isFetchingNextPage}
            loadMoreError={
              objectsQuery.isFetchNextPageError && objectsQuery.error
                ? objectListLoadMoreErrorMessage(objectsQuery.error, "metadataSql.error.load")
                : ""
            }
            onSearchChange={setTargetSearch}
            onOwnerPrefixChange={setTargetOwnerPrefix}
            onFilterChange={setTargetFilter}
            onToggle={toggleTarget}
            onBulkSelect={bulkSelectTargets}
            onClearSelection={clearTargets}
            onRetry={() => void refreshObjects()}
            onLoadMore={() => void objectsQuery.fetchNextPage()}
            onRetryLoadMore={() => void objectsQuery.fetchNextPage()}
            onFetchDetails={() => void fetchDetails(false, "fetch")}
            fetchingDetails={loading === "details" && detailsOrigin === "fetch"}
          />
        </DbObjectManagementPanelShell>

        <DbObjectManagementPanelShell
          id={`${pageId}-panel-input`}
          labelledBy={`${pageId}-tab-input`}
          idPrefix={pageId}
          ariaLabel={t("metadataSql.workspace.input")}
          className={activePanel === "input" ? "" : "hidden"}
          topContent={renderStepIndicator("input")}
        >
          <MetadataInputPanel
            pageId={pageId}
            inputTexts={inputTexts}
            detailsReady={validated && details.length > 0}
            detailsLoading={loading === "details"}
            // 押したボタン（「情報を取得」「最新情報を取得」）が回るときは、領域はスピナーを出さない（同じ処理の
            // スピナーは 1 つ。SQL 生成と同じ）。実行の後・画面へ戻ったときの取り直しは領域が出す（#819）。
            detailsButtonSpinning={detailsOrigin !== "auto"}
            selectedCount={selectedTargets.length}
            sampleLimit={sampleLimit}
            sampleText={refreshedSampleText ?? inputTexts.sampleText}
            extraText={extraText}
            loading={loading === "generate"}
            domain={
              mode === "domain"
                ? {
                    operation: domainOperation,
                    inventory: domainInventory,
                    onOperationChange: setDomainOperation,
                  }
                : null
            }
            onSampleLimitChange={(value) => {
              setSampleLimit(value);
              setRefreshedSampleText(null);
            }}
            onExtraTextChange={setExtraText}
            onGenerate={() => void generateSql()}
          />
        </DbObjectManagementPanelShell>

        <DbObjectManagementPanelShell
          id={`${pageId}-panel-execute`}
          labelledBy={`${pageId}-tab-execute`}
          idPrefix={pageId}
          ariaLabel={t("metadataSql.workspace.execute")}
          className={activePanel === "execute" ? "" : "hidden"}
          topContent={renderStepIndicator("execute")}
        >
          <MetadataExecutePanel
            pageId={pageId}
            mode={mode}
            generated={generated}
            executionBlocked={selectedTargets.length > 0 && !validated}
            draftScope={selectionSignature}
            loading={loading === "generate"}
            policy={policy}
            resetSignal={generationResetSignal}
            onExecuted={reloadAfterMutation}
          />
        </DbObjectManagementPanelShell>
      </PageBody>
    </>
  );
}

/**
 * 対象の表・ビューの選択（#608）。大量の候補から複数を選ぶ共通の `ListPicker` に、読み込んだ候補（カーソルの追加読み込み）を
 * 渡す。検索・所有者・種類の絞り込みは DB オブジェクトの一覧で共通のツールバー（`DbObjectSelectorToolbar`）が持ち、
 * 行の描画・仮想スクロール・キーボード操作・「選択中だけ表示」・追加読み込みのフッターは ListPicker が持つ
 * （業務プロファイルの「許可する表・ビュー」と同じ型。#600）。
 */
function MetadataTargetGrid({
  pageId,
  items,
  totalCount,
  selectedKeys,
  loading,
  refreshing,
  error,
  search,
  ownerPrefix,
  filter,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  fetchingDetails,
  onSearchChange,
  onOwnerPrefixChange,
  onFilterChange,
  onToggle,
  onBulkSelect,
  onClearSelection,
  onRetry,
  onLoadMore,
  onRetryLoadMore,
  onFetchDetails,
}: {
  pageId: string;
  items: MetadataTargetItem[];
  totalCount: number;
  selectedKeys: string[];
  loading: boolean;
  refreshing: boolean;
  error: string;
  search: string;
  ownerPrefix: string;
  filter: TargetFilter;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  fetchingDetails: boolean;
  onSearchChange: (value: string) => void;
  onOwnerPrefixChange: (value: string) => void;
  onFilterChange: (value: TargetFilter) => void;
  onToggle: (target: MetadataSqlTarget) => void;
  onBulkSelect: (targets: MetadataSqlTarget[], selected: boolean) => void;
  onClearSelection: () => void;
  onRetry: () => void;
  onLoadMore: () => void;
  onRetryLoadMore: () => void;
  onFetchDetails: () => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || Boolean(ownerPrefix.trim()) || filter !== "all";
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const pickerItems = useMemo(() => items.map(targetPickerItem), [items]);
  // 読み込んだ範囲の外にある選択（検索語を変えた後など）も「選択中だけ表示」で確かめられるよう、key から作る。
  const selectedPickerItems = useMemo(() => {
    const loaded = new Map(items.map((item) => [item.key, item]));
    return selectedKeys.flatMap((key) => {
      const item = loaded.get(key);
      if (item) return [targetPickerItem(item)];
      const target = targetFromKey(key);
      return target ? [targetPickerItem(targetItemFromTarget(target))] : [];
    });
  }, [items, selectedKeys]);
  const targetOf = (key: string) => items.find((item) => item.key === key) ?? targetFromKey(key);

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby={`${pageId}-targets-heading`}>
      <DbObjectPanelHeader
        headingId={`${pageId}-targets-heading`}
        icon={Table2}
        title={t("metadataSql.targets.title")}
        description={t("metadataSql.targets.hint")}
        action={
          <>
            <StatusBadge
              icon={false}
              variant="neutral"
              label={t("command.count", { count: totalCount })}
            />
            <StatusBadge icon={false} variant="info" label={t("metadataSql.targets.selected", { count: selectedKeys.length })} />
          </>
        }
      />

      <DbObjectSelectorToolbar
        searchLabel={t("dbAdmin.search.label")}
        searchPlaceholder={t("dbAdmin.search.placeholder")}
        searchValue={search}
        onSearchChange={onSearchChange}
        dataTestId={`${pageId}-target-toolbar`}
        ownerPrefixField={{
          label: t("dbAdmin.owner.label"),
          placeholder: t("dbAdmin.ownerPrefix.placeholder"),
          value: ownerPrefix,
          onChange: onOwnerPrefixChange,
        }}
      >
        <DbManagementSelectField
          label={t("metadataSql.targets.typeFilter")}
          value={filter}
          options={[
            { value: "all", label: t("metadataSql.targets.typeFilterAll") },
            { value: "table", label: t("metadataSql.targets.typeFilterTables") },
            { value: "view", label: t("metadataSql.targets.typeFilterViews") },
          ]}
          width="sm"
          onChange={onFilterChange}
        />
      </DbObjectSelectorToolbar>

      <ListPicker
        id={`${pageId}-target-picker`}
        label={t("metadataSql.targets.title")}
        items={pickerItems}
        selectedKeys={selectedSet}
        selectedItems={selectedPickerItems}
        onToggle={(item) => {
          const target = targetOf(item.key);
          if (target) onToggle(target);
        }}
        onSelectMany={(visible) =>
          onBulkSelect(
            visible.flatMap((item) => {
              const target = targetOf(item.key);
              return target ? [target] : [];
            }),
            true
          )
        }
        onClearSelection={onClearSelection}
        total={totalCount}
        hasActiveFilter={hasActiveFilter}
        loading={loading}
        refreshing={refreshing}
        error={error || undefined}
        onRetry={onRetry}
        hasMore={hasNextPage}
        loadingMore={loadingNextPage}
        loadMoreError={loadMoreError || undefined}
        onLoadMore={loadMoreError ? onRetryLoadMore : onLoadMore}
        fixedHeight
        labels={{
          resultCount: ({ visible, total, selected }) =>
            t("objectSelector.resultCountWithSelected", { visible, total, selected }),
          loading: t("metadataSql.targets.loading"),
          loadMore: t("objectSelector.loadMore"),
          retry: t("common.retry"),
          emptyTitle: t("metadataSql.targets.emptyTitle"),
          emptyHint: t("metadataSql.targets.emptyHint"),
          noResultsTitle: t("metadataSql.targets.noResultsTitle"),
          noResultsHint: t("metadataSql.targets.noResultsHint"),
          clearSearch: t("common.clearSearch"),
          keyboardHint: t("objectSelector.keyboardHint"),
        }}
        testId={`${pageId}-target`}
      />
      {!loading && (
        <ContentActionBar
          ariaLabel={t("metadataSql.targets.actions")}
          title={t("metadataSql.targets.fetchActionTitle")}
          description={
            selectedKeys.length > 0
              ? t("metadataSql.targets.fetchActionReady", { count: selectedKeys.length })
              : t("metadataSql.targets.fetchActionDisabled")
          }
          actionsClassName="w-full sm:w-auto"
          testId={`${pageId}-target-actions`}
        >
          <Button icon={Database}
            type="button"
            variant="primary"
            size="lg"
            className="w-full sm:w-auto"
            loading={fetchingDetails}
            disabled={selectedKeys.length === 0}
            onClick={onFetchDetails}
          >
            <span>{t("metadataSql.action.fetchInfo")}</span>
          </Button>
        </ContentActionBar>
      )}
    </section>
  );
}

/** 候補の 1 行（名前は SQL と同じ表記、下にコメント、右端に種類）。 */
function targetPickerItem(item: MetadataTargetItem): ListPickerItem {
  return {
    key: item.key,
    label: <DbObjectName value={item.qualifiedName} size="xs" truncate className="block" />,
    textValue: item.qualifiedName,
    description: item.comment || undefined,
    meta: <StatusBadge icon={false} variant="neutral" label={targetTypeLabel(item.object_type)} />,
  };
}

/** 読み込んだ範囲の外にある選択（key だけ）を候補の形にする（コメントは読んでいないので出さない）。 */
function targetItemFromTarget(target: MetadataSqlTarget): MetadataTargetItem {
  return {
    ...target,
    key: targetKey(target),
    qualifiedName: parseDbAdminObjectTarget(target.object_name, target.owner).qualifiedName,
    owner: target.owner ?? "",
    comment: "",
  };
}

function MetadataInputPanel({
  pageId,
  inputTexts,
  detailsReady,
  detailsLoading,
  detailsButtonSpinning,
  selectedCount,
  sampleLimit,
  sampleText,
  extraText,
  loading,
  domain,
  onSampleLimitChange,
  onExtraTextChange,
  onGenerate,
}: {
  pageId: string;
  inputTexts: ReturnType<typeof buildMetadataInputTexts>;
  detailsReady: boolean;
  detailsLoading: boolean;
  /** 情報の取得を始めたボタン（「情報を取得」「最新情報を取得」）が回っているか。 */
  detailsButtonSpinning: boolean;
  selectedCount: number;
  sampleLimit: number;
  sampleText: string;
  extraText: string;
  loading: boolean;
  /** ドメイン管理だけ: 操作種別と既存ドメイン。 */
  domain: {
    operation: DomainOperation;
    inventory: DomainInventoryData | null;
    onOperationChange: (value: DomainOperation) => void;
  } | null;
  onSampleLimitChange: (value: number) => void;
  onExtraTextChange: (value: string) => void;
  onGenerate: () => void;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={FileText}
        title={t("metadataSql.input.title")}
        description={t("metadataSql.input.hint")}
      />

      {detailsLoading ? (
        <DbManagementLoadingSkeleton
          idPrefix={`${pageId}-input`}
          ariaLabel={t("metadataSql.input.loading")}
          variant="detail"
          placement="result"
          // 実行の後・画面へ戻ったときの取り直しはボタンが回らないため、この表示がスピナーを出す（#819）。
          activityIcon={detailsButtonSpinning ? "none" : "spinner"}
        />
      ) : (
        <>
          {!detailsReady && (
            <EmptyState title={t("metadataSql.input.emptyTitle")} hint={t("metadataSql.input.emptyHint")} />
          )}

          <div className="grid gap-3 rounded-md border border-border bg-surface-sunken p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              {domain ? (
                <DbManagementSelectField
                  label={t("metadataSql.domain.operation")}
                  value={domain.operation}
                  options={[
                    { value: "create", label: t("metadataSql.domain.operation.create") },
                    { value: "update", label: t("metadataSql.domain.operation.update") },
                    { value: "rebuild", label: t("metadataSql.domain.operation.rebuild") },
                    { value: "delete", label: t("metadataSql.domain.operation.delete") },
                  ]}
                  width="md"
                  onChange={domain.onOperationChange}
                />
              ) : null}
              <TextField
                id={`${pageId}-sample-limit`}
                label={t("metadataSql.input.sampleLimit")}
                type="number"
                min={0}
                max={100}
                value={sampleLimit}
                onChange={(event) => {
                  const value = Number(event.currentTarget.value);
                  onSampleLimitChange(Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0);
                }}
                width="xs"
                className="min-w-0"
              />
              <StatusBadge icon={false} variant={detailsReady ? "info" : "neutral"} label={t("metadataSql.targets.selected", { count: selectedCount })} />
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <MetadataTextarea label={t("metadataSql.input.structure")} value={inputTexts.structureText} rows={8} />
            <MetadataTextarea label={t("metadataSql.input.sample")} value={sampleText} rows={8} />
            <MetadataTextarea label={t("metadataSql.input.pk")} value={inputTexts.primaryKeyText} rows={5} />
            <MetadataTextarea label={t("metadataSql.input.fk")} value={inputTexts.foreignKeyText} rows={5} />
            {domain ? (
              <MetadataTextarea
                label={t("metadataSql.input.domains")}
                value={domain.inventory?.domain_text || (detailsReady ? t("metadataSql.input.domainsEmpty") : "")}
                rows={8}
              />
            ) : null}
          </div>
          <WarningsBanner warnings={domain?.inventory?.warnings} />

          <TextareaField
            id={`${pageId}-extra-text`}
            label={t("metadataSql.input.extra")}
            value={extraText}
            onChange={(event) => onExtraTextChange(event.currentTarget.value)}
            rows={6}
          />

          <ContentActionBar
            ariaLabel={t("metadataSql.input.actions")}
            title={t("metadataSql.input.generateActionTitle")}
            description={
              detailsReady
                ? t("metadataSql.input.generateActionReady", { count: selectedCount })
                : t("metadataSql.input.generateActionDisabled")
            }
            actionsClassName="w-full sm:w-auto"
            testId={`${pageId}-input-actions`}
          >
            <Button
              type="button"
              variant="primary"
              size="lg"
              className="w-full sm:w-auto"
              loading={loading}
              disabled={!detailsReady}
              onClick={onGenerate} icon={Wand2}>
              <span>{t("metadataSql.action.generate")}</span>
            </Button>
          </ContentActionBar>
        </>
      )}
    </div>
  );
}

function MetadataExecutePanel({
  pageId,
  mode,
  generated,
  executionBlocked,
  draftScope,
  loading,
  policy,
  resetSignal,
  onExecuted,
}: {
  pageId: string;
  mode: MetadataMode;
  generated: MetadataSqlGenerateData | null;
  loading: boolean;
  policy: DbAdminStatementPolicy;
  resetSignal: number;
  executionBlocked: boolean;
  draftScope: string;
  onExecuted: (result: DbAdminExecuteData) => void | Promise<void>;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Code2}
        title={t("metadataSql.execute.title")}
        description={t("metadataSql.execute.hint")}
        action={
          generated ? (
            <StatusBadge icon={false} variant={generated.source === "oci_enterprise_ai" ? "success" : "neutral"} label={metadataSourceLabel(generated.source)} />
          ) : null
        }
      />

      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix={`${pageId}-execute-result`}
          ariaLabel={t("metadataSql.execute.loading")}
          variant="detail"
          placement="result"
        />
      ) : (
        <>
          {!generated && (
            <EmptyState title={t("metadataSql.execute.emptyTitle")} hint={t("metadataSql.execute.emptyHint")} />
          )}

          <WarningsBanner warnings={generated?.warnings} />

          <StatementRunnerCard
            policy={policy}
            executionBlocked={executionBlocked}
            draftScope={draftScope}
            title={t(MODE_CONFIG[mode].runnerKey)}
            placeholder={t(MODE_CONFIG[mode].placeholderKey)}
            initialSql={generated?.sql}
            resetSignal={resetSignal}
            executeOnly
            framed={false}
            onExecuted={onExecuted}
          />
        </>
      )}
    </div>
  );
}

/** SQL を生成した方式（API の内部値）を利用者向けの文言にする（#962。ビュー管理の #949 と同じ）。 */
function metadataSourceLabel(source: string) {
  return source === "oci_enterprise_ai"
    ? t("metadataSql.source.ociEnterpriseAi")
    : t("metadataSql.source.deterministic");
}

function MetadataTextarea({ label, value, rows }: { label: string; value: string; rows: number }) {
  // 同じ画面に複数並ぶ読み取り専用の欄なので、ラベルと結び付ける id は React で一意に作る。
  const id = useId();
  return <TextareaField id={id} label={label} className="min-w-0" readOnly value={value} rows={rows} monospace />;
}

function targetItemsFromObjects(items: DbAdminObjectSummary[]) {
  return items.map((item): MetadataTargetItem => {
    const objectType = normalizeMetadataTargetType(item.object_type);
    const qualifiedName = formatDbObjectName(item);
    const target: MetadataSqlTarget = {
      owner: item.owner,
      object_name: item.name,
      object_type: objectType,
    };
    return {
      ...target,
      key: targetKey(target),
      qualifiedName,
      owner: item.owner,
      row_count: item.row_count,
      comment: item.comment,
    };
  });
}

function targetTypeLabel(objectType: MetadataSqlTarget["object_type"]) {
  if (objectType === "materialized_view") return t("metadataSql.targets.type.materializedView");
  return objectType === "view" ? t("metadataSql.targets.type.view") : t("metadataSql.targets.type.table");
}

function targetKey(target: MetadataSqlTarget) {
  const qualifiedName = parseDbAdminObjectTarget(target.object_name, target.owner).qualifiedName;
  return `${target.object_type}:${qualifiedName}`;
}

function targetFromKey(key: string): MetadataSqlTarget | null {
  const [objectType, ...nameParts] = key.split(":");
  const qualifiedName = nameParts.join(":");
  if (!isMetadataTargetType(objectType) || !qualifiedName) return null;
  const target = parseDbAdminObjectTarget(qualifiedName);
  return { owner: target.owner, object_name: target.name, object_type: objectType };
}

function normalizeMetadataTargetType(value: string): MetadataSqlTarget["object_type"] {
  const normalized = value.replace(/[\s_-]+/gu, "_").toLowerCase();
  if (normalized === "materialized_view" || normalized === "materializedview" || normalized === "mview") {
    return "materialized_view";
  }
  return normalized === "view" ? "view" : "table";
}

function isMetadataTargetType(value: string): value is MetadataSqlTarget["object_type"] {
  return value === "table" || value === "view" || value === "materialized_view";
}

function isViewLikeTarget(value: MetadataSqlTarget["object_type"]) {
  return value === "view" || value === "materialized_view";
}
