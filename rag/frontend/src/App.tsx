import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigationType,
  useParams,
} from "react-router-dom";
import { ChevronLeft } from "lucide-react";

import { LoginPage } from "@/components/auth/LoginPage";
import { DashboardClient } from "@/components/dashboard/DashboardClient";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { EvaluationClient } from "@/components/evaluation/EvaluationClient";
import { FileListClient } from "@/components/file-list/FileListClient";
import { KnowledgeBaseManagementClient } from "@/components/knowledge-bases/KnowledgeBaseManagementClient";
import { Sidebar } from "@/components/layout/Sidebar";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { DatabaseGate } from "@/components/system/DatabaseGate";
import { PageHeader } from "@/components/PageHeader";
import { SearchClient } from "@/components/search/SearchClient";
import { ErrorState } from "@/components/StateViews";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { ParserAdapterSettingsClient } from "@/components/settings/ParserAdapterSettingsClient";
import { ChunkingSettingsClient } from "@/components/settings/ChunkingSettingsClient";
import { PreprocessSettingsClient } from "@/components/settings/PreprocessSettingsClient";
import { RetrievalSettingsClient } from "@/components/settings/RetrievalSettingsClient";
import { GroundingSettingsClient } from "@/components/settings/GroundingSettingsClient";
import { GenerationSettingsClient } from "@/components/settings/GenerationSettingsClient";
import { GuardrailSettingsClient } from "@/components/settings/GuardrailSettingsClient";
import { VectorIndexSettingsClient } from "@/components/settings/VectorIndexSettingsClient";
import { EvaluationSettingsClient } from "@/components/settings/EvaluationSettingsClient";
import { GraphSettingsClient } from "@/components/settings/GraphSettingsClient";
import { AgenticSettingsClient } from "@/components/settings/AgenticSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UploadWorkspace } from "@/components/upload/UploadWorkspace";
import { useAuth } from "@/lib/auth";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";

export function App() {
  const auth = useAuth();

  if (auth.isLoading) {
    return <AuthLoading />;
  }

  if (auth.error) {
    return (
      <main className="grid min-h-dvh place-items-center bg-background p-6">
        <div className="w-full max-w-lg">
          <ErrorState message={t("auth.status.error")} onRetry={() => void auth.refetch()} />
        </div>
      </main>
    );
  }

  return (
    <Routes>
      <Route path={APP_ROUTES.login} element={<LoginRoute />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<Navigate to={APP_ROUTES.dashboard} replace />} />
        <Route path={APP_ROUTES.dashboard} element={<DashboardClient />} />
        <Route path={APP_ROUTES.upload} element={<UploadWorkspace />} />
        <Route path={APP_ROUTES.fileList} element={<FileListClient />} />
        <Route path={APP_ROUTES.knowledgeBases} element={<KnowledgeBaseManagementClient />} />
        <Route path={`${APP_ROUTES.documents}/:id`} element={<DocumentDetailRoute />} />
        <Route path={APP_ROUTES.search} element={<SearchClient />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationClient />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route
          path={APP_ROUTES.settingsUploadStorage}
          element={<SettingsUploadStorageRoute />}
        />
        <Route
          path={APP_ROUTES.settingsParserAdapters}
          element={<SettingsParserAdaptersRoute />}
        />
        <Route path={APP_ROUTES.settingsPreprocess} element={<SettingsPreprocessRoute />} />
        <Route path={APP_ROUTES.settingsChunking} element={<SettingsChunkingRoute />} />
        <Route path={APP_ROUTES.settingsRetrieval} element={<SettingsRetrievalRoute />} />
        <Route path={APP_ROUTES.settingsGrounding} element={<SettingsGroundingRoute />} />
        <Route path={APP_ROUTES.settingsGeneration} element={<SettingsGenerationRoute />} />
        <Route path={APP_ROUTES.settingsGuardrail} element={<SettingsGuardrailRoute />} />
        <Route path={APP_ROUTES.settingsVectorIndex} element={<SettingsVectorIndexRoute />} />
        <Route path={APP_ROUTES.settingsEvaluation} element={<SettingsEvaluationRoute />} />
        <Route path={APP_ROUTES.settingsGraph} element={<SettingsGraphRoute />} />
        <Route path={APP_ROUTES.settingsAgentic} element={<SettingsAgenticRoute />} />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route path="/settings" element={<Navigate to={APP_ROUTES.settingsOci} replace />} />
      </Route>
      <Route path="*" element={<Navigate to={APP_ROUTES.dashboard} replace />} />
    </Routes>
  );
}

function ProtectedLayout() {
  const auth = useAuth();
  const location = useLocation();
  const navigationType = useNavigationType();
  const mainRef = useRef<HTMLElement | null>(null);
  const setSidebarCollapsed = useUiStore((state) => state.setSidebarCollapsed);
  useCollapseSidebarOnNarrowViewport(setSidebarCollapsed);
  useMainScrollRestoration(mainRef, location, navigationType);

  if (auth.authRequired && !auth.isAuthenticated) {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={APP_ROUTES.login} state={{ from }} replace />;
  }

  return (
    <div className="flex">
      <Sidebar />
      <CommandPalette />
      <main
        ref={mainRef}
        className="h-screen min-w-0 flex-1 overflow-y-auto [contain:layout] focus:outline-none"
        aria-label="メイン領域"
        tabIndex={-1}
      >
        <DatabaseGate>
          <Outlet />
        </DatabaseGate>
      </main>
    </div>
  );
}

type RouterLocation = ReturnType<typeof useLocation>;
type RouterNavigationType = ReturnType<typeof useNavigationType>;

function useCollapseSidebarOnNarrowViewport(setSidebarCollapsed: (collapsed: boolean) => void) {
  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const collapseIfNarrow = () => {
      if (media.matches) setSidebarCollapsed(true);
    };
    collapseIfNarrow();
    media.addEventListener("change", collapseIfNarrow);
    return () => media.removeEventListener("change", collapseIfNarrow);
  }, [setSidebarCollapsed]);
}

