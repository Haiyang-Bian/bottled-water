import type { ConversationWorkflow, WorkflowRun } from "./workflow";

export type MessageRole = "user" | "assistant" | "system" | "tool";
export type MessageKind =
  | "text"
  | "code"
  | "file"
  | "event"
  | "error"
  | "preview_card"
  | "diff_panel"
  | "deploy_status_card";
export type StreamState = "idle" | "streaming" | "done" | "error";

export interface Participant {
  id?: string;
  participant_type?: "agent" | "user";
  user_id?: string;
  agent_id?: string;
  agent_name?: string;
  agent_type?: string;
  agent_status?: string;
  agent_avatar_url?: string;
  role?: "owner" | "admin" | "member";
  nickname?: string;
  joined_at?: string;
  left_at?: string;
}

export interface Conversation {
  id: string;
  chat_type?: "single" | "group";
  conversation_number?: string | null;
  group_number?: string | null;
  title: string;
  participants: Participant[];
  participant_count?: number;
  agent_count?: number;
  user_count?: number;
  master_enabled?: boolean;
  updatedAt: string;
  pinned: boolean;
  archived: boolean;
  unread: number;
  tags: string[];
  category?: string;
  folder?: string;
  remark?: string;
  workspace_id?: string;
  lastMessage: string;
  workflow?: ConversationWorkflow;
  workflow_runtime?: WorkflowRun;
  generation_status?: "idle" | "running" | "failed" | "cancelled" | string;
  active_run_id?: string | null;
  scheduling_strategy?: "workflow" | "tech_lead" | "single_agent" | "collaborative" | string;
  workflow_enabled?: boolean;
  team_settings?: TeamSettings | null;
  team_activity?: TeamMessage[];
  team_last_sequence?: number;
  runtime?: ConversationRuntime;
}

export interface TeamSettings {
  summary_agent_id?: string | null;
  live_user_input: boolean;
  max_collaboration_messages: number;
  max_agent_turns: number;
  max_open_threads: number;
  max_team_message_chars: number;
}

export interface TeamMessage {
  message_id: string;
  run_id: string;
  conversation_id: string;
  sequence: number;
  sender_type: "agent" | "user" | string;
  sender_id: string;
  recipient_agent_ids: string[];
  channel: "direct" | "broadcast" | string;
  thread_id?: string | null;
  reply_to_message_id?: string | null;
  content: string;
  expects_reply: boolean;
  status: "pending" | "consumed" | "resolved" | "interrupted" | string;
  consumed_by: string[];
  created_at: string;
  resolved_at?: string | null;
  replayed?: boolean;
}

