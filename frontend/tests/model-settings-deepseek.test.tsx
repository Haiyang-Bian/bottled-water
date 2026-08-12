import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/api";
import { ModelSettings } from "@/features/settings/components/ModelSettings";
import type { BuiltinProvider, ModelConfig, ModelProvider, User } from "@/types";

vi.mock("@/api", () => ({
  api: {
    modelProviders: vi.fn(),
    modelConfigs: vi.fn(),
    builtinProviders: vi.fn(),
    availableModels: vi.fn(),
    createModelConfig: vi.fn(),
    updateModelConfig: vi.fn(),
    updateModelProviderCredential: vi.fn(),
    deleteModelConfig: vi.fn(),
    activateModelConfig: vi.fn(),
    testModel: vi.fn(),
  },
}));

const provider: ModelProvider = {
  id: "provider-deepseek",
  name: "DeepSeek",
  provider_type: "deepseek",
  base_url: "https://api.deepseek.com",
  default_model: "deepseek-v4-flash",
  supports_streaming: true,
  supports_embeddings: false,
  api_key_set: true,
  status: "active",
};

const builtin: BuiltinProvider = {
  provider_type: "deepseek",
  name: "DeepSeek",
  base_url: "https://api.deepseek.com",
  default_model: "deepseek-v4-flash",
  supports_streaming: true,
  supports_embeddings: false,
  supports_tools: true,
  supports_thinking: true,
  reasoning_efforts: ["high", "max"],
  models: [
    { id: "deepseek-v4-flash", name: "DeepSeek V4 Flash" },
    { id: "deepseek-v4-pro", name: "DeepSeek V4 Pro" },
  ],
};

const config: ModelConfig = {
  id: "config-deepseek",
  provider_id: provider.id,
  provider_name: provider.name,
  name: "DeepSeek Chat",
  model_id: "deepseek-v4-flash",
  purpose: "chat",
  context_window: 128000,
  max_output_tokens: 4096,
  temperature_default: 0.4,
  config: {
    thinking_enabled: false,
    reasoning_effort: "high",
    api_key: "legacy-secret-must-not-render",
  },
  status: "active",
};

const user: User = {
  id: "user-1",
  name: "Member",
  role: "member",
};

function renderSettings() {
  return render(
    <AntApp>
      <ModelSettings
        message={{ success: vi.fn(), error: vi.fn() }}
        user={user}
        onUserUpdated={vi.fn()}
      />
    </AntApp>,
  );
}

describe("ModelSettings DeepSeek support", () => {
  it("shows the DeepSeek card and creates Flash by default", async () => {
    vi.mocked(api.modelProviders).mockResolvedValue([provider]);
    vi.mocked(api.modelConfigs).mockResolvedValue([]);
    vi.mocked(api.builtinProviders).mockResolvedValue([builtin]);
    vi.mocked(api.availableModels).mockResolvedValue([]);
    vi.mocked(api.createModelConfig).mockResolvedValue(config);

    renderSettings();

    expect(await screen.findByTestId("deepseek-provider-card")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /刷新模型/ }));
    await waitFor(() => expect(api.availableModels).toHaveBeenCalledWith(true));
    fireEvent.click(screen.getByRole("button", { name: "添加 DeepSeek" }));

    await waitFor(() =>
      expect(api.createModelConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          provider_type: "deepseek",
          model_id: "deepseek-v4-flash",
        }),
      ),
    );
    expect(await screen.findByTestId("deepseek-model-select")).toBeInTheDocument();
  });

  it("keeps credentials blank and links thinking mode to sampling", async () => {
    vi.mocked(api.modelProviders).mockResolvedValue([provider]);
    vi.mocked(api.modelConfigs).mockResolvedValue([config]);
    vi.mocked(api.builtinProviders).mockResolvedValue([builtin]);
    vi.mocked(api.availableModels).mockResolvedValue([]);
    vi.mocked(api.updateModelConfig).mockImplementation(async (_id, payload) => ({
      ...config,
      ...payload,
    }));
    vi.mocked(api.updateModelProviderCredential).mockResolvedValue({
      id: provider.id,
      api_key_set: true,
    });
    vi.mocked(api.testModel).mockResolvedValue({
      model: "deepseek-v4-flash",
      response: "ready",
    });

    renderSettings();
    fireEvent.click(await screen.findByText("DeepSeek Chat"));

    const keyInput = await screen.findByPlaceholderText("已配置；留空保留");
    expect(keyInput).toHaveValue("");
    expect(screen.queryByDisplayValue("legacy-secret-must-not-render")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-temperature-input")).not.toBeDisabled();
    expect(screen.getByTestId("deepseek-thinking-switch")).toHaveAttribute(
      "aria-checked",
      "false",
    );

    fireEvent.click(screen.getByTestId("deepseek-thinking-switch"));
    expect(screen.getByTestId("model-temperature-input")).toBeDisabled();

    const effortItem = screen.getByText("推理强度").closest(".ant-form-item");
    const effortSelector = effortItem?.querySelector(".ant-select-selector");
    fireEvent.mouseDown(effortSelector as Element);
    fireEvent.click(await screen.findByText("Max"));
    fireEvent.change(keyInput, { target: { value: "new-deepseek-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() =>
      expect(api.updateModelConfig).toHaveBeenCalledWith(
        config.id,
        expect.objectContaining({
          config: expect.objectContaining({
            thinking_enabled: true,
            reasoning_effort: "max",
          }),
        }),
      ),
    );
    expect(api.updateModelProviderCredential).toHaveBeenCalledWith(
      provider.id,
      "new-deepseek-secret",
    );

    fireEvent.change(screen.getByPlaceholderText("输入测试提示词"), {
      target: { value: "ping" },
    });
    fireEvent.click(screen.getByRole("button", { name: /测试/ }));
    await waitFor(() =>
      expect(api.testModel).toHaveBeenCalledWith("ping", config.id),
    );
  });
});
