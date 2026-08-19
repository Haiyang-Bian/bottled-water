import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@/types";

const apiMocks = vi.hoisted(() => ({
  me: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: apiMocks,
}));

vi.mock("../src/router/WorkbenchRoute", () => ({
  WorkbenchRoute: ({ user }: { user?: User }) => (
    <div data-testid="desktop-workbench">{user?.username}</div>
  ),
}));

vi.mock("../src/router/LoginRoute", () => ({
  LoginRoute: () => <div data-testid="login-route">login</div>,
}));

vi.mock("@/pages/DocsPage", () => ({
  DocsPage: () => <div>docs</div>,
}));

vi.mock("@/pages/ProductReleasePage", () => ({
  ProductReleasePage: () => <div>release</div>,
}));

import { AppRouter } from "../src/router/AppRouter";

const sessionToken = "a".repeat(64);
const desktopSearch = `?desktopApiPort=18765&desktopSession=${sessionToken}`;

describe("desktop single-user bootstrap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", `/${desktopSearch}`);
  });

  it("opens the workbench through the local identity without a browser token", async () => {
    apiMocks.me.mockResolvedValue({
      id: "local-user-id",
      username: "local-user",
    } as User);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppRouter />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("desktop-workbench")).toHaveTextContent(
      "local-user",
    );
    expect(apiMocks.me).toHaveBeenCalledOnce();
    expect(window.localStorage.getItem("agenthub_token")).toBeNull();
    expect(screen.queryByTestId("login-route")).not.toBeInTheDocument();
  });

  it("shows a local bootstrap error instead of account login", async () => {
    apiMocks.me.mockRejectedValue(new Error("sidecar unavailable"));

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppRouter />
      </MemoryRouter>,
    );

    expect(await screen.findByText("本机身份初始化失败")).toBeInTheDocument();
    expect(screen.getByText("sidecar unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("login-route")).not.toBeInTheDocument();
  });
});