export interface ConversationRepositoryBinding {
  id: string;
  conversation_id: string;
  repository_path: string;
  base_commit: string;
  require_user_approval: boolean;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AgentWorktree {
  id: string;
  conversation_id: string;
  agent_id: string;
  path: string;
  branch: string;
  base_commit: string;
  head_commit: string;
  mode: "managed" | "adopted";
  status: string;
  dirty: boolean;
  merge_status: string;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationRepositoryState {
  repository: ConversationRepositoryBinding | null;
  worktrees: AgentWorktree[];
  integration?: Record<string, unknown>;
}

export interface ConversationRuntimeAgentRun {
  agent_id: string;
  agent_name?: string;
  role?: string;
  status?: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  output_preview?: string;
  rationale?: string;
  current_task?: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  tool_events?: unknown[];
  tool_count?: number;
}

export interface ConversationRuntimeTaskPlanItem {
  id?: string;
  agent_id?: string;
  agent_name?: string;
  role?: string;
  priority?: number;
  stage?: number;
  depends_on?: string[];
  status?: string;
  task?: string;
  assigned_task?: string;
  expected_outputs?: string[];
  rationale?: string;
  output_preview?: string;
  confidence?: number;
  blockers?: string[];
  tool_events?: unknown[];
}

export interface ConversationRuntimeSummary {
  status?: string;
  task?: string;
  plan?: ConversationRuntimeTaskPlanItem[];
  agent_outputs?: Record<string, unknown>[];
  completed_agent_ids?: string[];
  failed_agent_ids?: string[];
  waiting_agent_ids?: string[];
  pending_agent_ids?: string[];
  inflight_agent_ids?: string[];
  coordination_gaps?: string[];
  source_reviews?: Record<string, unknown>[];
  logic_chain?: Record<string, unknown>[];
  compliance_checks?: Record<string, unknown>[];
  final_product?: Record<string, unknown>;
  final_deliverable?: Record<string, unknown>;
  final_answer?: string;
  created_at?: string;
}

export interface ConversationRuntimeDecision {
  round?: number;
  decision?: string;
  target?: string;
  task?: string;
  rationale?: string;
  target_agent_ids?: string[];
  expected_outputs?: string[];
  requires_review?: boolean;
  fallback_reason?: string;
  summary?: ConversationRuntimeSummary;
  created_at?: string;
}

export interface ConversationRuntimeGeneration {
  id: string;
  session_id?: string;
  status?: string;
  started_at?: string;
  completed_at?: string | null;
  cancelled_at?: string | null;
  error?: string | null;
  event_counts?: Record<string, number>;
  task_plan?: ConversationRuntimeTaskPlanItem[];
  summary?: ConversationRuntimeSummary;
  summaries?: ConversationRuntimeSummary[];
  decisions?: ConversationRuntimeDecision[];
  agent_runs?: ConversationRuntimeAgentRun[];
}

export interface ConversationRuntime {
  active_generation_id?: string | null;
  generations?: ConversationRuntimeGeneration[];
}

export interface MessageAttachment {
  id?: string;
  file_id?: string;
  filename?: string;
  original_filename?: string;
  content_type?: string;
  size?: number;
  parse_status?: string;
  extracted_text?: string;
  metadata?: Record<string, unknown>;
  public_url?: string;
  download_url?: string;
  url?: string;
}

export interface ToolEventRecord {
  toolName: string;
  toolCallId?: string;
  run_id?: string;
  provider?: string;
  changed_files_count?: number | string;
  status?: string;
  exit_code?: number | string;
  duration_ms?: number | string;
  stdout?: string;
  stderr?: string;
  summary?: string;
  error?: string;
  session_id?: string;
  session_status?: string;
  command?: string;
  cwd?: string;
}

export interface CodeRunRecord {
  status?: string;
  language?: string;
  command?: string;
  stdout?: string;
  stderr?: string;
  exit_code?: number | string;
  duration_ms?: number | string;
  filename?: string;
  invocation_id?: string;
}

export interface ChatMessage {
  id: string; // 消息 ID，目前使用agent_id
  conversationId: string; // 对话 ID，不清楚来源
  sender_id?: string; // 不清楚作用
  sender_type?: string; // 不清楚作用
  sender_avatar_url?: string;
  role: MessageRole; // 不清楚作用
  kind: MessageKind; // 不清楚作用
  author: string; // 不清楚作用
  content: string; // 消息内容
  thinking?: string; // LLM思考内容
  rawContent?: Record<string, unknown>; // 不清楚作用
  clientMessageId?: string;
  client_message_id?: string;
  attachments?: MessageAttachment[]; // 消息附件
  toolEvents?: ToolEventRecord[]; // 工具执行事件
  createdAt: string; // 不清楚作用
  streamState?: StreamState; // 流式状态，根据其情况确定组件是否需要更新
  quotedMessageId?: string; // 引用的消息 ID，不清楚来源
  status?: string; // 不清楚作用
  state: "active" | "inactive"; // 旧的流式状态，之后会移除
}

export interface WorkspaceArtifact {
  id: string;
  conversationId: string;
  title: string;
  language: string;
  code: string;
  previousCode: string;
  previewUrl?: string;
  updatedAt: string;
  /** 扩展字段，用于支持多种格式的 artifact */
  content?: {
    format?: string;
    media_type?: string;
    filename?: string;
    preview_html?: string;
    files?: Record<string, string>;
    tool_output?: {
      format?: string;
      media_type?: string;
      filename?: string;
    };
  };
  format?: string;
  type?: "document" | "spreadsheet" | "slides" | string;
  media_type?: string;
  filename?: string;
}

export interface Deployment {
  id: string;
  artifact_id?: string;
  artifactId?: string;
  conversation_id?: string;
  conversationId?: string;
  status:
    | "idle"
    | "building"
    | "ready"
    | "failed"
    | "deployed"
    | "deploying"
    | "pending";
  url?: string;
  access_url?: string;
  mode?: string;
  commit?: string;
  health_status?: string;
  updatedAt?: string;
  updated_at?: string;
}
