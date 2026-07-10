import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search, Wand2 } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { buildMetadataInputTexts } from "../metadataSql";
import { PageMetric, StatementRunnerCard } from "../components/DbAdminShared";
import type {
  DbAdminObjectDetail,
  DbAdminObjectsData,
  DbAdminStatementPolicy,
  MetadataSqlGenerateData,
  MetadataSqlGeneratePayload,
  MetadataSqlTarget,
  SchemaCatalog,
} from "../types";

type MetadataMode = "comment" | "annotation";

const ANNOTATION_EXTRA_TEXT =
  "ANNOTATIONSの安全な適用ガイド:\n" +
  "- DROPとADDは同一文で混在させず、別々のALTER文に分割\n" +
  "- 重複名を避けるため、可能ならADD IF NOT EXISTSを使う\n" +
  "- 値内の'は''へエスケープする\n" +
  "例(表): ALTER TABLE USERS ANNOTATIONS (ADD UI_Display 'Users');\n" +
  "例(列): ALTER TABLE USERS MODIFY (ID ANNOTATIONS (ADD UI_Display 'ID'));";

export function CommentManagementPage() {
  return <MetadataSqlManagementPage mode="comment" />;
}

export function AnnotationManagementPage() {
  return <MetadataSqlManagementPage mode="annotation" />;
}

function MetadataSqlManagementPage({ mode }: { mode: MetadataMode }) {
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [details, setDetails] = useState<DbAdminObjectDetail[]>([]);
  const [sampleLimit, setSampleLimit] = useState(10);
  const [extraText, setExtraText] = useState(mode === "annotation" ? ANNOTATION_EXTRA_TEXT : "");
  const [generated, setGenerated] = useState<MetadataSqlGenerateData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const selectedTargets = useMemo(
    () => selectedKeys.map((key) => targetFromKey(key)).filter(Boolean) as MetadataSqlTarget[],
    [selectedKeys]
  );
  const inputTexts = useMemo(
    () => buildMetadataInputTexts(details, catalog, sampleLimit),
    [catalog, details, sampleLimit]
  );
  const policy: DbAdminStatementPolicy = mode === "comment" ? "comment_sql" : "annotation_sql";

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [tableData, viewData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setTables(tableData);
      setViews(viewData);
      setCatalog(catalogData);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const toggleTarget = (target: MetadataSqlTarget) => {
    const key = targetKey(target);
    setSelectedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
  };

  const fetchDetails = async () => {
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    setLoading("details");
    setMessage("");
    try {
      const nextDetails = await Promise.all(
        selectedTargets.map((target) =>
          apiGet<DbAdminObjectDetail>(
            target.object_type === "view"
              ? `/api/nl2sql/db-admin/views/${encodeURIComponent(target.object_name)}`
              : `/api/nl2sql/db-admin/tables/${encodeURIComponent(target.object_name)}`
          )
        )
      );
      setDetails(nextDetails);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.details"));
    } finally {
      setLoading("");
    }
  };

  const generateSql = async () => {
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    setLoading("generate");
    setMessage("");
    try {
      const payload: MetadataSqlGeneratePayload = {
        targets: selectedTargets,
        structure_text: inputTexts.structureText,
        primary_key_text: inputTexts.primaryKeyText,
        foreign_key_text: inputTexts.foreignKeyText,
        sample_text: inputTexts.sampleText,
        extra_text: extraText,
      };
      const path =
        mode === "comment"
          ? "/api/nl2sql/comments/generate-sql"
          : "/api/nl2sql/annotations/generate-sql";
      setGenerated(await apiPost<MetadataSqlGenerateData>(path, payload));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.generate"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t(mode === "comment" ? "nav.commentManagement" : "nav.annotationManagement")}
        subtitle={t(
          mode === "comment"
            ? "metadataSql.comment.subtitle"
            : "metadataSql.annotation.subtitle"
        )}
        actions={
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loading === "load"}
            onClick={() => void load()}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("tableMgmt.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardContent className="grid gap-3 p-4 md:grid-cols-3">
            <PageMetric label={t("metadataSql.metric.tables")} value={String(tables?.items.length ?? 0)} />
            <PageMetric label={t("metadataSql.metric.views")} value={String(views?.items.length ?? 0)} />
            <PageMetric label={t("metadataSql.metric.selected")} value={String(selectedTargets.length)} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                <Search size={18} aria-hidden="true" />
                {t("metadataSql.targets.title")}
              </CardTitle>
              <Button
                type="button"
                variant="primary"
                size="sm"
                loading={loading === "details"}
                disabled={selectedTargets.length === 0}
                onClick={() => void fetchDetails()}
              >
                <span>{t("metadataSql.action.fetchInfo")}</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 xl:grid-cols-2">
            <TargetPicker
              title={t("metadataSql.targets.tables")}
              items={tables?.items ?? []}
              objectType="table"
              selectedKeys={selectedKeys}
              onToggle={toggleTarget}
            />
            <TargetPicker
              title={t("metadataSql.targets.views")}
              items={views?.items ?? []}
              objectType="view"
              selectedKeys={selectedKeys}
              onToggle={toggleTarget}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle>{t("metadataSql.input.title")}</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-h-9 items-center gap-2 text-sm font-medium text-slate-800">
                  <span>{t("metadataSql.input.sampleLimit")}</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={sampleLimit}
                    onChange={(event) => setSampleLimit(Number(event.currentTarget.value))}
                    className="min-h-9 w-24 rounded-md border border-slate-300 px-3 py-1 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  loading={loading === "generate"}
                  disabled={selectedTargets.length === 0}
                  onClick={() => void generateSql()}
                >
                  <Wand2 size={15} aria-hidden="true" />
                  <span>{t("metadataSql.action.generate")}</span>
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="grid gap-3">
            <MetadataTextarea label={t("metadataSql.input.structure")} value={inputTexts.structureText} rows={8} />
            <MetadataTextarea label={t("metadataSql.input.pk")} value={inputTexts.primaryKeyText} rows={4} />
            <MetadataTextarea label={t("metadataSql.input.fk")} value={inputTexts.foreignKeyText} rows={4} />
            <MetadataTextarea label={t("metadataSql.input.sample")} value={inputTexts.sampleText} rows={6} />
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("metadataSql.input.extra")}</span>
              <textarea
                value={extraText}
                onChange={(event) => setExtraText(event.currentTarget.value)}
                rows={6}
                className="min-h-32 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
          </CardContent>
        </Card>

        {generated && (
          <section className="grid gap-2">
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant={generated.source === "oci_enterprise_ai" ? "success" : "neutral"} label={generated.source} />
            </div>
            {generated.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {warning}
              </p>
            ))}
          </section>
        )}

        <StatementRunnerCard
          policy={policy}
          target={mode}
          title={t(mode === "comment" ? "metadataSql.comment.runner" : "metadataSql.annotation.runner")}
          placeholder={t(
            mode === "comment"
              ? "metadataSql.comment.placeholder"
              : "metadataSql.annotation.placeholder"
          )}
          initialSql={generated?.sql ?? ""}
          onExecuted={() => load()}
        />
      </main>
    </>
  );
}

