import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import { isDesktopRuntime, waitForDesktopBackend } from "./config/desktopRuntime";
import "./styles/index.scss";

const root = ReactDOM.createRoot(document.getElementById("root")!);

function application() {
  return (
    <React.StrictMode>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: {
            colorPrimary: "#1677ff",
            borderRadius: 8,
            fontFamily:
              'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          },
        }}
      >
        <App />
      </ConfigProvider>
    </React.StrictMode>
  );
}

async function bootstrap() {
  if (isDesktopRuntime()) {
    root.render(
      <div className="desktop-bootstrap">
        <div className="desktop-bootstrap__spinner" />
        <strong>正在启动 AgentHub 本地服务…</strong>
        <span>首次启动需要初始化数据库，请稍候。</span>
      </div>,
    );
    try {
      await waitForDesktopBackend();
    } catch (error) {
      root.render(
        <div className="desktop-bootstrap desktop-bootstrap--error">
          <strong>本地服务启动失败</strong>
          <span>{error instanceof Error ? error.message : "请重新启动 AgentHub。"}</span>
        </div>,
      );
      return;
    }
  }
  root.render(application());
}

void bootstrap();
