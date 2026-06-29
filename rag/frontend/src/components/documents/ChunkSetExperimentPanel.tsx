"use client";

import { ArrowUpCircle, FlaskConical, Loader2, Search as SearchIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CitationCard, scoreMaximaForCitations } from "@/components/search/CitationCard";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  api,
  type ChunkSetExperimentRequest,
  type DocumentChunkSet,
  type ParserExtractionExperimentRequest,
  type RetrievedChunk,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import {
  ingestionJobIsActive,
  useCreateChunkSetExperiment,
  useCreateParserExtractionExperiment,
  useDocumentChunkSets,
  useIngestionJob,
  usePromoteChunkSetExperiment,
} from "@/lib/queries";
import { parserBackendLabel } from "@/lib/source-profile-labels";
import { toast } from "@/lib/toast";

const STRATEGIES = [
  "structure_aware",
  "recursive_character",
  "sentence_window",
  "hierarchical_parent_child",
  "markdown_heading",
  "page_level",
  "fixed_size",
  "fixed_delimiter",
] as const;

/** 再抽出実験で選べる文書解析(parser)backend。廃止(local)・別名(enterprise_ai_vlm)は除く。 */
const PARSER_BACKENDS = [
  "docling",
  "marker",
  "unstructured",
  "unlimited_ocr",
  "mineru",
  "dots_ocr",
  "glm_ocr",
  "oci_genai_vision",
  "oci_document_understanding",
] as const;

/** 再抽出実験で選べるファイル準備プロファイル。 */
const PREPROCESS_PROFILES = [
  "passthrough",
  "office_to_pdf",
  "pdf_to_page_images",
  "csv_to_json",
  "excel_to_json",
  "url_to_markdown",
  "image_enhance",
  "pii_redact",
] as const;

const PROBE_TOP_K = 5;

const inputClass =
  "h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring";

type ProbeResult = { serving: RetrievedChunk[]; candidate: RetrievedChunk[] };

/**
 * 文書詳細の「別レシピを試す」実験パネル。
 *
 * 既存抽出を別 chunking レシピで再 chunk した候補 chunk_set を materialize し、
 * 配信中(serving)と候補を同じプローブ検索で横並び比較して、良ければ昇格する。
 * 候補は配信に載らない(is_serving=0)ので検索結果には影響しない。
 */