function TargetPicker({
  title,
  items,
  objectType,
  selectedKeys,
  onToggle,
}: {
  title: string;
  items: Array<{ name: string; comment: string }>;
  objectType: "table" | "view";
  selectedKeys: string[];
  onToggle: (target: MetadataSqlTarget) => void;
}) {
  return (
    <section className="grid content-start gap-2">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {items.length === 0 ? (
        <EmptyState title={t("metadataSql.targets.emptyTitle")} hint={t("metadataSql.targets.emptyHint")} />
      ) : (
        <div className="grid max-h-80 gap-2 overflow-auto pr-1">
          {items.map((item) => {
            const target: MetadataSqlTarget = { object_name: item.name, object_type: objectType };
            const key = targetKey(target);
            return (
              <label
                key={key}
                className="flex min-h-14 cursor-pointer items-start gap-3 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800 hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={selectedKeys.includes(key)}
                  onChange={() => onToggle(target)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span className="min-w-0">
                  <span className="block break-all font-mono text-xs font-semibold text-sky-800">{item.name}</span>
                  <span className="mt-1 block break-words text-xs text-slate-600">{item.comment || "-"}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
    </section>
  );
}

function MetadataTextarea({ label, value, rows }: { label: string; value: string; rows: number }) {
  return (
    <label className="grid gap-1 text-sm font-medium text-slate-800">
      <span>{label}</span>
      <textarea
        readOnly
        value={value}
        rows={rows}
        className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800"
      />
    </label>
  );
}

function targetKey(target: MetadataSqlTarget) {
  return `${target.object_type}:${target.object_name}`;
}

function targetFromKey(key: string): MetadataSqlTarget | null {
  const [objectType, ...nameParts] = key.split(":");
  const objectName = nameParts.join(":");
  if ((objectType !== "table" && objectType !== "view") || !objectName) return null;
  return { object_name: objectName, object_type: objectType };
}
