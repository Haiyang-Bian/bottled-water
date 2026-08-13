import { describe, expect, it } from "vitest";
import {
  REDACTED,
  logPreview,
  sanitizeLogValue,
} from "../src/api/logRedaction";

describe("API log redaction", () => {
  it("redacts provider credentials from JSON request bodies", () => {
    const body = JSON.stringify({
      api_key: "sk-sensitive-provider-key",
      model: "deepseek-v4-flash",
      nested: { password: "secret-password" },
    });

    const sanitized = String(sanitizeLogValue(body));

    expect(sanitized).not.toContain("sk-sensitive-provider-key");
    expect(sanitized).not.toContain("secret-password");
    expect(sanitized).toContain(REDACTED);
    expect(sanitized).toContain("deepseek-v4-flash");
  });

  it("redacts access tokens and bearer credentials from response previews", () => {
    const preview = logPreview({
      access_token: "header.payload.signature",
      message: "Authorization: Bearer header.payload.signature",
    });

    expect(preview).not.toContain("header.payload.signature");
    expect(preview).toContain(REDACTED);
  });
});
