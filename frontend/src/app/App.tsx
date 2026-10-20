import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { EmptyState, LoadingState } from "../shared/components/ui";

const OverviewPage = lazy(() =>
  import("../features/overview/OverviewPage").then((module) => ({ default: module.OverviewPage }))
);
const AssetsPage = lazy(() =>
  import("../features/assets/AssetsPage").then((module) => ({ default: module.AssetsPage }))
);
const DiagnosisPage = lazy(() =>
  import("../features/diagnosis/DiagnosisPage").then((module) => ({ default: module.DiagnosisPage }))
);
const KnowledgePage = lazy(() =>
  import("../features/knowledge/KnowledgePage").then((module) => ({ default: module.KnowledgePage }))
);
const SettingsPage = lazy(() =>
  import("../features/settings/SettingsPage").then((module) => ({ default: module.SettingsPage }))
);

export function App() {
  return (
    <Suspense fallback={<LoadingState label="正在加载页面模块" />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/diagnosis" element={<DiagnosisPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="*"
            element={
              <EmptyState
                title="页面不存在"
                description="该地址没有对应的 NetOps Console 页面。"
                action={<a className="button button-primary" href="./overview">返回总览</a>}
              />
            }
          />
        </Route>
      </Routes>
    </Suspense>
  );
}
