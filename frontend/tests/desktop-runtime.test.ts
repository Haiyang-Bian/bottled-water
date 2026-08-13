import { describe, expect, it } from "vitest";

import {
  apiBaseUrl,
  normalizeBackendUrls,
  resolveBackendUrl,
  resolveDesktopApiOrigin,
  websocketBaseUrl,
} from "../src/config/desktopRuntime";

describe("desktop runtime addresses", () => {
  it("uses the Tauri sidecar port for HTTP and WebSocket traffic", () => {
    const search = "?desktopApiPort=18765";

    expect(resolveDesktopApiOrigin(search)).toBe("http://127.0.0.1:18765");
    expect(apiBaseUrl(search)).toBe("http://127.0.0.1:18765/api/v1");
    expect(websocketBaseUrl(search)).toBe("ws://127.0.0.1:18765");
  });

  it("keeps relative web API behavior outside Tauri", () => {
    expect(resolveDesktopApiOrigin("")).toBeNull();
    expect(apiBaseUrl("")).toBe("/api/v1");
    expect(
      websocketBaseUrl("", {
        protocol: "https:",
        host: "agenthub.example",
      } as Location),
    ).toBe("wss://agenthub.example");
  });

  it("rejects unsafe or invalid port values", () => {
    expect(resolveDesktopApiOrigin("?desktopApiPort=80")).toBeNull();
    expect(resolveDesktopApiOrigin("?desktopApiPort=not-a-port")).toBeNull();
    expect(resolveDesktopApiOrigin("?desktopApiPort=70000")).toBeNull();
  });

  it("rewrites backend resource URLs without touching external URLs", () => {
    const search = "?desktopApiPort=18765";
    expect(resolveBackendUrl("/api/v1/files/one", search)).toBe(
      "http://127.0.0.1:18765/api/v1/files/one",
    );
    expect(
      normalizeBackendUrls(
        {
          preview_url: "/api/v1/artifacts/one/preview",
          nested: [{ download_url: "/api/v1/files/one/download" }],
          external: "https://example.test/file",
          object: "blob:preview",
        },
        search,
      ),
    ).toEqual({
      preview_url: "http://127.0.0.1:18765/api/v1/artifacts/one/preview",
      nested: [
        { download_url: "http://127.0.0.1:18765/api/v1/files/one/download" },
      ],
      external: "https://example.test/file",
      object: "blob:preview",
    });
  });
});
