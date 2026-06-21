import { useEffect, useState } from "react";
import {
  Archive,
  Clipboard,
  Download,
  FileText,
  FlaskConical,
  GitCompare,
  Save,
  ShieldCheck,
  Upload,
  Wand2,
} from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { DEFAULT_ANALYZE_SQL, sqlAnalyzePayload } from "../analysisState";
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

const DEFAULT_CASES = [
  { question: "請求金額を一覧で見たい", expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES" },
  { question: "顧客の地域を確認したい", expected_sql: "SELECT REGION FROM CUSTOMERS" },
];

type EvaluationCaseLike = {
  question: string;
  expected_sql: string;
  profile_id?: string;
};

export function EvaluationPage() {
  const [question, setQuestion] = useState("今月の請求金額が大きい取引先を表示して");
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
  const [analysisRowLimit, setAnalysisRowLimit] = useState(100);
  const [analysis, setAnalysis] = useState<AnalyzeData | null>(null);
  const [reverseSql, setReverseSql] = useState("SELECT TOTAL_AMOUNT FROM INVOICES");
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [loading, setLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [message, setMessage] = useState("");

  const loadCompareHistory = async () => {
    try {
      const data = await apiGet<CompareHistoryData>("/api/nl2sql/compare-history?limit=5");
      setCompareHistory(data.items);
    } catch {
      setCompareHistory([]);
    }
  };

  const loadProfiles = async () => {
    try {
      const data = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles");
      setProfiles(data);
      if (data.length > 0 && !data.some((profile) => profile.id === profileId)) {
        setProfileId(data[0].id);
      }
    } catch {
      setProfiles([]);
    }
  };

  const loadEvaluationSets = async () => {
    try {
      const data = await apiGet<EvaluationSetsData>("/api/nl2sql/evaluation-sets");
      setEvaluationSets(data.items);
    } catch {
      setEvaluationSets([]);
    }
  };

  const loadEvaluationRuns = async () => {
    try {
      const data = await apiGet<EvaluationRunsData>("/api/nl2sql/evaluation-runs?limit=5");
      setEvaluationRuns(data.items);
    } catch {
      setEvaluationRuns([]);
    }
  };

  useEffect(() => {
    void loadCompareHistory();
    void loadProfiles();
    void loadEvaluationSets();
    void loadEvaluationRuns();
  }, []);

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
      setMessage(t("evaluation.synthetic.saved"));
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
    setMessage(t("evaluation.set.loaded"));
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
      setMessage(t("evaluation.set.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.set.saveError"));
    } finally {
      setLoading(false);
    }
  };

  const archiveEvaluationSet = async () => {
    if (!evaluationSetId) return;
    setLoading(true);
    setMessage("");
    try {
      await apiPost<EvaluationSet>(`/api/nl2sql/evaluation-sets/${evaluationSetId}/archive`);
      setEvaluationSetId("");
      setEvaluationSetName(t("evaluation.set.defaultName"));
      setEvaluationSetDescription("");
      await loadEvaluationSets();
      setMessage(t("evaluation.set.archived"));
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
    setMessage(t("evaluation.run.historyRestored"));
  };

  const exportEvaluationRunReport = (record: EvaluationRunRecord) => {
    downloadTextFile(
      record.report || buildEvaluationRunReport(record),
      `${evaluationRunFileStem(record)}.md`,
      "text/markdown;charset=utf-8"
    );
    setMessage(t("evaluation.run.reportDownloaded"));
  };

  const exportEvaluationRunCases = (record: EvaluationRunRecord) => {
    downloadEvaluationCasesCsv(record.cases, `${evaluationRunFileStem(record)}_cases.csv`);
    setMessage(t("evaluation.run.casesDownloaded"));
  };

  const exportEvaluationCases = () => {
    downloadEvaluationCasesCsv(evaluationCases(synthetic));
  };

  const importEvaluationCases = async (file: File | undefined) => {
    if (!file) return;
    setMessage("");
    try {
      const text = await file.text();
      const cases = parseEvaluationCasesCsv(text, profileId);
      if (cases.length === 0) {
        setMessage(t("evaluation.synthetic.importEmpty"));
        return;
      }
      setSynthetic({ cases });
      setEvaluation(null);
      setMessage(t("evaluation.synthetic.imported", { count: cases.length }));
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
    setMessage(t("evaluation.compare.historyRestored"));
  };

  const compareReport = comparison ? buildCompareReport(comparison) : "";

  const copyCompareReport = async () => {
    if (!compareReport) return;
    try {
      await navigator.clipboard.writeText(compareReport);
      setMessage(t("evaluation.compare.reportCopied"));
    } catch {
      setMessage(t("evaluation.compare.reportCopyError"));
    }
  };

  const analyzeSql = async () => {
    const sql = analysisSql.trim();
    if (!sql) return;
    setAnalysisLoading(true);
    setMessage("");
    try {
      setAnalysis(await apiPost<AnalyzeData>("/api/nl2sql/analyze", sqlAnalyzePayload(sql, analysisRowLimit)));
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
      setReverse(await apiPost<ReverseSqlData>("/api/nl2sql/reverse", { sql }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.runner.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-[1fr_14rem_14rem]">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.set.label")}</span>
              <select
                value={evaluationSetId}
                onChange={(event) => selectEvaluationSet(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                <option value="">{t("evaluation.set.new")}</option>
                {evaluationSets.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.set.name")}</span>
              <input
                value={evaluationSetName}
                onChange={(event) => setEvaluationSetName(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.set.description")}</span>
              <input
                value={evaluationSetDescription}
                onChange={(event) => setEvaluationSetDescription(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.question.label")}</span>
              <input
                value={question}
                onChange={(event) => setQuestion(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("nl2sql.engine.label")}</span>
              <select
                value={engine}
                onChange={(event) => setEngine(event.currentTarget.value as Nl2SqlEngine)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                <option value="auto">{t("nl2sql.engine.auto")}</option>
                <option value="select_ai_agent">{t("nl2sql.engine.agent")}</option>
                <option value="select_ai">{t("nl2sql.engine.selectAi")}</option>
                <option value="enterprise_ai_direct">{t("nl2sql.engine.direct")}</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.profile.label")}</span>
              <select
                value={profileId}
                onChange={(event) => setProfileId(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex flex-wrap gap-2 md:col-span-3">
              <Button type="button" variant="secondary" loading={loading} onClick={() => void saveEvaluationSet()}>
                <Save size={16} aria-hidden="true" />
                <span>{t("evaluation.action.saveSet")}</span>
              </Button>
              <Button
                type="button"
                variant="secondary"
                loading={loading}
                disabled={!evaluationSetId}
                onClick={() => void archiveEvaluationSet()}
              >
                <Archive size={16} aria-hidden="true" />
                <span>{t("evaluation.action.archiveSet")}</span>
              </Button>
              <Button type="button" loading={loading} onClick={() => void runEvaluate()}>
                <FlaskConical size={16} aria-hidden="true" />
                <span>{t("evaluation.action.run")}</span>
              </Button>
              <Button type="button" variant="secondary" loading={loading} onClick={() => void compare()}>
                <GitCompare size={16} aria-hidden="true" />
                <span>{t("evaluation.action.compare")}</span>
              </Button>
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
              <Button type="button" variant="secondary" onClick={exportEvaluationCases}>
                <Download size={16} aria-hidden="true" />
                <span>{t("evaluation.action.exportCases")}</span>
              </Button>
              <span className="relative inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                <Upload size={16} aria-hidden="true" />
                <span>{t("evaluation.action.importCases")}</span>
                <input
                  type="file"
                  accept=".csv,.tsv,.txt"
                  className="absolute inset-0 cursor-pointer opacity-0"
                  aria-label={t("evaluation.action.importCases")}
                  onChange={(event) => {
                    void importEvaluationCases(event.currentTarget.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.analyze.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("evaluation.analyze.sql")}</span>
                <textarea
                  value={analysisSql}
                  onChange={(event) => setAnalysisSql(event.currentTarget.value)}
                  rows={7}
                  className="min-h-44 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-[minmax(9rem,12rem)_auto] sm:items-end">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("nl2sql.rowLimit.label")}</span>
                  <input
                    type="number"
                    min={1}
                    max={5000}
                    value={analysisRowLimit}
                    onChange={(event) => setAnalysisRowLimit(Number(event.currentTarget.value))}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
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

            {analysis && (
              <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div>
                  <p className="mb-2 text-xs font-medium text-slate-500">
                    {t("evaluation.analyze.result")}
                  </p>
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
                      label={t("evaluation.analyze.rowLimit", {
                        count: analysis.safety.row_limit_applied,
                      })}
                    />
                  </div>
                </div>
                <p className="text-slate-700">{analysis.explanation}</p>
                {analysis.safety.blocked_reason && (
                  <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                    {analysis.safety.blocked_reason}
                  </p>
                )}
                <TextList label={t("evaluation.analyze.warnings")} items={analysis.safety.warnings} />
                <SqlSnippet label={t("evaluation.analyze.executableSql")} sql={analysis.executable_sql} />
                {analysis.repaired_sql && (
                  <SqlSnippet label={t("evaluation.analyze.repairedSql")} sql={analysis.repaired_sql} />
                )}
                <TextList label={t("evaluation.analyze.recommendations")} items={analysis.recommendations} />
                <TextList label={t("evaluation.analyze.optimization")} items={analysis.optimization_hints} />
                <div className="grid gap-3 sm:grid-cols-2">
                  <CompactFact
                    label={t("evaluation.analyze.referencedTables")}
                    value={analysis.safety.referenced_tables.join(", ") || "-"}
                  />
                  <CompactFact
                    label={t("evaluation.analyze.referencedColumns")}
                    value={analysis.safety.referenced_columns.join(", ") || "-"}
                  />
                </div>
              </section>
            )}
          </CardContent>
        </Card>

        {evaluation && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.result.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <Metric label={t("evaluation.metric.cases")} value={String(evaluation.total_cases)} />
              <Metric label={t("evaluation.metric.executable")} value={`${Math.round(evaluation.executable_rate * 100)}%`} />
              <Metric label={t("evaluation.metric.selectOnly")} value={`${Math.round(evaluation.select_only_rate * 100)}%`} />
            </CardContent>
          </Card>
        )}

        {evaluationRuns.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.run.historyTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {evaluationRuns.map((record) => (
                <section key={record.id} className="grid gap-3 rounded-md border border-slate-200 p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold leading-6 text-slate-900">
                        {record.evaluation_set_name || t("evaluation.set.new")}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
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
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("evaluation.run.report")}</span>
                    <textarea
                      readOnly
                      value={record.report}
                      rows={5}
                      className="min-h-28 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="secondary" onClick={() => restoreEvaluationRun(record)}>
                      <FlaskConical size={15} aria-hidden="true" />
                      <span>{t("evaluation.run.restore")}</span>
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => exportEvaluationRunReport(record)}>
                      <Download size={15} aria-hidden="true" />
                      <span>{t("evaluation.run.downloadReport")}</span>
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => exportEvaluationRunCases(record)}>
                      <Download size={15} aria-hidden="true" />
                      <span>{t("evaluation.run.downloadCases")}</span>
                    </Button>
                  </div>
                </section>
              ))}
            </CardContent>
          </Card>
        )}

        {comparison && (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>{t("evaluation.compare.title")}</CardTitle>
                  <p className="mt-1 text-sm text-slate-600">{comparison.recommendation}</p>
                </div>
                <Button type="button" size="sm" variant="secondary" onClick={() => void copyCompareReport()}>
                  <Clipboard size={15} aria-hidden="true" />
                  <span>{t("evaluation.compare.copyReport")}</span>
                </Button>
              </div>
            </CardHeader>
            <CardContent className="grid gap-3">
              <div className="grid gap-3 md:grid-cols-4">
                <Metric
                  label={t("evaluation.compare.engines")}
                  value={String(comparison.results.length)}
                />
                <Metric
                  label={t("evaluation.compare.safe")}
                  value={String(comparison.results.filter((item) => item.is_safe).length)}
                />
                <Metric
                  label={t("evaluation.compare.fastest")}
                  value={fastestCompareLabel(comparison.results)}
                />
                <Metric
                  label={t("evaluation.compare.errorRate")}
                  value={`${Math.round(comparison.error_rate * 100)}%`}
                />
              </div>
              {comparison.results.map((result) => {
                const execution = comparison.execution_results.find(
                  (item) => item.engine === result.engine
                );
                return (
                <div key={result.engine} className="grid gap-3 rounded-md border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge variant="info" label={engineLabel(result.engine)} />
                    <StatusBadge
                      variant={result.is_safe ? "success" : "danger"}
                      label={result.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                    />
                    <StatusBadge
                      variant="neutral"
                      label={formatElapsed(result.timing?.elapsed_ms)}
                    />
                    <StatusBadge
                      variant="neutral"
                      label={`${t("evaluation.compare.rowLimit")} ${result.row_limit}`}
                    />
                    {result.fallback_reason && (
                      <StatusBadge variant="warning" label={t("evaluation.compare.fallback")} />
                    )}
                    {execution && (
                      <>
                        <StatusBadge
                          variant={execution.executed ? "success" : "danger"}
                          label={
                            execution.executed
                              ? t("evaluation.compare.executed")
                              : t("evaluation.compare.notExecuted")
                          }
                        />
                        <StatusBadge
                          variant="neutral"
                          label={t("evaluation.compare.executionRows", {
                            count: execution.row_count,
                          })}
                        />
                      </>
                    )}
                  </div>
                  <div className="grid gap-3 text-sm text-slate-700 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.7fr)]">
                    <SqlSnippet
                      label={t("evaluation.compare.sql")}
                      sql={result.executable_sql || result.sql}
                    />
                    <div className="grid content-start gap-3">
                      <CompactFact
                        label={t("evaluation.compare.rewritten")}
                        value={result.rewritten_question || "-"}
                      />
                      <CompactFact
                        label={t("evaluation.compare.tables")}
                        value={result.safety?.referenced_tables.join(", ") || "-"}
                      />
                      <CompactFact
                        label={t("evaluation.compare.columns")}
                        value={result.safety?.referenced_columns.join(", ") || "-"}
                      />
                    </div>
                  </div>
                  {execution && <ExecutionPreview execution={execution} />}
                  <div className="grid gap-3 md:grid-cols-2">
                    <TextList
                      label={t("evaluation.compare.warnings")}
                      items={result.safety?.warnings ?? []}
                    />
                    <TextList
                      label={t("evaluation.compare.recommendations")}
                      items={result.recommendations}
                    />
                  </div>
                  {(result.fallback_reason || result.safety?.blocked_reason) && (
                    <div className="grid gap-1 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                      {result.fallback_reason && <p>{result.fallback_reason}</p>}
                      {result.safety?.blocked_reason && <p>{result.safety.blocked_reason}</p>}
                    </div>
                  )}
                </div>
                );
              })}
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("evaluation.compare.report")}</span>
                <textarea
                  readOnly
                  value={compareReport}
                  rows={8}
                  className="min-h-48 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800"
                />
              </label>
            </CardContent>
          </Card>
        )}

        {compareHistory.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.compare.historyTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {compareHistory.map((record) => (
                <section key={record.id} className="grid gap-3 rounded-md border border-slate-200 p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold leading-6 text-slate-900">{record.question}</p>
                      <p className="mt-1 text-xs text-slate-500">
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
                  <p className="text-sm leading-6 text-slate-700">{record.comparison.recommendation}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="secondary" onClick={() => restoreComparison(record)}>
                      <GitCompare size={15} aria-hidden="true" />
                      <span>{t("evaluation.compare.historyRestore")}</span>
                    </Button>
                  </div>
                </section>
              ))}
            </CardContent>
          </Card>
        )}

        {synthetic && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.synthetic.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              {synthetic.cases.map((item) => (
                <div key={item.question} className="rounded-md border border-slate-200 p-3 text-sm">
                  <p className="font-medium text-slate-900">{item.question}</p>
                  <code className="mt-1 block text-xs text-slate-600">{item.expected_sql}</code>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.reverse.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[1fr_minmax(18rem,0.8fr)]">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.reverse.sql")}</span>
              <textarea
                value={reverseSql}
                onChange={(event) => setReverseSql(event.currentTarget.value)}
                rows={6}
                className="min-h-36 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <div className="grid content-start gap-3">
              <Button
                type="button"
                variant="secondary"
                loading={reverseLoading}
                disabled={!reverseSql.trim()}
                onClick={() => void reverseExplain()}
              >
                <FileText size={16} aria-hidden="true" />
                <span>{t("evaluation.action.reverse")}</span>
              </Button>
              {reverse && (
                <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.question")}
                    </p>
                    <p className="mt-1 font-semibold text-slate-900">{reverse.question}</p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.explanation")}
                    </p>
                    <p className="mt-1 text-slate-700">{reverse.explanation}</p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.tables")}
                    </p>
                    <p className="mt-1 font-mono text-xs text-slate-700">
                      {reverse.referenced_tables.join(", ") || "-"}
                    </p>
                  </div>
                </section>
              )}
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
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
    glossary: profile.glossary,
    sql_rules: profile.sql_rules,
    default_row_limit: profile.default_row_limit,
    safety_policy: profile.safety_policy,
    few_shot_examples: [...profile.few_shot_examples, ...additions],
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
      `Row limit: ${result.row_limit}`,
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
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      <pre className="max-h-44 overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-50">
        <code>{sql || "-"}</code>
      </pre>
    </div>
  );
}

function ExecutionPreview({ execution }: { execution: CompareExecutionData }) {
  if (!execution.executed) {
    return (
      <section className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        <p className="text-xs font-medium text-red-700">
          {t("evaluation.compare.executionError")}
        </p>
        <p className="mt-1">{execution.error_message || "-"}</p>
      </section>
    );
  }
  const results = execution.results;
  if (!results || results.rows.length === 0) {
    return (
      <section className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
        <p className="text-xs font-medium text-slate-500">
          {t("evaluation.compare.executionResult")}
        </p>
        <p className="mt-1">-</p>
      </section>
    );
  }
  const visibleRows = results.rows.slice(0, 2);
  return (
    <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
      <p className="text-xs font-medium text-slate-500">
        {t("evaluation.compare.executionResult")}
      </p>
      <div className="overflow-auto rounded-md border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-xs">
          <thead className="bg-slate-50">
            <tr>
              {results.columns.map((column) => (
                <th key={column} scope="col" className="whitespace-nowrap px-2 py-1 text-left font-semibold text-slate-700">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {results.columns.map((column) => (
                  <td key={column} className="whitespace-nowrap px-2 py-1 text-slate-700">
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
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      {items.length > 0 ? (
        <ul className="grid gap-1 text-slate-700">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="text-slate-500">-</p>
      )}
    </div>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white px-3 py-2">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words font-mono text-xs text-slate-700">{value}</p>
    </div>
  );
}
