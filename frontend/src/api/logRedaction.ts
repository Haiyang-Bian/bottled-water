const SENSITIVE_FIELD = /^(?:api[_-]?key|authorization|password|secret|token|access[_-]?token|refresh[_-]?token|credential|cookie)$/i;
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi;
const PROVIDER_KEY = /\bsk-[A-Za-z0-9_-]{12,}\b/gi;

export const REDACTED = "<redacted>";

export function sanitizeLogValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeLogValue(item));
  }
  if (value && typeof value === "object") {
    if (value instanceof FormData) return "<FormData>";
    if (value instanceof Blob) return `<Blob:${value.type || "unknown"}>`;
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = SENSITIVE_FIELD.test(key) ? REDACTED : sanitizeLogValue(item);
    }
    return result;
  }
  if (typeof value !== "string") return value;

  const trimmed = value.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.stringify(sanitizeLogValue(JSON.parse(value)));
    } catch {
      // Fall through to conservative text redaction for malformed JSON.
    }
  }
  return value.replace(BEARER_TOKEN, "Bearer <redacted>").replace(PROVIDER_KEY, REDACTED);
}

export function logPreview(value: unknown, maxLength = 500): string {
  const sanitized = sanitizeLogValue(value);
  const text =
    typeof sanitized === "string" ? sanitized : JSON.stringify(sanitized);
  return String(text ?? "").slice(0, maxLength);
}
