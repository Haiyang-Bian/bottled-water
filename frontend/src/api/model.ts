import { del, get, patch, post } from "./client";
import type { ModelProvider, ModelConfig, BuiltinProvider } from "@/types";

export async function modelProviders(): Promise<ModelProvider[]> {
  const result = await get<{ items: ModelProvider[] }>(
    "/model-providers",
  );
  return result.items;
}

export async function builtinProviders(): Promise<BuiltinProvider[]> {
  const result = await get<{ items: BuiltinProvider[] }>(
    "/model-providers/builtin",
  );
  return result.items;
}

export async function createModelProvider(payload: {
  name: string;
  provider_type: string;
  base_url: string;
  api_key?: string;
  default_model: string;
  supports_streaming?: boolean;
  supports_embeddings?: boolean;
  config?: Record<string, unknown>;
}): Promise<ModelProvider> {
  return await post<ModelProvider>("/model-providers", payload);
}

export async function updateModelProvider(
  id: string,
  payload: {
    name: string;
    provider_type: string;
    base_url: string;
    api_key?: string;
    default_model: string;
    supports_streaming?: boolean;
    supports_embeddings?: boolean;
    config?: Record<string, unknown>;
  },
): Promise<ModelProvider> {
  return await patch<ModelProvider>(`/model-providers/${id}`, payload);
}

export async function updateModelProviderCredential(
  id: string,
  apiKey: string,
): Promise<{ id: string; api_key_set: boolean }> {
  return await patch<{ id: string; api_key_set: boolean }>(
    `/model-providers/${id}/credential`,
    { api_key: apiKey },
  );
}

export async function deleteModelProvider(id: string): Promise<{ id: string; deleted: boolean }> {
  return await del<{ id: string; deleted: boolean }>(`/model-providers/${id}`);
}

export async function modelConfigs(): Promise<ModelConfig[]> {
  const result = await get<{ items: ModelConfig[] }>("/model-configs");
  return result.items;
}

export async function deleteModelConfig(id: string): Promise<{ id: string; deleted: boolean }> {
  return await del<{ id: string; deleted: boolean }>(`/model-configs/${id}`);
}

export async function createModelConfig(payload: {
  provider_id?: string;
  provider_type?: string;
  name: string;
  model_id: string;
  purpose: string;
  context_window?: number;
  max_output_tokens?: number;
  temperature_default?: number;
  config?: Record<string, unknown>;
}): Promise<ModelConfig> {
  return await post<ModelConfig>("/model-configs", payload);
}

export async function updateModelConfig(
  id: string,
  payload: {
    name?: string;
    model_id?: string;
    purpose?: string;
    context_window?: number;
    max_output_tokens?: number;
    temperature_default?: number;
    config?: Record<string, unknown>;
  },
): Promise<ModelConfig> {
  return await patch<ModelConfig>(`/model-configs/${id}`, payload);
}

export async function testModel(
  prompt: string,
  model_config_id?: string,
): Promise<{
  response: string;
  model: string;
  usage?: Record<string, unknown>;
}> {
  return await post<{
    response: string;
    model: string;
    usage?: Record<string, unknown>;
  }>("/model-configs/test", { prompt, model_config_id });
}

export interface AvailableModel {
  provider_id: string;
  provider_name: string;
  model_id: string;
  config_id: string;
  name: string;
  context_window: number;
  status: string;
}

export async function availableModels(forceRefresh = false): Promise<AvailableModel[]> {
  const result = await get<{ items: AvailableModel[] }>(
    `/models/available${forceRefresh ? "?force_refresh=true" : ""}`,
  );
  return result.items;
}

export async function activateModelConfig(id: string): Promise<{
  default_model_config_id: string;
  name: string;
  model_id: string;
}> {
  const result = await post<{
    default_model_config_id: string;
    name: string;
    model_id: string;
  }>(`/model-configs/${id}/activate`, {});
  return result;
}