export function ChunkSetExperimentPanel({ documentId }: { documentId: string }) {
  const chunkSets = useDocumentChunkSets(documentId);
  const createExperiment = useCreateChunkSetExperiment();
  const createReparse = useCreateParserExtractionExperiment();
  const promoteExperiment = usePromoteChunkSetExperiment();
  const confirm = useConfirm();

  const serving = useMemo(
    () => (chunkSets.data ?? []).find((chunkSet) => chunkSet.is_serving) ?? null,
    [chunkSets.data]
  );
  const candidates = useMemo(
    () => (chunkSets.data ?? []).filter((chunkSet) => !chunkSet.is_serving),
    [chunkSets.data]
  );

  const [strategy, setStrategy] = useState("");
  const [chunkSize, setChunkSize] = useState("");
  const [overlap, setOverlap] = useState("");
  const [formError, setFormError] = useState("");

  const [probeCandidateId, setProbeCandidateId] = useState("");
  const [probeQuery, setProbeQuery] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState("");

  // parser/前処理 再抽出(非同期ジョブ)
  const [reparseProfile, setReparseProfile] = useState("");
  const [reparseParser, setReparseParser] = useState("");
  const [reparseError, setReparseError] = useState("");
  const [reparseJobId, setReparseJobId] = useState<string | null>(null);
  const reparseJob = useIngestionJob(reparseJobId);
  const reparseJobData = reparseJob.data;
  const reparseRefetchChunkSets = chunkSets.refetch;

  // ジョブが終端に達したら chunk-sets を再取得して候補を反映する。
  useEffect(() => {
    if (!reparseJobId || !reparseJobData || reparseJobData.id !== reparseJobId) return;
    const status = reparseJobData.status;
    if (status === "SUCCEEDED") {
      toast.success(t("documents.experiment.reparse.toast.done"));
      void reparseRefetchChunkSets();
      setReparseJobId(null);
    } else if (status === "FAILED" || status === "CANCELLED" || status === "SKIPPED") {
      setReparseError(
        reparseJobData.error_message ||
          reparseJobData.skip_reason ||
          t("documents.experiment.reparse.error")
      );
      setReparseJobId(null);
    }
  }, [reparseJobId, reparseJobData, reparseRefetchChunkSets]);

  const reparseRunning = reparseJobId != null && ingestionJobIsActive(reparseJobData?.status);

  const activeCandidate =
    candidates.find((candidate) => candidate.chunk_set_id === probeCandidateId) ?? candidates[0];

  const strategyOptions: SelectFieldOption[] = [
    { value: "", label: t("documents.experiment.form.strategyDefault") },
    ...STRATEGIES.map((name) => ({
      value: name,
      label: t(`settings.chunking.strategy.${name}` as I18nKey),
    })),
  ];

  const handleCreate = () => {
    const body: ChunkSetExperimentRequest = {};
    if (strategy) body.chunking_strategy = strategy;
    if (chunkSize.trim()) body.chunk_size = Number(chunkSize);
    if (overlap.trim()) body.chunk_overlap = Number(overlap);
    if (Object.keys(body).length === 0) {
      setFormError(t("documents.experiment.form.required"));
      return;
    }
    setFormError("");
    createExperiment.mutate(
      { id: documentId, body },
      {
        onSuccess: (created) => {
          toast.success(t("documents.experiment.toast.created"));
          setStrategy("");
          setChunkSize("");
          setOverlap("");
          setProbeCandidateId(created.chunk_set_id);
        },
        onError: (error) =>
          setFormError(
            error instanceof ApiError ? error.message : t("documents.experiment.form.error")
          ),
      }
    );
  };

  const preprocessOptions: SelectFieldOption[] = [
    { value: "", label: t("documents.experiment.reparse.unchanged") },
    ...PREPROCESS_PROFILES.map((name) => ({
      value: name,
      label: t(`settings.preprocess.profile.${name}` as I18nKey),
    })),
  ];
  const parserOptions: SelectFieldOption[] = [
    { value: "", label: t("documents.experiment.reparse.unchanged") },
    ...PARSER_BACKENDS.map((name) => ({ value: name, label: parserBackendLabel(name) })),
  ];

  const handleReparse = () => {
    const body: ParserExtractionExperimentRequest = {};
    if (reparseProfile) body.preprocess_profile = reparseProfile;
    if (reparseParser) body.parser_adapter_backend = reparseParser;
    if (Object.keys(body).length === 0) {
      setReparseError(t("documents.experiment.reparse.required"));
      return;
    }
    setReparseError("");
    createReparse.mutate(
      { id: documentId, body },
      {
        onSuccess: (job) => {
          toast.success(t("documents.experiment.reparse.toast.enqueued"));
          setReparseProfile("");
          setReparseParser("");
          setReparseJobId(job.id);
        },
        onError: (error) =>
          setReparseError(
            error instanceof ApiError ? error.message : t("documents.experiment.reparse.error")
          ),
      }
    );
  };

  const runProbe = async () => {
    const query = probeQuery.trim();
    if (!query || !serving || !activeCandidate || probing) return;
    setProbing(true);
    setProbeError("");
    setProbe(null);
    try {
      const [servingResult, candidateResult] = await Promise.all([
        api.search({
          query,
          top_k: PROBE_TOP_K,
          filters: { document_id: documentId, chunk_set_id: serving.chunk_set_id },
        }),
        api.search({
          query,
          top_k: PROBE_TOP_K,
          filters: { document_id: documentId, chunk_set_id: activeCandidate.chunk_set_id },
        }),
      ]);
      setProbe({ serving: servingResult.citations, candidate: candidateResult.citations });
    } catch (error) {
      setProbeError(
        error instanceof ApiError ? error.message : t("documents.experiment.compare.error")
      );
    } finally {
      setProbing(false);
    }
  };

  const handlePromote = async (candidate: DocumentChunkSet) => {
    const ok = await confirm({
      title: t("documents.experiment.promote.confirmTitle"),
      description: t("documents.experiment.promote.confirmDescription"),
      confirmLabel: t("documents.experiment.promote.confirm"),
      tone: "warning",
    });
    if (!ok) return;
    promoteExperiment.mutate(
      { id: documentId, chunkSetId: candidate.chunk_set_id },
      {
        onSuccess: () => {
          toast.success(t("documents.experiment.toast.promoted"));
          setProbe(null);
          setProbeCandidateId("");
        },
        onError: (error) =>
          toast.error(
            error instanceof ApiError ? error.message : t("documents.experiment.promote.error")
          ),
      }
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="size-4 text-muted" aria-hidden />
          {t("documents.experiment.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted">{t("documents.experiment.description")}</p>

        {chunkSets.isPending ? (
          <div
            className="h-20 animate-pulse rounded-md bg-background"
            role="status"
            aria-label={t("documents.experiment.loading")}
          />
        ) : chunkSets.isError ? (
          <ErrorState
            message={t("documents.experiment.loadError")}
            onRetry={() => void chunkSets.refetch()}
          />
        ) : !serving ? (
          <EmptyState title={t("documents.experiment.noServing")} />
        ) : (
          <div className="space-y-5">
            {/* 現在の構成: 配信中 + 候補 */}
            <ul className="space-y-2" aria-label={t("documents.experiment.current")}>
              <ChunkSetRow chunkSet={serving} />
              {candidates.map((candidate) => (
                <ChunkSetRow
                  key={candidate.chunk_set_id}
                  chunkSet={candidate}
                  onPromote={() => void handlePromote(candidate)}
                  promoting={
                    promoteExperiment.isPending &&
                    promoteExperiment.variables?.chunkSetId === candidate.chunk_set_id
                  }
                />
              ))}
            </ul>

            {/* 別レシピを試す(分割軸) */}
            <div className="space-y-3 rounded-lg border border-border bg-background p-3">
              <h3 className="text-sm font-semibold text-foreground">
                {t("documents.experiment.form.title")}
              </h3>
              <div className="grid gap-3 sm:grid-cols-3">
                <SelectField
                  id="experiment-strategy"
                  label={t("documents.experiment.form.strategy")}
                  value={strategy}
                  options={strategyOptions}
                  onValueChange={setStrategy}
                  buttonClassName="h-9"
                />
                <NumberField
                  id="experiment-chunk-size"
                  label={t("documents.experiment.form.chunkSize")}
                  value={chunkSize}
                  onChange={setChunkSize}
                  placeholder="800"
                />
                <NumberField
                  id="experiment-overlap"
                  label={t("documents.experiment.form.overlap")}
                  value={overlap}
                  onChange={setOverlap}
                  placeholder="120"
                />
              </div>
              {formError ? <FormStatus tone="danger" message={formError} /> : null}
              <div className="flex">
                <Button
                  type="button"
                  variant="secondary"
                  size="md"
                  onClick={handleCreate}
                  loading={createExperiment.isPending}
                  className="h-9"
                >
                  <FlaskConical size={15} aria-hidden />
                  {t("documents.experiment.form.submit")}
                </Button>
              </div>
            </div>

            {/* 解析・ファイル準備を変えて試す(再抽出・非同期ジョブ) */}
            <div className="space-y-3 rounded-lg border border-border bg-background p-3">
              <div className="space-y-1">
                <h3 className="text-sm font-semibold text-foreground">
                  {t("documents.experiment.reparse.title")}
                </h3>
                <p className="text-xs text-muted">
                  {t("documents.experiment.reparse.description")}
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <SelectField
                  id="experiment-reparse-preprocess"
                  label={t("documents.experiment.reparse.preprocess")}
                  value={reparseProfile}
                  options={preprocessOptions}
                  onValueChange={setReparseProfile}
                  helper={
                    serving?.preprocess
                      ? t("documents.experiment.reparse.current", {
                          value: t(`settings.preprocess.profile.${serving.preprocess}` as I18nKey),
                        })
                      : undefined
                  }
                  buttonClassName="h-9"
                />
                <SelectField
                  id="experiment-reparse-parser"
                  label={t("documents.experiment.reparse.parser")}
                  value={reparseParser}
                  options={parserOptions}
                  onValueChange={setReparseParser}
                  helper={
                    serving?.parser
                      ? t("documents.experiment.reparse.current", {
                          value: parserBackendLabel(serving.parser),
                        })
                      : undefined
                  }
                  buttonClassName="h-9"
                />
              </div>
              {reparseError ? <FormStatus tone="danger" message={reparseError} /> : null}
              {reparseRunning ? (
                <p
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-muted"
                  role="status"
                >
                  <Loader2 size={15} className="shrink-0 animate-spin" aria-hidden />
                  {reparseJobData?.status === "RUNNING"
                    ? t("documents.experiment.reparse.running")
                    : t("documents.experiment.reparse.queued")}
                </p>
              ) : null}
              <div className="flex">
                <Button
                  type="button"
                  variant="secondary"
                  size="md"
                  onClick={handleReparse}
                  loading={createReparse.isPending || reparseRunning}
                  className="h-9"
                >
                  <FlaskConical size={15} aria-hidden />
                  {t("documents.experiment.reparse.submit")}
                </Button>
              </div>
            </div>

            {/* プローブ検索の横並び比較 */}
            {candidates.length === 0 ? (
              <EmptyState title={t("documents.experiment.compare.needsCandidate")} />
            ) : (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-foreground">
                  {t("documents.experiment.compare.title")}
                </h3>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  {candidates.length > 1 ? (
                    <SelectField
                      id="experiment-probe-candidate"
                      label={t("documents.experiment.compare.candidate")}
                      value={activeCandidate?.chunk_set_id ?? ""}
                      options={candidates.map((candidate) => ({
                        value: candidate.chunk_set_id,
                        label: shortId(candidate.chunk_set_id),
                      }))}
                      onValueChange={setProbeCandidateId}
                      className="w-full min-w-0 sm:w-56"
                      buttonClassName="h-9"
                    />
                  ) : null}
                  <div className="relative min-w-0 flex-1">
                    <label htmlFor="experiment-probe-query" className="sr-only">
                      {t("documents.experiment.compare.queryLabel")}
                    </label>
                    <SearchIcon
                      size={16}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                      aria-hidden
                    />
                    <input
                      id="experiment-probe-query"
                      type="text"
                      value={probeQuery}
                      onChange={(event) => setProbeQuery(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void runProbe();
                      }}
                      placeholder={t("documents.experiment.compare.placeholder")}
                      className={`${inputClass} pl-9`}
                    />
                  </div>
                  <Button
                    type="button"
                    size="md"
                    onClick={() => void runProbe()}
                    loading={probing}
                    disabled={!probeQuery.trim()}
                    className="h-9 shrink-0"
                  >
                    {t("documents.experiment.compare.run")}
                  </Button>
                </div>

                {probeError ? (
                  <ErrorState message={probeError} onRetry={() => void runProbe()} />
                ) : probe ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    <ProbeColumn
                      title={t("documents.experiment.compare.servingColumn")}
                      chunks={probe.serving}
                    />
                    <ProbeColumn
                      title={t("documents.experiment.compare.candidateColumn")}
                      chunks={probe.candidate}
                    />
                  </div>
                ) : (
                  <p className="text-xs text-muted">{t("documents.experiment.compare.hint")}</p>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ChunkSetRow({
  chunkSet,
  onPromote,
  promoting,
}: {
  chunkSet: DocumentChunkSet;
  onPromote?: () => void;
  promoting?: boolean;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-background px-3 py-2 text-xs">
      {chunkSet.is_serving ? (
        <span className="rounded-sm bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
          {t("documents.experiment.serving")}
        </span>
      ) : (
        <span className="rounded-sm bg-muted/15 px-1.5 py-0.5 font-medium text-muted">
          {t("documents.experiment.candidate")}
        </span>
      )}
      <span className="font-mono text-muted" title={chunkSet.chunk_set_id}>
        {shortId(chunkSet.chunk_set_id)}
      </span>
      <span className="rounded-sm bg-muted/10 px-1.5 py-0.5 text-muted">{chunkSet.status}</span>
      <span className="tnum text-muted">
        {t("documents.experiment.chunkCount", { count: chunkSet.chunk_count })}
      </span>
      {onPromote ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onPromote}
          loading={promoting}
          className="ml-auto shrink-0 whitespace-nowrap"
        >
          <ArrowUpCircle size={14} aria-hidden />
          {t("documents.experiment.promote.action")}
        </Button>
      ) : null}
    </li>
  );
}

function ProbeColumn({ title, chunks }: { title: string; chunks: RetrievedChunk[] }) {
  const scoreMaxima = scoreMaximaForCitations(chunks);
  return (
    <section className="min-w-0">
      <h4 className="mb-2 text-xs font-semibold text-foreground">
        {title}（{chunks.length}）
      </h4>
      {chunks.length === 0 ? (
        <p className="rounded-md border border-border bg-background px-3 py-4 text-center text-xs text-muted">
          {t("search.noResults")}
        </p>
      ) : (
        <ul className="space-y-3">
          {chunks.map((chunk, index) => (
            <CitationCard
              key={chunk.chunk_id}
              chunk={chunk}
              index={index}
              scoreMaxima={scoreMaxima}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={inputClass}
      />
    </div>
  );
}

function shortId(value: string) {
  return value.slice(0, 12);
}
