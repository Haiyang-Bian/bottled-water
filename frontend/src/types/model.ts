export interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  default_model: string;
  supports_streaming: boolean;
  supports_embeddings: boolean;
  api_key_set?: boolean;
  status: string;
  config?: Record<string, unknown>;
  models?: ModelConfig[];
  created_at?: string;
  updated_at?: string;
}

export interface BuiltinProvider {
  provider_type: string;
  name: string;
  base_url: string;
  default_model: string;
  supports_streaming: boolean;
  supports_embeddings: boolean;
  supports_tools?: boolean;
  supports_thinking?: boolean;
  reasoning_efforts?: Array<"high" | "max">;
  models?: Array<{ id: string; name: string }>;
}

export interface ModelConfig {
  id: string;
  provider_id: string;
  provider_name?: string;
  name: string;
  model_id: string;
  purpose: string;
  context_window: number;
  max_output_tokens: number;
  temperature_default: number;
  config?: Record<string, unknown>;
  status: string;
  created_at?: string;
  updated_at?: string;
}
