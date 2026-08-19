import { useEffect, useState } from "react";
import { App as AntApp, Spin } from "antd";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "@/api";
import type { User } from "@/types";
import { LoginRoute } from "./LoginRoute";
import { WorkbenchRoute } from "./WorkbenchRoute";
import { DocsPage } from "@/pages/DocsPage";
import { ProductReleasePage } from "@/pages/ProductReleasePage";
import { isDesktopRuntime } from "@/config/desktopRuntime";

export function AppRouter() {
  const [user, setUser] = useState<User>();
  const [authReady, setAuthReady] = useState(false);
  const [desktopAuthError, setDesktopAuthError] = useState<string>();
  const desktopMode = isDesktopRuntime();

  useEffect(() => {
    const token = window.localStorage.getItem("agenthub_token");
    if (!token && !desktopMode) {
      setAuthReady(true);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch((error) => {
        window.localStorage.removeItem("agenthub_token");
        if (desktopMode) {
          setDesktopAuthError(
            error instanceof Error ? error.message : "本机身份初始化失败",
          );
        }
      })
      .finally(() => setAuthReady(true));
  }, [desktopMode]);

  if (!authReady) {
    return (
      <AntApp>
        <main className="login-shell">
          <Spin tip="Restoring session..." />
        </main>
      </AntApp>
    );
  }

  if (desktopMode && desktopAuthError) {
    return (
      <AntApp>
        <main className="desktop-bootstrap desktop-bootstrap--error">
          <strong>本机身份初始化失败</strong>
          <span>{desktopAuthError}</span>
        </main>
      </AntApp>
    );
  }

  return (
    <AntApp>
      <Routes>
        <Route
          path="/"
          element={
            desktopMode ? <Navigate to="/app" replace /> : <ProductReleasePage />
          }
        />
        <Route path="/release" element={<ProductReleasePage />} />
        <Route
          path="/login"
          element={<LoginRoute user={user} onLogin={setUser} />}
        />
        <Route
          path="/app"
          element={
            <WorkbenchRoute
              user={user}
              onLogout={() => {
                api.logout().finally(() => setUser(undefined));
              }}
            />
          }
        />
        <Route
          path="/app/:workspaceId"
          element={
            <WorkbenchRoute
              user={user}
              onLogout={() => {
                api.logout().finally(() => setUser(undefined));
              }}
            />
          }
        />
        <Route
          path="/app/:workspaceId/c/:conversationId"
          element={
            <WorkbenchRoute
              user={user}
              onLogout={() => {
                api.logout().finally(() => setUser(undefined));
              }}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/files"
          element={
            <WorkbenchRoute
              user={user}
              forcedTab="files"
              onLogout={() => {
                api.logout().finally(() => setUser(undefined));
              }}
            />
          }
        />
        <Route path="/docs" element={<DocsPage />} />
        <Route
          path="*"
          element={<Navigate to={user ? "/app" : "/login"} replace />}
        />
      </Routes>
    </AntApp>
  );
}
