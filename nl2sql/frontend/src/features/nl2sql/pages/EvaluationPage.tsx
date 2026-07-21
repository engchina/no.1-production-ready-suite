import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRightLeft,
  Archive,
  Clipboard,
  Download,
  FileText,
  FlaskConical,
  GitCompare,
  RefreshCw,
  Save,
  ShieldCheck,
  Upload,
  Wand2,
} from "lucide-react";

import {
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiGet, apiPatch, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { engineLabel } from "../labels";
import type {
  AnalyzeData,
  CompareData,
  CompareExecutionData,
  CompareHistoryData,
  CompareRecord,
  EvaluateData,
  EvaluationRunRecord,
  EvaluationRunsData,
  EvaluationSet,
  EvaluationSetPayload,
  EvaluationSetsData,
  Nl2SqlEngine,
  Nl2SqlProfile,
  PreviewData,
  ProfileUpsertPayload,
  ReverseSqlData,
  SyntheticCase,
  SyntheticCasesData,
} from "../types";
import { formatElapsed } from "../useOperationTimer";

const DEFAULT_CASES: EvaluationCaseLike[] = [];
const DEFAULT_ANALYZE_SQL = "";

type EvaluationCaseLike = {
  question: string;
  expected_sql: string;
  profile_id?: string;
};

type ActiveView = "sets" | "analyze" | "compare" | "synthetic" | "reverse";

const fieldClass = "grid min-w-0 gap-1 text-sm font-medium text-foreground";
const controlClass =
  "min-h-11 rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const textareaClass =
  "rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

export function EvaluationPage() {
  const confirm = useConfirm();
  const [activeView, setActiveView] = useState<ActiveView>("sets");
  const [question, setQuestion] = useState("");
  const [engine, setEngine] = useState<Nl2SqlEngine>("auto");
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("default");
  const [evaluationSets, setEvaluationSets] = useState<EvaluationSet[]>([]);
  const [evaluationSetId, setEvaluationSetId] = useState("");
  const [evaluationSetName, setEvaluationSetName] = useState(t("evaluation.set.defaultName"));
  const [evaluationSetDescription, setEvaluationSetDescription] = useState("");
  const [evaluation, setEvaluation] = useState<EvaluateData | null>(null);
  const [evaluationRuns, setEvaluationRuns] = useState<EvaluationRunRecord[]>([]);
  const [comparison, setComparison] = useState<CompareData | null>(null);
  const [compareHistory, setCompareHistory] = useState<CompareRecord[]>([]);
  const [synthetic, setSynthetic] = useState<SyntheticCasesData | null>(null);
  const [analysisSql, setAnalysisSql] = useState(DEFAULT_ANALYZE_SQL);
  const [analysis, setAnalysis] = useState<AnalyzeData | null>(null);
  const [reverseSql, setReverseSql] = useState("");
  const [reverseUseGlossary, setReverseUseGlossary] = useState(true);
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [evaluationSetSearch, setEvaluationSetSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const loadCompareHistory = async (signal?: AbortSignal): Promise<boolean> => {
    try {
      const data = await apiGet<CompareHistoryData>(
        "/api/nl2sql/compare-history?limit=5",
        { signal }
      );
      if (signal?.aborted) return true;
      setCompareHistory(data.items);
      return true;
    } catch (cause) {
      if (isAbortError(cause)) throw cause;
      setCompareHistory([]);
      return false;
    }
  };

  const loadProfiles = async (signal?: AbortSignal): Promise<boolean> => {
    try {
      const data = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", { signal });
      if (signal?.aborted) return true;
      setProfiles(data);
      if (data.length > 0 && !data.some((profile) => profile.id === profileId)) {
        setProfileId(data[0].id);
      }
      return true;
    } catch (cause) {
      if (isAbortError(cause)) throw cause;
      setProfiles([]);
      return false;
    }
  };

  const loadEvaluationSets = async (signal?: AbortSignal): Promise<boolean> => {
    try {
      const data = await apiGet<EvaluationSetsData>("/api/nl2sql/evaluation-sets", {
        signal,
      });
      if (signal?.aborted) return true;
      setEvaluationSets(data.items);
      return true;
    } catch (cause) {
      if (isAbortError(cause)) throw cause;
      setEvaluationSets([]);
      return false;
    }
  };

  const loadEvaluationRuns = async (signal?: AbortSignal): Promise<boolean> => {
    try {
      const data = await apiGet<EvaluationRunsData>(
        "/api/nl2sql/evaluation-runs?limit=5",
        { signal }
      );
      if (signal?.aborted) return true;
      setEvaluationRuns(data.items);
      return true;
    } catch (cause) {
      if (isAbortError(cause)) throw cause;
      setEvaluationRuns([]);
      return false;
    }
  };

  const loadPageData = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const results = await Promise.all([
          loadCompareHistory(signal),
          loadProfiles(signal),
          loadEvaluationSets(signal),
          loadEvaluationRuns(signal),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        if (results.every(Boolean)) {
          if (announce) toast.success(t("common.action.refreshed"));
        } else {
          setMessage(t("evaluation.error.load"));
        }
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }
      setMessage(t("evaluation.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    void loadPageData();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  const currentCases = evaluationCases(synthetic);
  const filteredEvaluationSets = useMemo(() => {
    const q = evaluationSetSearch.trim().toLowerCase();
    if (!q) return evaluationSets;
    return evaluationSets.filter((item) =>
      [
        item.name,
        item.description,
        item.profile_name,
        item.profile_id,
        engineLabel(item.engine),
      ]
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }, [evaluationSets, evaluationSetSearch]);

  const runEvaluate = async () => {
    setLoading(true);
    setMessage("");
    try {
      setEvaluation(
        await apiPost<EvaluateData>("/api/nl2sql/evaluate", {
          cases: evaluationCases(synthetic),
          engine,
          profile_id: profileId,
          evaluation_set_id: evaluationSetId || undefined,
        })
      );
      void loadEvaluationRuns();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.run"));
    } finally {
      setLoading(false);
    }
  };

  const generateSynthetic = async () => {
    setLoading(true);
    setMessage("");
    try {
      setSynthetic(
        await apiPost<SyntheticCasesData>(
          `/api/nl2sql/synthetic-cases?profile_id=${encodeURIComponent(profileId)}`
        )
      );
      setActiveView("synthetic");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.synthetic"));
    } finally {
      setLoading(false);
    }
  };

  const saveSyntheticToProfile = async () => {
    const profile = profiles.find((item) => item.id === profileId);
    if (!profile || !synthetic?.cases.length) return;
    setLoading(true);
    setMessage("");
    try {
      const updated = await apiPatch<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${profile.id}`,
        profilePayloadWithSynthetic(profile, synthetic)
      );
      setProfiles((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      toast.success(t("evaluation.synthetic.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.synthetic.saveError"));
    } finally {
      setLoading(false);
    }
  };

  const selectEvaluationSet = (nextId: string) => {
    setEvaluationSetId(nextId);
    if (!nextId) {
      setEvaluationSetName(t("evaluation.set.defaultName"));
      setEvaluationSetDescription("");
      return;
    }
    const selected = evaluationSets.find((item) => item.id === nextId);
    if (!selected) return;
    setEvaluationSetName(selected.name);
    setEvaluationSetDescription(selected.description);
    setProfileId(selected.profile_id || "default");
    setEngine(selected.engine);
    setSynthetic({ cases: selected.cases });
    setEvaluation(null);
    setActiveView("sets");
  };

  const saveEvaluationSet = async () => {
    const name = evaluationSetName.trim();
    if (!name) {
      setMessage(t("evaluation.set.nameRequired"));
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const payload: EvaluationSetPayload = {
        name,
        description: evaluationSetDescription,
        profile_id: profileId,
        engine,
        cases: casesForProfile(evaluationCases(synthetic), profileId),
      };
      const saved = evaluationSetId
        ? await apiPatch<EvaluationSet>(`/api/nl2sql/evaluation-sets/${evaluationSetId}`, payload)
        : await apiPost<EvaluationSet>("/api/nl2sql/evaluation-sets", payload);
      setEvaluationSetId(saved.id);
      setEvaluationSetName(saved.name);
      setEvaluationSetDescription(saved.description);
      setSynthetic({ cases: saved.cases });
      await loadEvaluationSets();
      toast.success(t("evaluation.set.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.set.saveError"));
    } finally {
      setLoading(false);
    }
  };

  const archiveEvaluationSet = async () => {
    if (!evaluationSetId) return;
    const accepted = await confirm({
      title: t("evaluation.set.archiveConfirmTitle"),
      description: t("evaluation.set.archiveConfirmDescription"),
      confirmLabel: t("evaluation.action.archiveSet"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!accepted) return;
    setLoading(true);
    setMessage("");
    try {
      await apiPost<EvaluationSet>(`/api/nl2sql/evaluation-sets/${evaluationSetId}/archive`);
      setEvaluationSetId("");
      setEvaluationSetName(t("evaluation.set.defaultName"));
      setEvaluationSetDescription("");
      await loadEvaluationSets();
      toast.success(t("evaluation.set.archived"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.set.archiveError"));
    } finally {
      setLoading(false);
    }
  };

  const restoreEvaluationRun = (record: EvaluationRunRecord) => {
    setEvaluation(record.result);
    setSynthetic({ cases: record.cases });
    setEngine(record.engine);
    setProfileId(record.profile_id || "default");
    if (record.evaluation_set_id && evaluationSets.some((item) => item.id === record.evaluation_set_id)) {
      setEvaluationSetId(record.evaluation_set_id);
      setEvaluationSetName(record.evaluation_set_name || t("evaluation.set.defaultName"));
      const selected = evaluationSets.find((item) => item.id === record.evaluation_set_id);
      setEvaluationSetDescription(selected?.description ?? "");
    }
    setActiveView("sets");
  };

  const exportEvaluationRunReport = (record: EvaluationRunRecord) => {
    try {
      downloadTextFile(
        record.report || buildEvaluationRunReport(record),
        `${evaluationRunFileStem(record)}.md`,
        "text/markdown;charset=utf-8"
      );
      toast.success(t("evaluation.run.reportDownloaded"));
    } catch {
      toast.error(t("evaluation.run.downloadError"));
    }
  };

  const exportEvaluationRunCases = (record: EvaluationRunRecord) => {
    try {
      downloadEvaluationCasesCsv(record.cases, `${evaluationRunFileStem(record)}_cases.csv`);
      toast.success(t("evaluation.run.casesDownloaded"));
    } catch {
      toast.error(t("evaluation.run.downloadError"));
    }
  };

  const exportEvaluationCases = () => {
    try {
      downloadEvaluationCasesCsv(evaluationCases(synthetic));
      toast.success(t("evaluation.run.casesDownloaded"));
    } catch {
      toast.error(t("evaluation.run.downloadError"));
    }
  };

  const importEvaluationCases = async (file: File | undefined) => {
    if (!file) return;
    setMessage("");
    try {
      const text = await file.text();
      const cases = parseEvaluationCasesCsv(text, profileId);
      if (cases.length === 0) {
        toast.info(t("evaluation.synthetic.importEmpty"));
        return;
      }
      setSynthetic({ cases });
      setEvaluation(null);
      toast.success(t("evaluation.synthetic.imported", { count: cases.length }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.synthetic.importError"));
    }
  };

  const compare = async () => {
    setLoading(true);
    setMessage("");
    try {
      const data = await apiPost<CompareData>("/api/nl2sql/compare", {
        question,
        execute: true,
        profile_id: profileId,
      });
      setComparison(data);
      setActiveView("compare");
      void loadCompareHistory();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.compare"));
    } finally {
      setLoading(false);
    }
  };

  const restoreComparison = (record: CompareRecord) => {
    setQuestion(record.question);
    setComparison(record.comparison);
    setActiveView("compare");
  };

  const compareReport = comparison ? buildCompareReport(comparison) : "";

  const copyCompareReport = async () => {
    if (!compareReport) return;
    try {
      await navigator.clipboard.writeText(compareReport);
      toast.success(t("evaluation.compare.reportCopied"));
    } catch {
      toast.error(t("evaluation.compare.reportCopyError"));
    }
  };

  const analyzeSql = async () => {
    const sql = analysisSql.trim();
    if (!sql) return;
    setAnalysisLoading(true);
    setMessage("");
    try {
      setAnalysis(await apiPost<AnalyzeData>("/api/nl2sql/analyze", { sql }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.analyze"));
    } finally {
      setAnalysisLoading(false);
    }
  };

  const reverseExplain = async () => {
    const sql = reverseSql.trim();
    if (!sql) return;
    setReverseLoading(true);
    setMessage("");
    try {
      setReverse(
        await apiPost<ReverseSqlData>("/api/nl2sql/reverse", {
          sql,
          profile_id: profileId || undefined,
          use_glossary: reverseUseGlossary,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  const evaluationTabs = [
    { id: "sets", label: t("evaluation.tabs.sets"), icon: FlaskConical },
    { id: "analyze", label: t("evaluation.tabs.analyze"), icon: ShieldCheck },
    { id: "compare", label: t("evaluation.tabs.compare"), icon: GitCompare },
    { id: "synthetic", label: t("evaluation.tabs.synthetic"), icon: Wand2 },
    { id: "reverse", label: t("evaluation.tabs.reverse"), icon: ArrowRightLeft },
  ] satisfies Array<DbObjectTab<ActiveView>>;

  return (
    <>
      <PageHeader title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice notice={message ? { tone: "danger", message } : null} />

        <DbObjectManagementStatusBar
          ariaLabel={t("evaluation.status.label")}
          metricColumnsClass="sm:grid-cols-4"
          metrics={[
            { label: t("evaluation.status.sets"), value: String(evaluationSets.length), emphasis: true },
            { label: t("evaluation.status.cases"), value: String(currentCases.length) },
            { label: t("evaluation.status.runs"), value: String(evaluationRuns.length) },
            { label: t("evaluation.status.comparisons"), value: String(compareHistory.length) },
          ]}
          actions={
            <Button type="button" variant="secondary" size="sm" loading={loading} onClick={() => void loadPageData(true)}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("evaluation.action.refresh")}</span>
            </Button>
          }
        />

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={evaluationTabs}
          idPrefix="evaluation"
          ariaLabel={t("evaluation.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "sets" && (
          <DbObjectManagementPanelShell
            id="evaluation-panel-sets"
            labelledBy="evaluation-tab-sets"
            idPrefix="evaluation"
            ariaLabel={t("evaluation.workspace.sets")}
            splitId="evaluation-sets"
            preferredWidePane="right"
          >
            <EvaluationSetList
              items={filteredEvaluationSets}
              selectedId={evaluationSetId}
              search={evaluationSetSearch}
              onSearchChange={setEvaluationSetSearch}
              onSelect={selectEvaluationSet}
            />
            <section className="grid min-w-0 content-start gap-4">
              <DbObjectPanelHeader
                icon={FlaskConical}
                title={t("evaluation.runner.title")}
                description={t("evaluation.runner.description")}
                action={<StatusBadge variant="info" label={t("evaluation.run.caseCount", { count: currentCases.length })} />}
              />

              <div className="grid gap-3 rounded-md border border-border bg-background p-3">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(11rem,0.55fr)_minmax(11rem,0.55fr)]">
                  <label className={fieldClass}>
                    <span>{t("evaluation.set.label")}</span>
                    <select
                      value={evaluationSetId}
                      onChange={(event) => selectEvaluationSet(event.currentTarget.value)}
                      className={controlClass}
                    >
                      <option value="">{t("evaluation.set.new")}</option>
                      {evaluationSets.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={fieldClass}>
                    <span>{t("evaluation.set.name")}</span>
                    <input
                      value={evaluationSetName}
                      onChange={(event) => setEvaluationSetName(event.currentTarget.value)}
                      className={controlClass}
                    />
                  </label>
                  <label className={fieldClass}>
                    <span>{t("evaluation.set.description")}</span>
                    <input
                      value={evaluationSetDescription}
                      onChange={(event) => setEvaluationSetDescription(event.currentTarget.value)}
                      className={controlClass}
                    />
                  </label>
                </div>

                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(11rem,0.45fr)_minmax(11rem,0.45fr)]">
                  <label className={fieldClass}>
                    <span>{t("evaluation.question.label")}</span>
                    <input
                      value={question}
                      onChange={(event) => setQuestion(event.currentTarget.value)}
                      className={controlClass}
                    />
                  </label>
                  <label className={fieldClass}>
                    <span>{t("nl2sql.engine.label")}</span>
                    <select
                      value={engine}
                      onChange={(event) => setEngine(event.currentTarget.value as Nl2SqlEngine)}
                      className={controlClass}
                    >
                      <option value="auto">{t("nl2sql.engine.auto")}</option>
                      <option value="select_ai_agent">{t("nl2sql.engine.agent")}</option>
                      <option value="select_ai">{t("nl2sql.engine.selectAi")}</option>
                      <option value="enterprise_ai_direct">{t("nl2sql.engine.direct")}</option>
                    </select>
                  </label>
                  <label className={fieldClass}>
                    <span>{t("evaluation.profile.label")}</span>
                    <select
                      value={profileId}
                      onChange={(event) => setProfileId(event.currentTarget.value)}
                      className={controlClass}
                    >
                      {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <EvaluationPrimaryActions
                  loading={loading}
                  hasEvaluationSet={Boolean(evaluationSetId)}
                  hasSyntheticCases={Boolean(synthetic?.cases.length)}
                  hasProfiles={profiles.length > 0}
                  onSaveSet={() => void saveEvaluationSet()}
                  onArchiveSet={() => void archiveEvaluationSet()}
                  onRunEvaluate={() => void runEvaluate()}
                  onCompare={() => void compare()}
                  onGenerateSynthetic={() => void generateSynthetic()}
                  onSaveSynthetic={() => void saveSyntheticToProfile()}
                />
              </div>

              {evaluation ? (
                <EvaluationResultSummary evaluation={evaluation} />
              ) : (
                <EmptyState title={t("evaluation.result.emptyTitle")} hint={t("evaluation.result.emptyHint")} />
              )}

              <EvaluationCasesTable cases={currentCases} />

              <EvaluationRunHistory
                records={evaluationRuns}
                onRestore={restoreEvaluationRun}
                onExportReport={exportEvaluationRunReport}
                onExportCases={exportEvaluationRunCases}
              />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "analyze" && (
          <DbObjectManagementPanelShell
            id="evaluation-panel-analyze"
            labelledBy="evaluation-tab-analyze"
            idPrefix="evaluation"
            ariaLabel={t("evaluation.workspace.analyze")}
          >
            <section className="grid gap-4">
              <DbObjectPanelHeader
                icon={ShieldCheck}
                title={t("evaluation.analyze.title")}
                description={t("evaluation.analyze.description")}
              />
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
                <div className="grid content-start gap-4">
                  <label className={fieldClass}>
                    <span>{t("evaluation.analyze.sql")}</span>
                    <textarea
                      value={analysisSql}
                      onChange={(event) => setAnalysisSql(event.currentTarget.value)}
                      rows={8}
                      className={`${textareaClass} min-h-56`}
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      loading={analysisLoading}
                      disabled={!analysisSql.trim()}
                      onClick={() => void analyzeSql()}
                    >
                      <ShieldCheck size={16} aria-hidden="true" />
                      <span>{t("evaluation.action.analyze")}</span>
                    </Button>
                  </div>
                </div>

                {analysis ? (
                  <AnalysisResult analysis={analysis} />
                ) : (
                  <EmptyState title={t("evaluation.analyze.emptyTitle")} hint={t("evaluation.analyze.emptyHint")} />
                )}
              </div>
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "compare" && (
          <DbObjectManagementPanelShell
            id="evaluation-panel-compare"
            labelledBy="evaluation-tab-compare"
            idPrefix="evaluation"
            ariaLabel={t("evaluation.workspace.compare")}
          >
            <section className="grid gap-4">
              <DbObjectPanelHeader
                icon={GitCompare}
                title={t("evaluation.compare.title")}
                description={t("evaluation.compare.description")}
                action={
                  <Button type="button" loading={loading} onClick={() => void compare()}>
                    <GitCompare size={16} aria-hidden="true" />
                    <span>{t("evaluation.action.compare")}</span>
                  </Button>
                }
              />
              <label className={fieldClass}>
                <span>{t("evaluation.question.label")}</span>
                <input
                  value={question}
                  onChange={(event) => setQuestion(event.currentTarget.value)}
                  className={controlClass}
                />
              </label>
              {comparison ? (
                <CompareResult comparison={comparison} compareReport={compareReport} onCopyReport={copyCompareReport} />
              ) : (
                <EmptyState title={t("evaluation.compare.emptyTitle")} hint={t("evaluation.compare.emptyHint")} />
              )}
              <CompareHistoryList records={compareHistory} onRestore={restoreComparison} />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "synthetic" && (
          <DbObjectManagementPanelShell
            id="evaluation-panel-synthetic"
            labelledBy="evaluation-tab-synthetic"
            idPrefix="evaluation"
            ariaLabel={t("evaluation.workspace.synthetic")}
          >
            <section className="grid gap-4">
              <DbObjectPanelHeader
                icon={Wand2}
                title={t("evaluation.synthetic.title")}
                description={t("evaluation.synthetic.description")}
                action={
                  <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
                    <Button type="button" variant="secondary" loading={loading} onClick={() => void generateSynthetic()}>
                      <Wand2 size={16} aria-hidden="true" />
                      <span>{t("evaluation.action.synthetic")}</span>
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      loading={loading}
                      disabled={!synthetic?.cases.length || !profiles.length}
                      onClick={() => void saveSyntheticToProfile()}
                    >
                      <Wand2 size={16} aria-hidden="true" />
                      <span>{t("evaluation.action.saveSynthetic")}</span>
                    </Button>
                    <Button type="button" variant="secondary" onClick={() => void saveEvaluationSet()}>
                      <Save size={16} aria-hidden="true" />
                      <span>{t("evaluation.action.saveSet")}</span>
                    </Button>
                    <Button type="button" variant="secondary" onClick={exportEvaluationCases}>
                      <Download size={16} aria-hidden="true" />
                      <span>{t("evaluation.action.exportCases")}</span>
                    </Button>
                    <CsvImportButton onImport={importEvaluationCases} />
                  </div>
                }
              />
              <EvaluationCasesTable cases={currentCases} title={t("evaluation.synthetic.title")} />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "reverse" && (
          <DbObjectManagementPanelShell
            id="evaluation-panel-reverse"
            labelledBy="evaluation-tab-reverse"
            idPrefix="evaluation"
            ariaLabel={t("evaluation.workspace.reverse")}
          >
            <section className="grid gap-4">
              <DbObjectPanelHeader
                icon={ArrowRightLeft}
                title={t("nav.sqlToQuestion")}
                description={t("evaluation.reverse.description")}
              />
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
                <div className="grid content-start gap-4">
                  <label className={fieldClass}>
                    <span>{t("sqlToQuestion.sql.label")}</span>
                    <textarea
                      value={reverseSql}
                      onChange={(event) => setReverseSql(event.currentTarget.value)}
                      rows={8}
                      className={`${textareaClass} min-h-56`}
                    />
                  </label>
                  <label className="flex min-h-11 w-fit items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={reverseUseGlossary}
                      onChange={(event) => setReverseUseGlossary(event.currentTarget.checked)}
                      className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                    />
                    <span>{t("sqlToQuestion.useGlossary")}</span>
                  </label>
                  {/* 主アクションバー: spec §4(border-t で区切り、size 統一)。SqlToQuestionPage と統一。 */}
                  <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                    <Button
                      type="button"
                      variant="primary"
                      size="lg"
                      loading={reverseLoading}
                      disabled={!reverseSql.trim()}
                      onClick={() => void reverseExplain()}
                    >
                      <ArrowRightLeft size={16} aria-hidden="true" />
                      <span>{t("sqlToQuestion.action.generate")}</span>
                    </Button>
                  </div>
                </div>
                <ReverseResult reverse={reverse} useGlossary={reverseUseGlossary} />
              </div>
            </section>
          </DbObjectManagementPanelShell>
        )}

      </main>
    </>
  );
}

function EvaluationSetList({
  items,
  selectedId,
  search,
  onSearchChange,
  onSelect,
}: {
  items: EvaluationSet[];
  selectedId: string;
  search: string;
  onSearchChange: (value: string) => void;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="evaluation-set-list-heading">
      <DbObjectPanelHeader
        headingId="evaluation-set-list-heading"
        icon={FileText}
        title={t("evaluation.set.listTitle")}
        description={t("evaluation.set.listHint")}
        action={<StatusBadge variant="info" label={t("evaluation.set.listCount", { count: items.length })} />}
      />
      <div className="rounded-md border border-border bg-background p-3">
        <DbManagementSearchField
          label={t("evaluation.set.search")}
          placeholder={t("evaluation.set.searchPlaceholder")}
          value={search}
          onChange={onSearchChange}
        />
      </div>
      {items.length === 0 ? (
        <EmptyState title={t("evaluation.set.emptyTitle")} hint={t("evaluation.set.emptyHint")} />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="max-h-[42rem] overflow-auto">
            <table className="w-full min-w-[34rem] table-fixed divide-y divide-border text-left text-sm">
              <colgroup>
                <col className="w-[42%]" />
                <col className="w-[18%]" />
                <col className="w-[18%]" />
                <col className="w-[22%]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
                <tr>
                  <th className="px-3 py-2">{t("evaluation.set.name")}</th>
                  <th className="px-3 py-2">{t("evaluation.metric.cases")}</th>
                  <th className="px-3 py-2">{t("nl2sql.engine.label")}</th>
                  <th className="px-3 py-2 text-right">{t("evaluation.set.actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {items.map((item) => {
                  const selected = item.id === selectedId;
                  return (
                    <tr key={item.id} className={selected ? "bg-primary/10" : "hover:bg-background"}>
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          aria-current={selected ? "true" : undefined}
                          className="break-words text-left font-semibold text-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                          onClick={() => onSelect(item.id)}
                        >
                          {item.name}
                        </button>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{item.description || "-"}</p>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-foreground">{item.cases.length}</td>
                      <td className="px-3 py-2 text-xs text-foreground">{engineLabel(item.engine)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right align-top">
                        <Button type="button" size="sm" variant="secondary" onClick={() => onSelect(item.id)}>
                          <span>{t("evaluation.set.show")}</span>
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function EvaluationPrimaryActions({
  loading,
  hasEvaluationSet,
  hasSyntheticCases,
  hasProfiles,
  onSaveSet,
  onArchiveSet,
  onRunEvaluate,
  onCompare,
  onGenerateSynthetic,
  onSaveSynthetic,
}: {
  loading: boolean;
  hasEvaluationSet: boolean;
  hasSyntheticCases: boolean;
  hasProfiles: boolean;
  onSaveSet: () => void;
  onArchiveSet: () => void;
  onRunEvaluate: () => void;
  onCompare: () => void;
  onGenerateSynthetic: () => void;
  onSaveSynthetic: () => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="secondary" loading={loading} onClick={onSaveSet}>
        <Save size={16} aria-hidden="true" />
        <span>{t("evaluation.action.saveSet")}</span>
      </Button>
      <Button type="button" variant="secondary" loading={loading} disabled={!hasEvaluationSet} onClick={onArchiveSet}>
        <Archive size={16} aria-hidden="true" />
        <span>{t("evaluation.action.archiveSet")}</span>
      </Button>
      <Button type="button" loading={loading} onClick={onRunEvaluate}>
        <FlaskConical size={16} aria-hidden="true" />
        <span>{t("evaluation.action.run")}</span>
      </Button>
      <Button type="button" variant="secondary" loading={loading} onClick={onCompare}>
        <GitCompare size={16} aria-hidden="true" />
        <span>{t("evaluation.action.compare")}</span>
      </Button>
      <Button type="button" variant="secondary" loading={loading} onClick={onGenerateSynthetic}>
        <Wand2 size={16} aria-hidden="true" />
        <span>{t("evaluation.action.synthetic")}</span>
      </Button>
      <Button
        type="button"
        variant="secondary"
        loading={loading}
        disabled={!hasSyntheticCases || !hasProfiles}
        onClick={onSaveSynthetic}
      >
        <Wand2 size={16} aria-hidden="true" />
        <span>{t("evaluation.action.saveSynthetic")}</span>
      </Button>
    </div>
  );
}

function EvaluationResultSummary({ evaluation }: { evaluation: EvaluateData }) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-4" aria-label={t("evaluation.result.title")}>
      <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
        <FlaskConical size={18} aria-hidden="true" />
        {t("evaluation.result.title")}
      </h2>
      <div className="grid gap-3 md:grid-cols-3">
        <Metric label={t("evaluation.metric.cases")} value={String(evaluation.total_cases)} />
        <Metric label={t("evaluation.metric.executable")} value={`${Math.round(evaluation.executable_rate * 100)}%`} />
        <Metric label={t("evaluation.metric.selectOnly")} value={`${Math.round(evaluation.select_only_rate * 100)}%`} />
      </div>
      <TextList label={t("evaluation.run.findings")} items={evaluation.findings} />
    </section>
  );
}

function EvaluationCasesTable({
  cases,
  title = t("evaluation.cases.title"),
}: {
  cases: EvaluationCaseLike[];
  title?: string;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-4" aria-labelledby="evaluation-cases-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="evaluation-cases-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
          <FileText size={18} aria-hidden="true" />
          {title}
        </h2>
        <StatusBadge variant="neutral" label={t("evaluation.run.caseCount", { count: cases.length })} />
      </div>
      {cases.length === 0 ? (
        <EmptyState title={t("evaluation.cases.emptyTitle")} hint={t("evaluation.cases.emptyHint")} />
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <table className="w-full min-w-[42rem] table-fixed divide-y divide-border text-sm">
            <colgroup>
              <col className="w-[38%]" />
              <col />
            </colgroup>
            <thead className="bg-background text-xs text-muted">
              <tr>
                <th className="px-3 py-2 text-left">{t("evaluation.cases.question")}</th>
                <th className="px-3 py-2 text-left">{t("evaluation.cases.expectedSql")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {cases.map((item) => (
                <tr key={`${item.question}-${item.expected_sql}`}>
                  <td className="break-words px-3 py-2 font-medium text-foreground">{item.question}</td>
                  <td className="break-words px-3 py-2 font-mono text-sm leading-6 text-foreground">{item.expected_sql}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EvaluationRunHistory({
  records,
  onRestore,
  onExportReport,
  onExportCases,
}: {
  records: EvaluationRunRecord[];
  onRestore: (record: EvaluationRunRecord) => void;
  onExportReport: (record: EvaluationRunRecord) => void;
  onExportCases: (record: EvaluationRunRecord) => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-4" aria-labelledby="evaluation-run-history-heading">
      <h2 id="evaluation-run-history-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
        <FlaskConical size={18} aria-hidden="true" />
        {t("evaluation.run.historyTitle")}
      </h2>
      {records.length === 0 ? (
        <EmptyState title={t("evaluation.run.emptyTitle")} hint={t("evaluation.run.emptyHint")} />
      ) : (
        records.map((record) => (
          <section key={record.id} className="grid gap-3 rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold leading-6 text-foreground">
                  {record.evaluation_set_name || t("evaluation.set.new")}
                </p>
                <p className="mt-1 text-xs text-muted">
                  {engineLabel(record.engine)} / {record.profile_name || record.profile_id || "-"} /{" "}
                  {new Date(record.created_at).toLocaleString("ja-JP")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusBadge
                  variant={record.result.executable_rate === 1 ? "success" : "warning"}
                  label={t("evaluation.run.executableRate", {
                    rate: Math.round(record.result.executable_rate * 100),
                  })}
                />
                <StatusBadge
                  variant="neutral"
                  label={t("evaluation.run.caseCount", {
                    count: record.result.total_cases,
                  })}
                />
              </div>
            </div>
            <TextList label={t("evaluation.run.findings")} items={record.result.findings} />
            <label className={fieldClass}>
              <span>{t("evaluation.run.report")}</span>
              <textarea
                readOnly
                value={record.report}
                rows={5}
                className="min-h-28 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 text-foreground"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" onClick={() => onRestore(record)}>
                <FlaskConical size={15} aria-hidden="true" />
                <span>{t("evaluation.run.restore")}</span>
              </Button>
              <Button type="button" size="sm" variant="secondary" onClick={() => onExportReport(record)}>
                <Download size={15} aria-hidden="true" />
                <span>{t("evaluation.run.downloadReport")}</span>
              </Button>
              <Button type="button" size="sm" variant="secondary" onClick={() => onExportCases(record)}>
                <Download size={15} aria-hidden="true" />
                <span>{t("evaluation.run.downloadCases")}</span>
              </Button>
            </div>
          </section>
        ))
      )}
    </section>
  );
}

function AnalysisResult({ analysis }: { analysis: AnalyzeData }) {
  return (
    <section className="grid content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div>
        <p className="mb-2 text-xs font-medium text-muted">{t("evaluation.analyze.result")}</p>
        <div className="flex flex-wrap gap-2">
          <StatusBadge
            variant={analysis.safety.is_safe ? "success" : "danger"}
            label={analysis.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
          />
          <StatusBadge
            variant={analysis.safety.is_select_only ? "success" : "danger"}
            label={t("evaluation.analyze.selectOnly")}
          />
          <StatusBadge
            variant="neutral"
            label={
              analysis.safety.row_limit_applied > 0
                ? t("evaluation.analyze.rowLimit", { count: analysis.safety.row_limit_applied })
                : t("evaluation.analyze.rowLimitUnlimited")
            }
          />
        </div>
      </div>
      <p className="text-foreground">{analysis.explanation}</p>
      {analysis.safety.blocked_reason && (
        <p className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-danger">
          {analysis.safety.blocked_reason}
        </p>
      )}
      <TextList label={t("evaluation.analyze.warnings")} items={analysis.safety.warnings} />
      <SqlSnippet label={t("evaluation.analyze.executableSql")} sql={analysis.executable_sql} />
      {analysis.repaired_sql && <SqlSnippet label={t("evaluation.analyze.repairedSql")} sql={analysis.repaired_sql} />}
      <TextList label={t("evaluation.analyze.recommendations")} items={analysis.recommendations} />
      <TextList label={t("evaluation.analyze.optimization")} items={analysis.optimization_hints} />
      <div className="grid gap-3 sm:grid-cols-2">
        <CompactFact label={t("evaluation.analyze.referencedTables")} value={analysis.safety.referenced_tables.join(", ") || "-"} />
        <CompactFact label={t("evaluation.analyze.referencedColumns")} value={analysis.safety.referenced_columns.join(", ") || "-"} />
      </div>
    </section>
  );
}

function CompareResult({
  comparison,
  compareReport,
  onCopyReport,
}: {
  comparison: CompareData;
  compareReport: string;
  onCopyReport: () => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">{t("evaluation.compare.title")}</h2>
          <p className="mt-1 text-sm text-muted">{comparison.recommendation}</p>
        </div>
        <Button type="button" size="sm" variant="secondary" onClick={() => void onCopyReport()}>
          <Clipboard size={15} aria-hidden="true" />
          <span>{t("evaluation.compare.copyReport")}</span>
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <Metric label={t("evaluation.compare.engines")} value={String(comparison.results.length)} />
        <Metric label={t("evaluation.compare.safe")} value={String(comparison.results.filter((item) => item.is_safe).length)} />
        <Metric label={t("evaluation.compare.fastest")} value={fastestCompareLabel(comparison.results)} />
        <Metric label={t("evaluation.compare.errorRate")} value={`${Math.round(comparison.error_rate * 100)}%`} />
      </div>
      {comparison.results.map((result) => {
        const execution = comparison.execution_results.find((item) => item.engine === result.engine);
        return (
          <div key={result.engine} className="grid gap-3 rounded-md border border-border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant="info" label={engineLabel(result.engine)} />
              <StatusBadge
                variant={result.is_safe ? "success" : "danger"}
                label={result.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
              />
              <StatusBadge variant="neutral" label={formatElapsed(result.timing?.elapsed_ms)} />
              <StatusBadge
                variant="neutral"
                label={
                  result.row_limit > 0
                    ? `${t("evaluation.compare.rowLimit")} ${result.row_limit}`
                    : t("evaluation.compare.rowLimitUnlimited")
                }
              />
              {result.fallback_reason && <StatusBadge variant="warning" label={t("evaluation.compare.fallback")} />}
              {execution && (
                <>
                  <StatusBadge
                    variant={execution.executed ? "success" : "danger"}
                    label={execution.executed ? t("evaluation.compare.executed") : t("evaluation.compare.notExecuted")}
                  />
                  <StatusBadge
                    variant="neutral"
                    label={t("evaluation.compare.executionRows", { count: execution.row_count })}
                  />
                </>
              )}
            </div>
            <div className="grid gap-3 text-sm text-foreground lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.7fr)]">
              <SqlSnippet label={t("evaluation.compare.sql")} sql={result.executable_sql || result.sql} />
              <div className="grid content-start gap-3">
                <CompactFact label={t("evaluation.compare.rewritten")} value={result.rewritten_question || "-"} />
                <CompactFact label={t("evaluation.compare.tables")} value={result.safety?.referenced_tables.join(", ") || "-"} />
                <CompactFact label={t("evaluation.compare.columns")} value={result.safety?.referenced_columns.join(", ") || "-"} />
              </div>
            </div>
            {execution && <ExecutionPreview execution={execution} />}
            <div className="grid gap-3 md:grid-cols-2">
              <TextList label={t("evaluation.compare.warnings")} items={result.safety?.warnings ?? []} />
              <TextList label={t("evaluation.compare.recommendations")} items={result.recommendations} />
            </div>
            {(result.fallback_reason || result.safety?.blocked_reason) && (
              <div className="grid gap-1 rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
                {result.fallback_reason && <p>{result.fallback_reason}</p>}
                {result.safety?.blocked_reason && <p>{result.safety.blocked_reason}</p>}
              </div>
            )}
          </div>
        );
      })}
      <label className={fieldClass}>
        <span>{t("evaluation.compare.report")}</span>
        <textarea
          readOnly
          value={compareReport}
          rows={8}
          className="min-h-48 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm leading-6 text-foreground"
        />
      </label>
    </section>
  );
}

function CompareHistoryList({
  records,
  onRestore,
}: {
  records: CompareRecord[];
  onRestore: (record: CompareRecord) => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-card p-4" aria-labelledby="compare-history-heading">
      <h2 id="compare-history-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
        <GitCompare size={18} aria-hidden="true" />
        {t("evaluation.compare.historyTitle")}
      </h2>
      {records.length === 0 ? (
        <EmptyState title={t("evaluation.compare.historyEmptyTitle")} hint={t("evaluation.compare.historyEmptyHint")} />
      ) : (
        records.map((record) => (
          <section key={record.id} className="grid gap-3 rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold leading-6 text-foreground">{record.question}</p>
                <p className="mt-1 text-xs text-muted">
                  {record.profile_name || record.profile_id || "-"} / {new Date(record.created_at).toLocaleString("ja-JP")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusBadge
                  variant={record.comparison.error_rate === 0 ? "success" : "warning"}
                  label={t("evaluation.compare.historyErrorRate", {
                    rate: Math.round(record.comparison.error_rate * 100),
                  })}
                />
                <StatusBadge
                  variant="neutral"
                  label={t("evaluation.compare.historyEngines", {
                    count: record.comparison.results.length,
                  })}
                />
              </div>
            </div>
            <p className="text-sm leading-6 text-foreground">{record.comparison.recommendation}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" onClick={() => onRestore(record)}>
                <GitCompare size={15} aria-hidden="true" />
                <span>{t("evaluation.compare.historyRestore")}</span>
              </Button>
            </div>
          </section>
        ))
      )}
    </section>
  );
}

function CsvImportButton({ onImport }: { onImport: (file: File | undefined) => void | Promise<void> }) {
  return (
    <span className="relative inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md border border-border bg-card px-4 text-sm font-medium text-foreground hover:bg-background focus-within:ring-2 focus-within:ring-ring/40">
      <Upload size={16} aria-hidden="true" />
      <span>{t("evaluation.action.importCases")}</span>
      <input
        type="file"
        accept=".csv,.tsv,.txt"
        className="absolute inset-0 cursor-pointer opacity-0"
        aria-label={t("evaluation.action.importCases")}
        onChange={(event) => {
          void onImport(event.currentTarget.files?.[0]);
          event.currentTarget.value = "";
        }}
      />
    </span>
  );
}

function ReverseResult({
  reverse,
  useGlossary,
}: {
  reverse: ReverseSqlData | null;
  useGlossary: boolean;
}) {
  if (!reverse) {
    return <EmptyState title={t("sqlToQuestion.result.emptyTitle")} hint={t("sqlToQuestion.result.emptyHint")} />;
  }
  return (
    <section className="grid content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="neutral" label={reverse.source ?? "deterministic"} />
        {useGlossary && <StatusBadge variant="info" label={t("sqlToQuestion.glossaryApplied")} />}
      </div>
      <CompactFact label={t("sqlToQuestion.result.question")} value={reverse.question} />
      <CompactFact label={t("sqlToQuestion.result.explanation")} value={reverse.explanation} />
      {reverse.logical_structure && <SqlSnippet label={t("sqlToQuestion.structure.title")} sql={reverse.logical_structure} />}
      <CompactFact label={t("sqlToQuestion.result.tables")} value={reverse.referenced_tables.join(", ") || "-"} />
      <TextList label={t("sqlToQuestion.result.steps")} items={reverse.logical_steps ?? []} />
      <TextList label={t("sqlToQuestion.result.warnings")} items={reverse.warnings ?? []} />
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
    </div>
  );
}

function profilePayloadWithSynthetic(
  profile: Nl2SqlProfile,
  synthetic: SyntheticCasesData
): ProfileUpsertPayload {
  const existingKeys = new Set(
    profile.few_shot_examples.map((item) => `${item.question ?? ""}\n${item.sql ?? item.expected_sql ?? ""}`)
  );
  const additions = synthetic.cases
    .map((item) => ({ question: item.question, sql: item.expected_sql }))
    .filter((item) => {
      const key = `${item.question}\n${item.sql}`;
      if (existingKeys.has(key)) return false;
      existingKeys.add(key);
      return true;
    });
  return {
    name: profile.name,
    description: profile.description,
    allowed_tables: profile.allowed_tables,
    allowed_views: profile.allowed_views ?? [],
    glossary: profile.glossary,
    sql_rules: [],
    default_row_limit: profile.default_row_limit,
    safety_policy: profile.safety_policy,
    few_shot_examples: [...profile.few_shot_examples, ...additions],
    select_ai_config: profile.select_ai_config,
  };
}

function evaluationCases(synthetic: SyntheticCasesData | null): EvaluationCaseLike[] {
  return synthetic?.cases ?? DEFAULT_CASES;
}

function casesForProfile(cases: EvaluationCaseLike[], profileId: string): SyntheticCase[] {
  return cases.map((item) => ({
    question: item.question,
    expected_sql: item.expected_sql,
    profile_id: item.profile_id || profileId,
  }));
}

function downloadEvaluationCasesCsv(cases: EvaluationCaseLike[], filename = "nl2sql_evaluation_cases.csv") {
  const rows = cases.map((item) => `${escapeCsv(item.question)},${escapeCsv(item.expected_sql)}`);
  downloadTextFile(["QUESTION,EXPECTED_SQL", ...rows].join("\n"), filename, "text/csv;charset=utf-8");
}

function downloadTextFile(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function evaluationRunFileStem(record: EvaluationRunRecord) {
  const timestamp = record.created_at.replace(/\D/g, "").slice(0, 14) || "unknown";
  const id = record.id.replace(/[^a-zA-Z0-9_-]+/g, "_").slice(0, 12) || "run";
  return `nl2sql_evaluation_run_${timestamp}_${id}`;
}

function buildEvaluationRunReport(record: EvaluationRunRecord) {
  const lines = [
    "NL2SQL deterministic evaluation",
    `Evaluation set: ${record.evaluation_set_name || "-"}`,
    `Profile: ${record.profile_name || record.profile_id || "-"}`,
    `Engine: ${engineLabel(record.engine)}`,
    `Created at: ${record.created_at}`,
    `Cases: ${record.result.total_cases}`,
    `Executable rate: ${Math.round(record.result.executable_rate * 100)}%`,
    `SELECT-only rate: ${Math.round(record.result.select_only_rate * 100)}%`,
  ];
  return [
    ...lines,
    ...(record.result.findings.length > 0
      ? ["", "Findings:", ...record.result.findings.map((item) => `- ${item}`)]
      : []),
  ].join("\n");
}

function escapeCsv(value: string) {
  const normalized = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (/[",\n]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`;
  }
  return normalized;
}

function parseEvaluationCasesCsv(text: string, profileId: string) {
  const rows = parseDelimited(text);
  const [header = [], ...body] = rows;
  const normalized = header.map((item) => item.trim().toUpperCase());
  const questionIndex = normalized.indexOf("QUESTION");
  const expectedSqlIndex =
    normalized.indexOf("EXPECTED_SQL") >= 0
      ? normalized.indexOf("EXPECTED_SQL")
      : normalized.indexOf("SQL");
  if (questionIndex < 0 || expectedSqlIndex < 0) {
    throw new Error(t("evaluation.synthetic.importInvalid"));
  }
  return body
    .map((row) => ({
      question: row[questionIndex]?.trim() ?? "",
      expected_sql: row[expectedSqlIndex]?.trim() ?? "",
      profile_id: profileId,
    }))
    .filter((item) => item.question && item.expected_sql);
}

function parseDelimited(text: string) {
  const delimiter = text.includes("\t") ? "\t" : ",";
  const rows: string[][] = [];
  let current = "";
  let row: string[] = [];
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && quoted && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === delimiter && !quoted) {
      row.push(current);
      current = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(current);
      rows.push(row);
      row = [];
      current = "";
    } else {
      current += char;
    }
  }
  row.push(current);
  rows.push(row);
  return rows.filter((items) => items.some((item) => item.trim()));
}

function fastestCompareLabel(results: PreviewData[]): string {
  const safeResults = results.filter((item) => item.is_safe);
  if (safeResults.length === 0) return "-";
  const fastest = safeResults.reduce((current, candidate) => {
    const currentElapsed = current.timing?.elapsed_ms ?? Number.MAX_SAFE_INTEGER;
    const candidateElapsed = candidate.timing?.elapsed_ms ?? Number.MAX_SAFE_INTEGER;
    return candidateElapsed < currentElapsed ? candidate : current;
  });
  return `${engineLabel(fastest.engine)} / ${formatElapsed(fastest.timing?.elapsed_ms)}`;
}

function buildCompareReport(comparison: CompareData): string {
  const lines = [
    "NL2SQL engine comparison",
    `Question: ${comparison.question}`,
    `Recommendation: ${comparison.recommendation}`,
    `Error rate: ${Math.round(comparison.error_rate * 100)}%`,
    "",
  ];
  for (const result of comparison.results) {
    const execution = comparison.execution_results.find((item) => item.engine === result.engine);
    lines.push(
      `## ${engineLabel(result.engine)}`,
      `Safe: ${result.is_safe ? "yes" : "no"}`,
      `Elapsed: ${formatElapsed(result.timing?.elapsed_ms)}`,
      `Row limit: ${result.row_limit || "none"}`,
      `Tables: ${result.safety?.referenced_tables.join(", ") || "-"}`,
      `Columns: ${result.safety?.referenced_columns.join(", ") || "-"}`,
      `Execution: ${execution?.executed ? `${execution.row_count} rows` : execution?.error_message || "not executed"}`,
      `SQL: ${oneLine(result.executable_sql || result.sql)}`,
      ""
    );
  }
  return lines.join("\n").trim();
}

function oneLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function SqlSnippet({ label, sql }: { label: string; sql: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted">{label}</p>
      <pre className="max-h-44 overflow-auto rounded-md bg-code p-3 text-sm leading-6 text-code-fg">
        <code>{sql || "-"}</code>
      </pre>
    </div>
  );
}

function ExecutionPreview({ execution }: { execution: CompareExecutionData }) {
  if (!execution.executed) {
    return (
      <section className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
        <p className="text-xs font-medium text-danger">
          {t("evaluation.compare.executionError")}
        </p>
        <p className="mt-1">{execution.error_message || "-"}</p>
      </section>
    );
  }
  const results = execution.results;
  if (!results || results.rows.length === 0) {
    return (
      <section className="rounded-md border border-border bg-background px-3 py-2 text-sm text-muted">
        <p className="text-xs font-medium text-muted">
          {t("evaluation.compare.executionResult")}
        </p>
        <p className="mt-1">-</p>
      </section>
    );
  }
  const visibleRows = results.rows.slice(0, 2);
  return (
    <section className="grid gap-2 rounded-md border border-border bg-background p-3">
      <p className="text-xs font-medium text-muted">
        {t("evaluation.compare.executionResult")}
      </p>
      <div className="overflow-auto rounded-md border border-border bg-card">
        <table className="min-w-full divide-y divide-border text-xs">
          <thead className="bg-background">
            <tr>
              {results.columns.map((column) => (
                <th key={column} scope="col" className="whitespace-nowrap px-2 py-1 text-left font-semibold text-foreground">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/70">
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {results.columns.map((column) => (
                  <td key={column} className="whitespace-nowrap px-2 py-1 text-foreground">
                    {formatCompareCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatCompareCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function TextList({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted">{label}</p>
      {items.length > 0 ? (
        <ul className="grid gap-1 text-foreground">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="text-muted">-</p>
      )}
    </div>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card px-3 py-2">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words font-mono text-xs text-foreground">{value}</p>
    </div>
  );
}
