import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  useResetExecutionConsent,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { apiGet, apiPost } from "@/lib/api";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";
import { t } from "@/lib/i18n";

import { versionLabel } from "./ontologyResultPresentation";
import { ProfileOntologyBundle } from "./ProfileOntologyResults";

export const tabs = [
  "overview",
  "model",
  "mapping",
  "validation",
  "review",
] as const;
type Tab = (typeof tabs)[number];
interface Workspace {
  bundle: ProfileOntologyBundle;
  artifacts: Record<string, string>;
  head: { release_id: string; etag: string; display_version?: number | null };
  releases?: {
    id: string;
    published_at: string;
    display_version?: number | null;
  }[];
}
interface Changes {
  id: string;
  base_etag: string;
  before: Record<string, unknown>[];
  after: Record<string, unknown>[];
}
interface ValidationJob {
  job_id: string;
  status: string;
  error_message_ja?: string;
  report?: Record<string, unknown>;
}

export function useOntologyDefinitionWorkspace({
  bundle,
  profileId,
  profileLabel,
  onChanged,
  onPublished,
}: {
  bundle: ProfileOntologyBundle;
  profileId: string;
  profileLabel?: string;
  onChanged: () => Promise<unknown>;
  onPublished?: () => void;
}) {
  const queryClient = useQueryClient();
  const prefix = `ontology-v2:${profileId}:${bundle.id}`;
  const [savedTab, setTab] = useWorkspaceState<Tab | "capabilities">(
    `${prefix}:tab`,
    "overview",
  );
  const tab: Tab = savedTab === "capabilities" ? "overview" : savedTab;
  useEffect(() => {
    if (savedTab === "capabilities") setTab("overview");
  }, [savedTab, setTab]);
  const [focusId, setFocusId] = useState("");
  const [success, setSuccess] = useState("");
  const [acceptanceError, setAcceptanceError] = useState("");
  const [instruction, setInstruction] = useWorkspaceState(
    `${prefix}:instruction`,
    "",
  );
  const [notes, setNotes] = useWorkspaceState(
    `${prefix}:notes`,
    bundle.notes_ja ?? "",
  );
  const notesBaseline = useRef(bundle.notes_ja ?? "");
  useEffect(() => {
    const saved = bundle.notes_ja ?? "";
    if (notes === notesBaseline.current && notes !== saved) setNotes(saved);
    notesBaseline.current = saved;
  }, [bundle.notes_ja]);
  const [acceptance, setAcceptance] = useWorkspaceState(
    `${prefix}:acceptance`,
    "",
  );
  const [validationJobId, setValidationJobId] = useWorkspaceState(
    `${prefix}:validation-job`,
    "",
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [changes, setChanges] = useState<Changes | null>(null);
  const [rollbackId, setRollbackId] = useState("");
  const confirm = useConfirm();
  useUnsavedChangesGuard(notes !== (bundle.notes_ja ?? ""), () =>
    confirm({
      title: t("ontologyUi.leaveTitle"),
      description: t("ontologyUi.leaveHint"),
      confirmLabel: t("ontologyUi.leave"),
      tone: "warning",
    }),
  );
  const consentVersion = useRef(0);
  useEffect(
    () => () => {
      consentVersion.current += 1;
    },
    [],
  );
  useResetExecutionConsent(() => {
    setChanges(null);
    setRollbackId("");
    consentVersion.current += 1;
  }, `${profileId}:${bundle.etag}:${instruction}:${notes}:${acceptance}`);
  const workspace = useQuery({
    queryKey: [
      "nl2sql",
      "profiles",
      "ontology-workspace",
      profileId,
      bundle.id,
      bundle.etag,
    ],
    queryFn: ({ signal }) =>
      apiGet<Workspace>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results/${encodeURIComponent(bundle.id)}/workspace`,
        { signal },
      ),
    retry: false,
  });
  const job = useQuery({
    queryKey: [
      "nl2sql",
      "profiles",
      "ontology-validation-job",
      profileId,
      validationJobId,
    ],
    queryFn: ({ signal }) =>
      apiGet<ValidationJob>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-validation-jobs/${encodeURIComponent(validationJobId)}`,
        { signal },
      ),
    enabled: !!validationJobId,
    retry: false,
    refetchInterval: (query) =>
      query.state.data &&
      ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 1000,
  });
  useEffect(() => {
    consentVersion.current += 1;
  }, [rollbackId, workspace.data?.head?.etag]);
  useEffect(() => {
    setError("");
    setSuccess("");
  }, [tab]);
  const handledJob = useRef("");
  useEffect(() => {
    if (
      job.data?.status === "succeeded" &&
      handledJob.current !== job.data.job_id
    ) {
      handledJob.current = job.data.job_id;
      void onChanged();
    }
  }, [job.data, onChanged]);
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results/${encodeURIComponent(bundle.id)}`;
  const readOnly = bundle.status === "published";
  const headers = () => ({
    "If-Match": `"${bundle.etag}"`,
    "Idempotency-Key": crypto.randomUUID(),
  });
  const run = async (
    action: string,
    body?: unknown,
    needsConfirmation = false,
  ) => {
    if (busy || workspace.isError || !workspace.data) return;
    if (action === "data") {
      try {
        if (acceptance.trim() && !Array.isArray(JSON.parse(acceptance)))
          throw new Error();
      } catch {
        setAcceptanceError(t("ontologyUi.jsonError"));
        return;
      }
    }
    const consent = consentVersion.current;
    if (
      needsConfirmation &&
      !(await confirm({
        title: t(
          `ontologyWorkspace.action.${action}` as Parameters<typeof t>[0],
        ),
        description: t("ontologyWorkspace.confirmScope", {
          profile: profileLabel || profileId,
          version: versionLabel(
            action === "rollback"
              ? workspace.data?.releases?.find((r) => r.id === rollbackId)
                  ?.display_version
              : bundle.display_version,
          ),
        }),
        confirmLabel: t("ontologyWorkspace.confirm"),
        tone: "info",
      }))
    )
      return;
    if (consent !== consentVersion.current) return;
    setBusy(action);
    setError("");
    setSuccess("");
    try {
      if (action === "analyze")
        setChanges(
          await apiPost<Changes>(
            `${endpoint}/analyze`,
            { instruction_ja: instruction },
            { headers: headers() },
          ),
        );
      else if (action === "rollback") {
        await apiPost(
          `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-releases/${encodeURIComponent(rollbackId)}/rollback`,
          {
            expected_head: workspace.data?.head?.release_id ?? "",
            confirmed: true,
          },
          {
            headers: {
              ...headers(),
              "If-Match": workspace.data?.head?.etag ?? "",
            },
          },
        );
        setRollbackId("");
        await workspace.refetch();
        await onChanged();
      } else if (action === "data") {
        let cases: unknown;
        try {
          cases = acceptance.trim() ? JSON.parse(acceptance) : [];
          if (!Array.isArray(cases)) throw new Error();
        } catch {
          setAcceptanceError(t("ontologyUi.jsonError"));
          return;
        }
        setAcceptanceError("");
        const result = await apiPost<ValidationJob>(
          `${endpoint}/validation-jobs`,
          { confirmed: true, sample_limit: 50, acceptance_cases: cases },
          { headers: headers() },
        );
        setValidationJobId(result.job_id);
      } else {
        const suffix =
          action === "apply" && changes
            ? `changes/${encodeURIComponent(changes.id)}/apply`
            : action;
        await apiPost(`${endpoint}/${suffix}`, body, { headers: headers() });
        setChanges(null);
        await onChanged();
        await workspace.refetch();
      }
      if (action === "publish" || action === "rollback") {
        await queryClient.invalidateQueries({
          queryKey: ["nl2sql", "profiles", "ontology-capabilities", profileId],
        });
        await onPublished?.();
      }
      setSuccess(t("ontologyUi.operationDone"));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("ontologyWorkspace.failed"),
      );
    } finally {
      setBusy("");
    }
  };
  return {
    prefix,
    tab,
    setTab,
    focusId,
    setFocusId,
    success,
    acceptanceError,
    setAcceptanceError,
    instruction,
    setInstruction,
    notes,
    setNotes,
    acceptance,
    setAcceptance,
    validationJobId,
    busy,
    error,
    changes,
    setChanges,
    rollbackId,
    setRollbackId,
    workspace,
    job,
    readOnly,
    run,
  };
}
