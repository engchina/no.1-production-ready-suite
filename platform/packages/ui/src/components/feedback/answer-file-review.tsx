import { BookOpen, RotateCcw, Search, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { Button } from "../ui/button";
import { RadioOptionGroup } from "../ui/radio-option-group";
import { SearchField } from "../ui/search-field";
import { SideSheet } from "../ui/side-sheet";
import {
  cited,
  files,
  locator,
  page,
  passagePageLabel,
  position,
  type AnswerRating,
  type FileReviewAction,
  type FileReviewState,
  type ReviewText,
  type ReviewVersion,
} from "./file-review-model";

type Mode = "only" | "mix" | "replace";
interface Props {
  rootId: string;
  t: ReviewText;
  readOnly?: boolean;
  disabled?: boolean;
  load: () => Promise<FileReviewState>;
  original?: (task: string, documentId: string) => Promise<Record<string, string>>;
  change: (action: FileReviewAction) => Promise<FileReviewState>;
  vote: (id: string, value: AnswerRating) => Promise<void>;
  initialRating?: AnswerRating | null;
  onHistory: (rootId: string) => void;
  taskLabel?: (task: string, question: string, number: number) => string;
  renderVersion: (version: ReviewVersion) => ReactNode;
}

export function AnswerFileReview({
  rootId,
  t,
  readOnly,
  disabled,
  load,
  original,
  change,
  vote,
  initialRating,
  onHistory,
  taskLabel,
  renderVersion,
}: Props) {
  const [state, setState] = useState<FileReviewState | null>(null);
  const [rating, setRating] = useState<AnswerRating | null>(initialRating ?? null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [task, setTask] = useState("");
  const [mode, setMode] = useState<Mode | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState("");
  const [passages, setPassages] = useState<Record<string, string>>({});
  const [manage, setManage] = useState(false);
  const [reset, setReset] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const selectionId = useId();
  const loader = useRef(load);
  const userAction = useRef(0);
  const active = useRef(true);
  useEffect(() => {
    loader.current = load;
  }, [load]);
  useEffect(() => {
    active.current = true;
    let cancelled = false;
    const initialAction = userAction.current;
    void loader
      .current()
      .then((s) => {
        if (!cancelled && initialAction === userAction.current && Array.isArray(s?.versions)) setState(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      active.current = false;
    };
  }, [rootId]);
  const hasHistory = (state?.versions.length ?? 0) > 1;
  useEffect(() => {
    if (hasHistory) onHistory(rootId);
  }, [rootId, hasHistory, onHistory]);
  const latest = state?.versions.at(-1);
  const pending = state?.pending[task];
  const source = pending ?? latest?.sources[task];
  const groups = useMemo(
    () => files(source?.citations ?? []).filter((file) => !Object.hasOwn(state?.excluded ?? {}, file.id)),
    [source?.citations, state?.excluded],
  );
  const normalize = (v: string) => v.normalize("NFKC").trim().toLocaleLowerCase();
  const matching = groups.filter((f) => normalize(f.name).includes(normalize(query)));
  const shown = new Set(matching.map((f) => f.id));
  const dropping = groups.filter((f) => !checked.includes(f.id));
  const hidden = dropping.filter((f) => !shown.has(f.id)).length;
  const excluded = state?.excluded ?? {};
  const total = new Set([...Object.keys(excluded), ...dropping.map((f) => f.id)]).size;
  const selectedFile = groups.find((f) => f.id === preview);
  const carried = state?.kept[task] ?? [];
  function choose(s: FileReviewState, key: string) {
    setTask(key);
    setQuery("");
    setPreview("");
    setManage(false);
    setReset(false);
    setMode(null);
    const available = files((s.pending[key] ?? s.versions.at(-1)?.sources[key])?.citations ?? []);
    setChecked(
      (s.kept[key] ?? []).filter((id) => !Object.hasOwn(s.excluded, id) && available.some((f) => f.id === id)),
    );
    if (list.current) list.current.scrollTop = 0;
  }
  async function operation(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : t("fileReview.error"));
    } finally {
      if (active.current) setBusy(false);
    }
  }
  function mark(value: AnswerRating) {
    void operation(async () => {
      userAction.current += 1;
      const current = latest ? latest.rating : (rating ?? initialRating);
      const id = latest?.id ?? rootId;
      if (current !== value) await vote(id, value);
      if (!active.current) return;
      setRating(value);
      setState(
        (previous) =>
          previous && {
            ...previous,
            versions: previous.versions.map((v) => (v.id === id ? { ...v, rating: value } : v)),
          },
      );
      if (value === "not_helpful") {
        const s = await load();
        if (!active.current) return;
        if (!s?.versions?.length) throw new Error(t("fileReview.error"));
        setState(s);
        setOpen(true);
        const keys = Object.keys(s.versions.at(-1)?.sources ?? {});
        choose(s, keys.length === 1 ? keys[0] : "");
      }
    });
  }
  function submit(action: FileReviewAction["action"], selected = checked) {
    if (!state) return;
    void operation(async () => {
      const s = await change({ task, action, selected, revision: state.revision });
      if (!active.current) return;
      setState(s);
      choose(s, task);
      if (action === "answer") {
        setRating(null);
        setOpen(false);
      }
      if (action === "restore") setManage(true);
    });
  }
  const close = () => {
    if (!busy) {
      setOpen(false);
      setError("");
    }
  };
  const back = () => {
    if (selectedFile) {
      setPreview("");
      requestAnimationFrame(() => opener.current?.focus({ preventScroll: true }));
    } else if (manage || reset) {
      setManage(false);
      setReset(false);
    } else close();
  };
  const errorView = error && (
    <div role="alert" className="space-y-1 text-sm">
      <p>{error}</p>
      <Button
        size="sm"
        variant="secondary"
        onClick={() =>
          void operation(async () => {
            const s = await load();
            setState(s);
            if (task) choose(s, task);
          })
        }
      >
        {t("fileReview.retry")}
      </Button>
    </div>
  );
  const currentRating = latest ? latest.rating : (rating ?? initialRating);
  const title = selectedFile
    ? t("fileReview.original")
    : manage
      ? t("fileReview.manage", { count: Object.keys(excluded).length })
      : reset
        ? t("fileReview.reset")
        : !task
          ? t("fileReview.task")
          : pending
            ? t("fileReview.candidates")
            : t("fileReview.title");
  const actionTitle =
    mode === "only" || pending
      ? t("fileReview.confirm")
      : checked.length
        ? t("fileReview.keepFind")
        : mode === "replace" && groups.length
          ? t("fileReview.replaceFind")
          : t("fileReview.find");
  return (
    <div className="space-y-2" data-testid="answer-file-review">
      {state && state.versions.length > 1 && (
        <div className="space-y-3">
          {state.versions.map((v, i) => {
            const status = t(
              v.rating === "helpful"
                ? "fileReview.helpful"
                : v.rating === "not_helpful"
                  ? "fileReview.notHelpful"
                  : "fileReview.unrated",
            );
            const title = t("fileReview.version", { version: i + 1 }) + " · " + status;
            return i === state.versions.length - 1 ? (
              <section key={v.id} className="space-y-2 border-t border-border pt-3">
                <h3 className="font-semibold">{title}</h3>
                {renderVersion(v)}
              </section>
            ) : (
              <details key={v.id}>
                <summary>{title}</summary>
                {renderVersion(v)}
              </details>
            );
          })}
        </div>
      )}
      {!readOnly && (
        <div role="group" aria-labelledby={selectionId + "-question"} className="space-y-1.5">
          <p id={selectionId + "-question"} className="text-sm">
            {t("fileReview.question")}
          </p>
          <div className="flex flex-wrap gap-2">
            {(["helpful", "not_helpful"] as const).map((value) => (
              <Button
                key={value}
                size="sm"
                variant="secondary"
                icon={value === "helpful" ? ThumbsUp : ThumbsDown}
                disabled={busy || disabled}
                aria-pressed={currentRating === value}
                className={currentRating === value ? "border-primary bg-primary-subtle ring-1 ring-primary" : undefined}
                onClick={() => mark(value)}
              >
                {t(value === "helpful" ? "fileReview.helpful" : "fileReview.notHelpful")}
              </Button>
            ))}
          </div>
        </div>
      )}
      {!open && errorView}
      {state && (
        <SideSheet
          side="right"
          size="md"
          fullScreenBelowSm
          initialFocus="dialog"
          open={open}
          onClose={close}
          title={title}
          closeLabel={t("fileReview.close")}
          bodyClassName="flex min-h-0 flex-col gap-2"
          footer={
            <div className="space-y-2">
              {errorView}
              {task && !selectedFile && !manage && !reset && (
                <p className="flex flex-col text-xs text-fg-muted tnum sm:flex-row sm:flex-wrap sm:gap-x-3" role="status" aria-atomic="true">
                  <span>
                    {t("fileReview.selectionCount", {
                      scope: t(query ? "fileReview.showing" : "fileReview.all", { shown: matching.length, total: groups.length }),
                      keep: checked.length,
                    })}
                  </span>{" "}
                  <span>
                    {t(hidden ? "fileReview.exclusionWithHidden" : "fileReview.exclusionCount", {
                      remove: dropping.length, total, hidden,
                    })}
                  </span>
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {task && !selectedFile && !manage && !reset && (
                  <>
                    <Button
                      icon={Search}
                      disabled={
                        busy || (!pending && !mode) || (!checked.length && (pending != null || mode !== "replace"))
                      }
                      onClick={() => submit(pending || mode === "only" ? "answer" : "discover")}
                    >
                      {actionTitle}
                    </Button>
                    {pending && (
                      <Button variant="secondary" disabled={busy} onClick={() => submit("discover")}>
                        {t("fileReview.more")}
                      </Button>
                    )}
                  </>
                )}
                {reset && (
                  <Button icon={RotateCcw} disabled={busy} onClick={() => submit("reset", [])}>
                    {t("fileReview.reset")}
                  </Button>
                )}
                <Button disabled={busy} variant="secondary" onClick={back}>
                  {t("fileReview.back")}
                </Button>
              </div>
            </div>
          }
        >
          {!task &&
            latest &&
            Object.entries(latest.sources).map(([id, s], index) => (
              <Button key={id} variant="secondary" onClick={() => choose(state, id)}>
                {taskLabel?.(id, s.title, index + 1) ?? s.title}
              </Button>
            ))}
          {task && (
            <div
              hidden={!!selectedFile || manage || reset}
              className={selectedFile || manage || reset ? "hidden" : "flex h-full min-h-0 flex-col gap-2"}
            >
              {!pending && (
                <RadioOptionGroup<Mode>
                  legend={t("fileReview.method")}
                  className="[&_[data-field-width-exempt]]:grid-cols-1 sm:[&_[data-field-width-exempt]]:grid-cols-3"
                  options={[
                    { value: "only", label: t("fileReview.only") },
                    { value: "mix", label: t("fileReview.mix") },
                    {
                      value: "replace",
                      label: t(carried.length ? "fileReview.replaceNew" : "fileReview.replace"),
                    },
                  ]}
                  value={mode}
                  onValueChange={(v) => {
                    setMode(v);
                    if (v === "replace") setChecked(carried.filter((id) => groups.some((f) => f.id === id)));
                  }}
                />
              )}
              <div className="flex h-[var(--button-height-sm)] shrink-0 items-center justify-between gap-1">
                <h3 className="text-sm font-semibold">{t(pending ? "fileReview.found" : "fileReview.given")}</h3>
                {hidden > 0 && (
                  <Button variant="ghost" size="sm" aria-controls={selectionId} onClick={() => setQuery("")}>
                    {t("fileReview.showHidden")}
                  </Button>
                )}
              </div>
              <SearchField
                id={selectionId + "-search"}
                value={query}
                onSearch={setQuery}
                label={t("fileReview.filename")}
                labelHidden
                placeholder={t("fileReview.placeholder")}
                clearLabel={t("fileReview.clear")}
                aria-controls={selectionId}
                debounceMs={0}
              />
              <div
                id={selectionId}
                ref={list}
                role="region"
                aria-label={t("fileReview.candidates")}
                tabIndex={0}
                className="min-h-32 flex-1 space-y-2 overflow-y-auto overscroll-contain rounded-md border border-border bg-surface-sunken p-2"
              >
                {!matching.length && (
                  <p className="text-sm text-fg-muted">{t(query ? "fileReview.noMatch" : "fileReview.none")}</p>
                )}
                {matching.map((f) => {
                  const selected = checked.includes(f.id);
                  const count = f.hits.filter(cited).length;
                  return (
                    <div
                      key={f.id}
                      className={
                        "rounded-md border p-2 " +
                        (selected ? "border-primary bg-surface-selected" : "border-border bg-surface")
                      }
                    >
                      <div className="flex items-start justify-between gap-2">
                        <label className="flex min-w-0 items-baseline gap-2 text-sm font-medium">
                          <input
                            type="checkbox"
                            checked={selected}
                            disabled={busy}
                            onChange={() => {
                              setChecked((old) => (selected ? old.filter((id) => id !== f.id) : [...old, f.id]));
                              if (!mode && !pending) setMode("mix");
                            }}
                          />
                          <span className="break-all">{f.name}</span>
                        </label>
                        <Button
                          size="sm"
                          icon={BookOpen}
                          variant="ghost"
                          onClick={(e) => {
                            opener.current = e.currentTarget;
                            setPassages({});
                            setPreview(f.id);
                            if (original && f.hits.some((hit) => hit.truncated)) {
                              void operation(async () => setPassages(await original(task, f.id)));
                            }
                          }}
                        >
                          {t("fileReview.original")}
                        </Button>
                      </div>
                      <p className="text-xs text-fg-muted">
                        {[
                          t("fileReview.locations", { count: f.hits.length }),
                          f.pages.remaining ? t("fileReview.morePages", { pages: f.pages.label, count: f.pages.remaining }) : f.pages.label,
                          t(count ? "fileReview.cited" : "fileReview.noCites", { count }),
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      <p className="truncate text-sm text-fg-muted">{f.excerpt.text ?? f.excerpt.excerpt}</p>
                    </div>
                  );
                })}
              </div>
              {(state.revision > 0 || Object.keys(excluded).length > 0) && (
                <div className="flex shrink-0 flex-wrap gap-2">
                  {Object.keys(excluded).length > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => setManage(true)}>
                      {t("fileReview.manage", { count: Object.keys(excluded).length })}
                    </Button>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => setReset(true)}>
                    {t("fileReview.reset")}
                  </Button>
                </div>
              )}
            </div>
          )}
          {selectedFile && (
            <div className="space-y-3 overflow-y-auto">
              <h3 className="break-all font-semibold">{selectedFile.name}</h3>
              {[...selectedFile.hits]
                .sort((a, b) => page(a) - page(b) || position(a) - position(b))
                .map((hit) => (
                  <section key={hit.chunk_id} className="space-y-1 border-t border-border pt-2">
                    <p className="text-xs text-fg-muted">
                      {[
                        passagePageLabel(hit),
                        locator(hit).section_title ?? "",
                        cited(hit) ? t("fileReview.used") : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                    <p className="whitespace-pre-wrap break-words text-sm">
                      {passages[hit.chunk_id] ?? hit.text ?? hit.excerpt}
                    </p>
                  </section>
                ))}
            </div>
          )}
          {manage &&
            Object.entries(excluded).map(([id, name]) => (
              <div className="flex items-center justify-between gap-2" key={id}>
                <span>{name}</span>
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => submit("restore", [id])}>
                  {t("fileReview.restore")}
                </Button>
              </div>
            ))}
        </SideSheet>
      )}
    </div>
  );
}
