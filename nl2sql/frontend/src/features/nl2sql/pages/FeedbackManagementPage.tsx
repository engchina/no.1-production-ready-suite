import { useEffect, useMemo, useState } from "react";
import { Code2, DatabaseZap, MessageSquareText, RefreshCw, Save, Trash2 } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import type {
  SelectAiDbProfile,
  SelectAiDbProfilesData,
  SelectAiFeedbackEntriesData,
  SelectAiFeedbackEntry,
  SelectAiFeedbackMutationData,
} from "../types";

type FeedbackManagementView = "entries" | "vectorIndex";
type MessageTone = "success" | "error";

const FEEDBACK_MANAGEMENT_TABS: Array<DbObjectTab<FeedbackManagementView>> = [
  { id: "entries", label: t("feedbackManagement.tabs.entries"), icon: MessageSquareText },
  { id: "vectorIndex", label: t("feedbackManagement.tabs.vectorIndex"), icon: DatabaseZap },
];
const BUSINESS_SELECT_AI_DB_PROFILES_URL =
  "/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true";

function formatAttributes(entry: SelectAiFeedbackEntry) {
  if (entry.raw_attributes) return entry.raw_attributes;
  return JSON.stringify(entry.attributes ?? {});
}

function profileOptionLabel(profile: SelectAiDbProfile) {
  return profile.owner ? `${profile.name} (${profile.owner})` : profile.name;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function roundThreshold(value: number) {
  return Number(clamp(value, 0.1, 0.95).toFixed(2));
}

export function FeedbackManagementPage() {
  const confirm = useConfirm();
  const [activeView, setActiveView] = useState<FeedbackManagementView>("entries");
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [profileName, setProfileName] = useState("");
  const [feedback, setFeedback] = useState<SelectAiFeedbackEntriesData | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.9);
  const [matchLimit, setMatchLimit] = useState(3);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<MessageTone>("success");

  const profiles = dbProfiles?.profiles ?? [];
  const feedbackItems = feedback?.items ?? [];
  const selectedFeedback = useMemo(
    () => feedbackItems[selectedIndex] ?? feedbackItems[0] ?? null,
    [feedbackItems, selectedIndex]
  );

  const fetchSelectAiFeedback = (name: string) =>
    apiGet<SelectAiFeedbackEntriesData>(
      `/api/nl2sql/select-ai/feedback?profile_name=${encodeURIComponent(name)}&limit=50`
    );

  const refreshSelectAiFeedback = async (name = profileName) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setLoading("feedback");
    setMessage("");
    try {
      setFeedback(await fetchSelectAiFeedback(trimmed));
      setSelectedIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.load"));
    } finally {
      setLoading("");
    }
  };

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const dbProfileData = await apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL);
      const hasCurrentProfile = dbProfileData.profiles.some((profile) => profile.name === profileName);
      const nextProfile = !profileName
        ? dbProfileData.profiles[0]?.name || ""
        : hasCurrentProfile
          ? profileName
          : "";
      const feedbackData = nextProfile ? await fetchSelectAiFeedback(nextProfile) : null;
      setDbProfiles(dbProfileData);
      setProfileName(nextProfile);
      setFeedback(feedbackData);
      setSelectedIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.load"));
    } finally {
      setLoading("");
    }
  };

  const changeProfile = (nextProfile: string) => {
    setProfileName(nextProfile);
    void refreshSelectAiFeedback(nextProfile);
  };

  const deleteSelectedFeedback = async () => {
    if (!selectedFeedback || !profileName.trim()) return;
    const ok = await confirm({
      title: t("feedbackManagement.deleteConfirmTitle"),
      description: t("feedbackManagement.deleteConfirmDescription"),
      confirmLabel: t("feedbackManagement.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("delete");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>("/api/nl2sql/select-ai/feedback/delete", {
        profile_name: profileName,
        sql_text: selectedFeedback.sql_text,
      });
      setMessageTone(data.executed ? "success" : "error");
      setMessage(data.warnings.join(" ") || t("feedbackManagement.deleted"));
      setFeedback(await fetchSelectAiFeedback(profileName));
      setSelectedIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.delete"));
    } finally {
      setLoading("");
    }
  };

  const updateVectorIndex = async () => {
    if (!profileName.trim()) return;
    setLoading("vector-index");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>("/api/nl2sql/select-ai/feedback/vector-index", {
        profile_name: profileName,
        similarity_threshold: similarityThreshold,
        match_limit: matchLimit,
      });
      setMessageTone(data.executed ? "success" : "error");
      setMessage(data.warnings.join(" ") || t("feedbackManagement.index.updated"));
      setFeedback(await fetchSelectAiFeedback(profileName));
      setSelectedIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.update"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const profileSelect = (
    <ProfileSelect
      profiles={profiles}
      value={profileName}
      disabled={loading !== ""}
      onChange={changeProfile}
    />
  );

  return (
    <>
      <PageHeader
        title={t("nav.feedbackManagement")}
        subtitle={t("feedbackManagement.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("learning.action.refresh")}</span>
          </Button>
        }
      />

      <main className="grid gap-4 p-4 lg:p-8">
        <DbObjectManagementStatusBar
          ariaLabel={t("feedbackManagement.status.aria")}
          metricColumnsClass="sm:grid-cols-3"
          metrics={[
            {
              label: t("feedbackManagement.metric.entries"),
              value: String(feedback?.total ?? feedbackItems.length),
              emphasis: true,
              testId: "feedback-management-entry-count",
            },
            { label: t("feedbackManagement.metric.runtime"), value: feedback?.runtime ?? dbProfiles?.runtime ?? "-" },
            { label: t("feedbackManagement.metric.profile"), value: profileName || "-" },
          ]}
          actions={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading === "feedback"}
              disabled={!profileName.trim()}
              onClick={() => void refreshSelectAiFeedback()}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("feedbackManagement.action.refresh")}</span>
            </Button>
          }
        />

        {message && (
          <div
            role={messageTone === "error" ? "alert" : "status"}
            className={`rounded-md border px-4 py-3 text-sm ${
              messageTone === "error"
                ? "border-red-200 bg-red-50 text-red-800"
                : "border-emerald-200 bg-emerald-50 text-emerald-800"
            }`}
          >
            {message}
          </div>
        )}

        <DbObjectManagementTabs
          idPrefix="feedback-management"
          tabs={FEEDBACK_MANAGEMENT_TABS}
          activeView={activeView}
          ariaLabel={t("feedbackManagement.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "entries" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-entries"
            labelledBy="feedback-management-tab-entries"
            ariaLabel={t("feedbackManagement.workspace.entries")}
            idPrefix="feedback-management-entries"
            splitId="feedback-management-entries-split"
            preferredWidePane="left"
          >
            <section className="grid min-w-0 gap-4">
              <DbObjectPanelHeader
                title={t("feedbackManagement.entries.title")}
                description={t("feedbackManagement.entries.hint")}
                icon={MessageSquareText}
                action={
                  <>
                    {profileSelect}
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      loading={loading === "feedback"}
                      disabled={!profileName.trim()}
                      onClick={() => void refreshSelectAiFeedback()}
                    >
                      <RefreshCw size={15} aria-hidden="true" />
                      <span>{t("feedbackManagement.action.refresh")}</span>
                    </Button>
                  </>
                }
              />
              <FeedbackWarnings warnings={feedback?.warnings ?? dbProfiles?.warnings ?? []} />
              <div className="overflow-x-auto rounded-md border border-slate-200">
                <table className="min-w-[860px] w-full table-fixed divide-y divide-slate-200 text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-slate-50 text-xs font-semibold uppercase tracking-normal text-slate-500">
                    <tr>
                      <th scope="col" className="w-[28%] px-3 py-2">
                        {t("feedbackManagement.entries.content")}
                      </th>
                      <th scope="col" className="w-[18%] px-3 py-2">
                        {t("feedbackManagement.entries.sqlId")}
                      </th>
                      <th scope="col" className="w-[34%] px-3 py-2">
                        {t("feedbackManagement.entries.sqlText")}
                      </th>
                      <th scope="col" className="w-[20%] px-3 py-2">
                        {t("feedbackManagement.entries.attributes")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {feedbackItems.map((entry, index) => (
                      <FeedbackEntryRow
                        key={`${entry.sql_id}-${index}`}
                        entry={entry}
                        selected={selectedIndex === index}
                        onSelect={() => setSelectedIndex(index)}
                      />
                    ))}
                    {feedbackItems.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-3 py-8">
                          <EmptyState
                            title={t("feedbackManagement.entries.emptyTitle")}
                            hint={t("feedbackManagement.entries.emptyHint")}
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="grid min-w-0 gap-4 rounded-md border border-slate-200 bg-slate-50 p-4">
              <DbObjectPanelHeader
                title={t("feedbackManagement.entries.selectedSql")}
                icon={Code2}
                action={
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    loading={loading === "delete"}
                    disabled={!selectedFeedback}
                    onClick={() => void deleteSelectedFeedback()}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.delete")}</span>
                  </Button>
                }
              />
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={selectedFeedback?.sql_id || "-"} />
                {feedback?.index_name && <StatusBadge variant="info" label={feedback.index_name} />}
                {feedback?.table_name && <StatusBadge variant="neutral" label={feedback.table_name} />}
              </div>
              <textarea
                aria-label={t("feedbackManagement.entries.selectedSql")}
                value={selectedFeedback?.sql_text ?? ""}
                readOnly
                rows={16}
                className="min-h-80 rounded-md border border-slate-300 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-50 outline-none"
              />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "vectorIndex" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-vectorIndex"
            labelledBy="feedback-management-tab-vectorIndex"
            ariaLabel={t("feedbackManagement.workspace.vectorIndex")}
            idPrefix="feedback-management-vector-index"
          >
            <DbObjectPanelHeader
              title={t("feedbackManagement.index.title")}
              description={t("feedbackManagement.index.hint")}
              icon={DatabaseZap}
              action={profileSelect}
            />
            <FeedbackWarnings warnings={feedback?.warnings ?? dbProfiles?.warnings ?? []} />
            <div className="grid gap-4 lg:grid-cols-2">
              <SliderNumberField
                label={t("feedbackManagement.index.threshold")}
                min={0.1}
                max={0.95}
                step={0.05}
                value={similarityThreshold}
                onChange={(value) => setSimilarityThreshold(roundThreshold(value))}
              />
              <SliderNumberField
                label={t("feedbackManagement.index.matchLimit")}
                min={1}
                max={5}
                step={1}
                value={matchLimit}
                onChange={(value) => setMatchLimit(Math.round(clamp(value, 1, 5)))}
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={feedback?.runtime ?? dbProfiles?.runtime ?? "-"} />
                {feedback?.index_name && <StatusBadge variant="info" label={feedback.index_name} />}
                {feedback?.table_name && <StatusBadge variant="neutral" label={feedback.table_name} />}
              </div>
              <Button
                type="button"
                variant="primary"
                size="sm"
                loading={loading === "vector-index"}
                disabled={!profileName.trim()}
                onClick={() => void updateVectorIndex()}
              >
                <Save size={15} aria-hidden="true" />
                <span>{t("feedbackManagement.index.update")}</span>
              </Button>
            </div>
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

function ProfileSelect({
  profiles,
  value,
  disabled,
  onChange,
}: {
  profiles: SelectAiDbProfile[];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800 sm:min-w-72">
      <span>{t("feedbackManagement.profile")}</span>
      <select
        value={value}
        disabled={disabled || profiles.length === 0}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
      >
        {profiles.map((profile) => (
          <option key={profile.name} value={profile.name}>
            {profileOptionLabel(profile)}
          </option>
        ))}
      </select>
    </label>
  );
}

function FeedbackWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="grid gap-2">
      {warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}
    </div>
  );
}

function FeedbackEntryRow({
  entry,
  selected,
  onSelect,
}: {
  entry: SelectAiFeedbackEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr className={selected ? "bg-sky-50" : "hover:bg-slate-50"} onClick={onSelect}>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words text-slate-800">{entry.content || "-"}</p>
      </td>
      <td className="px-3 py-2 align-top">
        <button
          type="button"
          className="text-left font-mono text-xs font-semibold text-sky-700 underline-offset-2 hover:underline focus:outline-none focus:ring-2 focus:ring-sky-200"
          aria-label={t("feedbackManagement.entries.show", { id: entry.sql_id || "-" })}
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
        >
          {entry.sql_id || "-"}
        </button>
      </td>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words font-mono text-xs leading-5 text-slate-800">{entry.sql_text || "-"}</p>
      </td>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words font-mono text-xs leading-5 text-slate-600">
          {formatAttributes(entry)}
        </p>
      </td>
    </tr>
  );
}

function SliderNumberField({
  label,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <fieldset className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-4 text-sm font-medium text-slate-800">
      <legend className="px-1">{label}</legend>
      <input
        type="range"
        aria-label={`${label} slider`}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="w-full accent-sky-700"
      />
      <input
        type="number"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
      />
    </fieldset>
  );
}
