import { isDesktopRuntime } from "@/config/desktopRuntime";

export async function selectDesktopDirectory(): Promise<string | null> {
  if (!isDesktopRuntime()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ directory: true, multiple: false });
  return typeof selected === "string" ? selected : null;
}
