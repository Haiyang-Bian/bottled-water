/** 前端日志模块。
 *
 * 单例模式，内存队列缓冲，批量上报到后端 /api/v1/logs。
 * 页面关闭时通过 sendBeacon 发送剩余日志，避免丢失。
 * 队列上限 200 条，超限丢弃最早的。
 */
import {
  apiBaseUrl,
  desktopSessionToken,
} from "@/config/desktopRuntime";

export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

interface LogEntry {
  timestamp: string;
  level: LogLevel;
  module: string;
  message: string;
  data?: unknown;
  url?: string;
  user_agent?: string;
}

class Logger {
  private queue: LogEntry[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly MAX_QUEUE = 200;
  private readonly BATCH_SIZE = 50;
  private readonly FLUSH_INTERVAL = 5000;

  constructor() {
    this.timer = setInterval(() => this.flush(), this.FLUSH_INTERVAL);
    window.addEventListener("beforeunload", () => this.sendBeacon());
  }

  /** 记录一条日志。 */
  log(level: LogLevel, module: string, message: string, data?: unknown) {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level,
      module,
      message,
      data: data ?? undefined,
      url: typeof window !== "undefined" ? window.location.href : undefined,
      user_agent: typeof navigator !== "undefined" ? navigator.userAgent : undefined,
    };
    this.queue.push(entry);
    if (this.queue.length > this.MAX_QUEUE) {
      this.queue.shift();
    }
    if (this.queue.length >= this.BATCH_SIZE) {
      this.flush();
    }
  }

  debug(module: string, message: string, data?: unknown) {
    this.log("DEBUG", module, message, data);
  }

  info(module: string, message: string, data?: unknown) {
    this.log("INFO", module, message, data);
  }

  warn(module: string, message: string, data?: unknown) {
    this.log("WARN", module, message, data);
  }

  error(module: string, message: string, data?: unknown) {
    this.log("ERROR", module, message, data);
  }

  /** 获取认证 token。 */
  private getToken(): string | null {
    return window.localStorage.getItem("agenthub_token");
  }

  /** 立即发送队列中的日志（批量）。 */
  private async flush() {
    if (this.queue.length === 0) return;
    const batch = this.queue.splice(0, this.BATCH_SIZE);
    const token = this.getToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const desktopSession = desktopSessionToken();
    if (desktopSession) {
      headers["X-AgentHub-Desktop-Session"] = desktopSession;
    }
    try {
      await fetch(`${apiBaseUrl()}/logs`, {
        method: "POST",
        headers,
        body: JSON.stringify({ logs: batch }),
        keepalive: true,
      });
    } catch {
      // 静默丢弃，避免循环报错或阻塞业务
    }
  }

  /** 页面卸载时通过 sendBeacon 发送剩余日志。
   *
   * sendBeacon 不支持自定义 header，因此通过 URL query param 传递 token。
   */
  sendBeacon() {
    if (this.queue.length === 0) return;
    const token = this.getToken();
    const desktopSession = desktopSessionToken();
    const query = desktopSession
      ? `desktop_session=${encodeURIComponent(desktopSession)}`
      : token
        ? `token=${encodeURIComponent(token)}`
        : "";
    const url = `${apiBaseUrl()}/logs${query ? `?${query}` : ""}`;
    const blob = new Blob([JSON.stringify({ logs: this.queue })], {
      type: "application/json",
    });
    navigator.sendBeacon(url, blob);
    this.queue = [];
  }
}

export const logger = new Logger();
