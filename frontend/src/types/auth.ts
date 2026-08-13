export interface User {
  id: string;
  name: string;
  avatar?: string;
  avatar_url?: string;
  signature?: string;
  role: "member" | "agent_provider" | "developer" | "admin" | string;
  default_model_config_id?: string;
}
