import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { PageHeader } from "@/components/PageHeader";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";
import { Nl2SqlWorkbench } from "@/features/nl2sql/Nl2SqlWorkbench";
import { DataManagementPage } from "@/features/nl2sql/pages/DataManagementPage";
import { EngineOperationsPage } from "@/features/nl2sql/pages/EngineOperationsPage";
import { EvaluationPage } from "@/features/nl2sql/pages/EvaluationPage";
import { GlossaryRulesPage } from "@/features/nl2sql/pages/GlossaryRulesPage";
import { HistoryPage } from "@/features/nl2sql/pages/HistoryPage";
import { LearningPage } from "@/features/nl2sql/pages/LearningPage";
import { QuestionLearningPage } from "@/features/nl2sql/pages/QuestionLearningPage";
import {
  AnnotationManagementPage,
  CommentManagementPage,
} from "@/features/nl2sql/pages/MetadataSqlManagementPage";
import { ProfileManagementPage } from "@/features/nl2sql/pages/ProfileManagementPage";
import { SampleDataPage } from "@/features/nl2sql/pages/SampleDataPage";
import { TableManagementPage } from "@/features/nl2sql/pages/TableManagementPage";
import { ViewManagementPage } from "@/features/nl2sql/pages/ViewManagementPage";
import {
  DatabaseSettingsPage as Nl2SqlDatabaseSettingsPage,
  ModelSettingsPage as Nl2SqlModelSettingsPage,
} from "@/features/nl2sql/pages/SettingsPages";
import { SqlAnalysisPage } from "@/features/nl2sql/pages/SqlAnalysisPage";
import { SqlToQuestionPage } from "@/features/nl2sql/pages/SqlToQuestionPage";

export function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path={APP_ROUTES.dashboard} element={<Navigate to={APP_ROUTES.query} replace />} />
        <Route path={APP_ROUTES.tableManagement} element={<TableManagementPage />} />
        <Route path={APP_ROUTES.viewManagement} element={<ViewManagementPage />} />
        <Route path={APP_ROUTES.dataManagement} element={<DataManagementPage />} />
        <Route path={APP_ROUTES.sampleData} element={<SampleDataPage />} />
        <Route path={APP_ROUTES.commentManagement} element={<CommentManagementPage />} />
        <Route path={APP_ROUTES.annotationManagement} element={<AnnotationManagementPage />} />
        {/* 旧ルート互換: スキーマ管理はテーブルの管理へ、データ投入はデータの管理へ */}
        <Route path="/schema" element={<Navigate to={APP_ROUTES.tableManagement} replace />} />
        <Route path="/data-tools" element={<Navigate to={APP_ROUTES.dataManagement} replace />} />
        <Route path={APP_ROUTES.query} element={<Nl2SqlWorkbench />} />
        <Route path={APP_ROUTES.profiles} element={<ProfileManagementPage />} />
        <Route path={APP_ROUTES.glossaryRules} element={<GlossaryRulesPage />} />
        <Route path={APP_ROUTES.sqlAnalysis} element={<SqlAnalysisPage />} />
        <Route path={APP_ROUTES.sqlToQuestion} element={<SqlToQuestionPage />} />
        <Route path={APP_ROUTES.learning} element={<LearningPage />} />
        <Route path={APP_ROUTES.questionLearning} element={<QuestionLearningPage />} />
        <Route path={APP_ROUTES.history} element={<HistoryPage />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationPage />} />
        <Route path={APP_ROUTES.engineOperations} element={<EngineOperationsPage />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route
          path={APP_ROUTES.settingsUploadStorage}
          element={<SettingsUploadStorageRoute />}
        />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route
          path={APP_ROUTES.nl2sqlSettingsConnection}
          element={<Navigate to={APP_ROUTES.engineOperations} replace />}
        />
        <Route
          path={APP_ROUTES.nl2sqlSettingsModel}
          element={<Nl2SqlModelSettingsPage />}
        />
        <Route
          path={APP_ROUTES.nl2sqlSettingsDatabase}
          element={<Nl2SqlDatabaseSettingsPage />}
        />
        <Route path="/settings" element={<Navigate to={APP_ROUTES.settingsOci} replace />} />
      </Routes>
    </AppLayout>
  );
}

function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigationType = useNavigationType();
  const mainRef = useRef<HTMLElement | null>(null);
  const setSidebarCollapsed = useUiStore((state) => state.setSidebarCollapsed);

  useCollapseSidebarOnNarrowViewport(setSidebarCollapsed);
  useMainScrollRestoration(mainRef, location, navigationType);

  return (
    <div className="flex">
      <AppSidebar />
      <main
        ref={mainRef}
        className="h-screen min-w-0 flex-1 overflow-y-auto [contain:layout] focus:outline-none"
        aria-label="メイン領域"
        tabIndex={-1}
      >
        {children}
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

function SettingsOciRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <>
      <PageHeader
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </>
  );
}

function SettingsDatabaseRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </>
  );
}
