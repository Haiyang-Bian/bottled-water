import { beforeEach, describe, expect, it, vi } from "vitest";
import { isDesktopRuntime } from "@/config/desktopRuntime";
import { selectDesktopDirectory } from "@/lib/desktopDirectory";
import { open } from "@tauri-apps/plugin-dialog";

vi.mock("@/config/desktopRuntime", () => ({ isDesktopRuntime: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));

describe("desktop directory selection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the native Tauri directory dialog in desktop mode", async () => {
    vi.mocked(isDesktopRuntime).mockReturnValue(true);
    vi.mocked(open).mockResolvedValue("C:/project");

    await expect(selectDesktopDirectory()).resolves.toBe("C:/project");
    expect(open).toHaveBeenCalledWith({ directory: true, multiple: false });
  });

  it("does not pretend a browser can choose a server-local path", async () => {
    vi.mocked(isDesktopRuntime).mockReturnValue(false);

    await expect(selectDesktopDirectory()).resolves.toBeNull();
    expect(open).not.toHaveBeenCalled();
  });
});
