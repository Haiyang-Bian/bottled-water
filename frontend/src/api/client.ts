import { logger } from "@/utils/logger";
import { apiBaseUrl, normalizeBackendUrls } from "@/config/desktopRuntime";
import { logPreview, sanitizeLogValue } from "./logRedaction";

export const API_BASE = apiBaseUrl();

/** 统一 API 错误类型。 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: number,
    message: string,
    public readonly data?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 从 localStorage 读取认证 token。 */
export function getAuthToken(): string | null {
  return window.localStorage.getItem("agenthub_token");
}

export function unwrap<T>(payload: unknown): T {
  if (
    payload &&
    typeof payload === "object" &&
    "code" in payload &&
    "data" in payload
  ) {
    return normalizeBackendUrls((payload as { data: T }).data);
  }
  return normalizeBackendUrls(payload as T);
}

/** 请求拦截器：在请求发送前执行。 */
export type RequestInterceptor = (path: string, init: RequestInit) => void;
/** 响应拦截器：在请求成功后执行。 */
export type ResponseInterceptor = <T>(
  path: string,
  response: Response,
  data: T,
) => void;
/** 错误拦截器：在请求失败后执行。 */
export type ErrorInterceptor = (path: string, error: ApiError) => void;

export const requestInterceptors: RequestInterceptor[] = [];
export const responseInterceptors: ResponseInterceptor[] = [];
export const errorInterceptors: ErrorInterceptor[] = [];

/** 默认注册日志拦截器，自动记录所有 API 请求和响应。 */
requestInterceptors.push((path, init) => {
  logger.debug("api", `${init.method ?? "GET"} ${path}`, {
    body: sanitizeLogValue(init.body),
  });
});

responseInterceptors.push((path, _response, _data) => {
  logger.debug("api", `OK ${path}`);
});

errorInterceptors.push((path, error) => {
  logger.error("api", `FAIL ${path}: ${error.message}`, {
    status: error.status,
    code: error.code,
  });
});

export async function request<T>(
  path: string,
  init?: RequestInit,
  controller?: AbortController,
): Promise<T> {
  const token = getAuthToken();
  const isForm = init?.body instanceof FormData;

  if (!controller) {
    controller = new AbortController();
  }

  const mergedInit: RequestInit = {
    ...init,
    signal: controller.signal,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  };

  requestInterceptors.forEach((fn) => fn(path, mergedInit));

  const start = performance.now();
  try {
    const response = await fetch(`${API_BASE}${path}`, mergedInit);
    const duration = Math.round(performance.now() - start);

    if (!response.ok) {
      let detail = response.statusText;
      let payload: Record<string, unknown> | undefined;

      try {
        payload = await response.clone().json();
        detail = String(
          payload?.message || payload?.detail || payload?.error || detail,
        );
      } catch {
        try {
          detail = await response.clone().text();
        } catch {
          detail = response.statusText;
        }
      }
      const err = new ApiError(
        response.status,
        Number(payload?.code ?? 0),
        `${response.status} ${detail}`,
        payload,
      );
      errorInterceptors.forEach((fn) => fn(path, err));
      throw err;
    }

    const data = unwrap<T>(await response.json());
    responseInterceptors.forEach((fn) => fn(path, response, data));
    const dataPreview = logPreview(data);
    logger.debug("api", `${response.status} ${path} (${duration}ms)`, {
      response: dataPreview,
    });
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    const err = new ApiError(
      0,
      0,
      error instanceof Error ? error.message : "网络请求失败",
    );
    errorInterceptors.forEach((fn) => fn(path, err));
    throw err;
  }
}

/** HTTP 快捷方法。 */
export const get = <T>(path: string) => request<T>(path);
export const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

/** SSE协议。 */
export async function sse(
  path: string,
  body: unknown,
  controller?: AbortController,
) {
  if (!controller) {
    controller = new AbortController();
  }

  const token = getAuthToken();

  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    signal: controller?.signal,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "Content-Type": "application/json", // ← 你发送的 body 格式
      Accept: "text/event-stream", // ← 告诉后端：我要 SSE 流
    },
  });

  return response;
}

export async function requestWithTimeout<T>(
  path: string,
  init: RequestInit,
  timeoutMs = 7000,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await request<T>(path, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

export async function requestFile(path: string): Promise<{
  previewUrl?: string;
  previewText?: string;
  contentType: string;
  filename?: string;
}> {
  const token = getAuthToken();
  const response = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });

  if (!response.ok) {
    throw new ApiError(
      response.status,
      0,
      `${response.status} ${response.statusText}`,
    );
  }

  const contentType =
    response.headers.get("content-type") ?? "application/octet-stream";
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = parseContentDispositionFilename(disposition);

  if (isTextualResponse(contentType)) {
    return { previewText: await response.text(), contentType, filename };
  }

  return {
    previewUrl: URL.createObjectURL(await response.blob()),
    contentType,
    filename,
  };
}

function isTextualResponse(contentType: string) {
  const value = contentType.toLowerCase();
  if (value.includes("openxmlformats-officedocument")) return false;
  if (value.includes("application/pdf")) return false;
  if (value.includes("application/zip")) return false;
  if (value.includes("application/octet-stream")) return false;
  return (
    value.startsWith("text/") ||
    value.includes("json") ||
    value.includes("xml")
  );
}

function parseContentDispositionFilename(disposition: string) {
  const encoded = /filename\*\s*=\s*([^']*)''([^;]+)/i.exec(disposition);
  if (encoded?.[2]) {
    try {
      return decodeURIComponent(encoded[2].trim().replace(/^"|"$/g, ""));
    } catch {
      return encoded[2].trim().replace(/^"|"$/g, "");
    }
  }
  return /filename\s*=\s*"?([^";]+)"?/i.exec(disposition)?.[1];
}

export const wait = (ms: number) =>
  new Promise((resolve) => window.setTimeout(resolve, ms));
