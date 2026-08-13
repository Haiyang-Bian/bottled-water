const DESKTOP_PORT_PARAMETER = "desktopApiPort";

export function resolveDesktopApiOrigin(search = window.location.search): string | null {
  const rawPort = new URLSearchParams(search).get(DESKTOP_PORT_PARAMETER);
  if (!rawPort || !/^\d+$/.test(rawPort)) return null;
  const port = Number(rawPort);
  if (port < 1024 || port > 65535) return null;
  return `http://127.0.0.1:${port}`;
}

export function apiBaseUrl(search = window.location.search): string {
  return `${resolveDesktopApiOrigin(search) ?? ""}/api/v1`;
}

export function websocketBaseUrl(
  search = window.location.search,
  location = window.location,
): string {
  const desktopOrigin = resolveDesktopApiOrigin(search);
  if (desktopOrigin) return desktopOrigin.replace(/^http/, "ws");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}`;
}

export function isDesktopRuntime(search = window.location.search): boolean {
  return resolveDesktopApiOrigin(search) !== null;
}

export function resolveBackendUrl(
  value: string,
  search = window.location.search,
): string {
  const origin = resolveDesktopApiOrigin(search);
  if (!origin || !value.startsWith("/api/")) return value;
  return `${origin}${value}`;
}

export function normalizeBackendUrls<T>(
  value: T,
  search = window.location.search,
): T {
  if (typeof value === "string") {
    return resolveBackendUrl(value, search) as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeBackendUrls(item, search)) as T;
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        normalizeBackendUrls(item, search),
      ]),
    ) as T;
  }
  return value;
}

export async function waitForDesktopBackend(
  search = window.location.search,
  options: { attempts?: number; delayMs?: number } = {},
): Promise<void> {
  const origin = resolveDesktopApiOrigin(search);
  if (!origin) return;
  const attempts = options.attempts ?? 120;
  const delayMs = options.delayMs ?? 250;
  let lastError: unknown;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(`${origin}/health`, { cache: "no-store" });
      if (response.ok) return;
      lastError = new Error(`backend health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("AgentHub 本地后端启动超时");
}