const mainScrollPositions = new Map<string, number>();

function useMainScrollRestoration(
  mainRef: RefObject<HTMLElement | null>,
  location: RouterLocation,
  navigationType: RouterNavigationType
) {
  const pathnameRef = useRef(location.pathname);
  const hashRef = useRef(location.hash);
  const scrollKey = mainScrollPositionKey(location);

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return;

    const save = () => {
      mainScrollPositions.set(scrollKey, main.scrollTop);
    };
    main.addEventListener("scroll", save, { passive: true });

    return () => {
      main.removeEventListener("scroll", save);
    };
  }, [mainRef, scrollKey]);

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return;

    const pathnameChanged = pathnameRef.current !== location.pathname;
    const hashChanged = hashRef.current !== location.hash;
    pathnameRef.current = location.pathname;
    hashRef.current = location.hash;

    if (!pathnameChanged && !hashChanged && navigationType !== "POP") return;

    const nextTop =
      navigationType === "POP" ? mainScrollPositions.get(scrollKey) ?? 0 : 0;
    const scroll = () => {
      if (location.hash && scrollHashTargetIntoView(location.hash)) return;
      main.scrollTo({ top: nextTop, left: 0, behavior: "auto" });
    };

    if (pathnameChanged) main.focus({ preventScroll: true });
    scroll();
    const animationFrame = window.requestAnimationFrame(scroll);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [location.hash, location.pathname, mainRef, navigationType, scrollKey]);
}

function mainScrollPositionKey(location: RouterLocation) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function scrollHashTargetIntoView(hash: string) {
  const id = decodeHashId(hash);
  if (!id) return false;

  const target = document.getElementById(id);
  if (!target) return false;

  target.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
  return true;
}

function decodeHashId(hash: string) {
  const id = hash.slice(1);
  try {
    return decodeURIComponent(id);
  } catch {
    return id;
  }
}

function LoginRoute() {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.authRequired || auth.isAuthenticated) {
    const redirectTarget = (location.state as { from?: string } | null)?.from;
    return (
      <Navigate
        to={
          redirectTarget && redirectTarget !== APP_ROUTES.login
            ? redirectTarget
            : APP_ROUTES.dashboard
        }
        replace
      />
    );
  }
  return <LoginPage />;
}

function AuthLoading() {
  return (
    <main className="grid min-h-dvh place-items-center bg-background p-6">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-6">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="mt-4 h-11 w-full" />
        <Skeleton className="mt-3 h-11 w-full" />
        <Button className="mt-5 h-11 w-full" disabled>
          {t("auth.status.checking")}
        </Button>
      </div>
    </main>
  );
}

function DocumentDetailRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to={APP_ROUTES.fileList} replace />;

  return (
    <div>
      <div className="border-b border-border bg-card px-8 py-4">
        <Link
          to={APP_ROUTES.fileList}
          className="inline-flex items-center gap-1 text-sm text-muted transition-colors hover:text-foreground"
        >
          <ChevronLeft size={16} aria-hidden />
          {t("workspace.back")}
        </Link>
      </div>
      <div className="p-8">
        <DocumentWorkspace documentId={id} />
      </div>
    </div>
  );
}

function SettingsOciRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </div>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </div>
  );
}

function SettingsParserAdaptersRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsParserAdapters")}
        subtitle={t("settings.parserAdapters.subtitle")}
      />
      <ParserAdapterSettingsClient />
    </div>
  );
}

function SettingsPreprocessRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsPreprocess")}
        subtitle={t("settings.preprocess.subtitle")}
      />
      <PreprocessSettingsClient />
    </div>
  );
}

function SettingsChunkingRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsChunking")}
        subtitle={t("settings.chunking.subtitle")}
      />
      <ChunkingSettingsClient />
    </div>
  );
}

function SettingsRetrievalRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsRetrieval")}
        subtitle={t("settings.retrieval.subtitle")}
      />
      <RetrievalSettingsClient />
    </div>
  );
}

function SettingsGroundingRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsGrounding")}
        subtitle={t("settings.grounding.subtitle")}
      />
      <GroundingSettingsClient />
    </div>
  );
}

function SettingsGenerationRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsGeneration")}
        subtitle={t("settings.generation.subtitle")}
      />
      <GenerationSettingsClient />
    </div>
  );
}

function SettingsGuardrailRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsGuardrail")}
        subtitle={t("settings.guardrail.subtitle")}
      />
      <GuardrailSettingsClient />
    </div>
  );
}

function SettingsVectorIndexRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsVectorIndex")}
        subtitle={t("settings.vectorIndex.subtitle")}
      />
      <VectorIndexSettingsClient />
    </div>
  );
}

function SettingsEvaluationRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsEvaluation")}
        subtitle={t("settings.evaluation.subtitle")}
      />
      <EvaluationSettingsClient />
    </div>
  );
}

function SettingsGraphRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsGraph")} subtitle={t("settings.graph.subtitle")} />
      <GraphSettingsClient />
    </div>
  );
}

function SettingsAgenticRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsAgentic")} subtitle={t("settings.agentic.subtitle")} />
      <AgenticSettingsClient />
    </div>
  );
}

function SettingsDatabaseRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </div>
  );
}
