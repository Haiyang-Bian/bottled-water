from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError


class _RuntimeModeRemovedMixin:
    @model_validator(mode="before")
    @classmethod
    def reject_runtime_mode(cls, value):
        if isinstance(value, dict) and "runtime_mode" in value:
            raise PydanticCustomError(
                "runtime_mode_removed",
                "runtime_mode was removed; use scheduling_strategy and workflow_enabled",
            )
        return value


class LoginRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    name: str | None = None
    password: str


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    display_name: str | None = None


class CreateConversationRequest(_RuntimeModeRemovedMixin, BaseModel):
    chat_type: Literal["single", "group"] = "single"
    type: Literal["single", "group"] | None = None
    title: str | None = None
    description: str | None = None
    participant_agent_ids: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    group: bool = False
    master_enabled: bool | None = None
    category: str | None = None
    folder: str | None = None
    remark: str | None = None
    scheduling_strategy: Literal["workflow", "tech_lead", "single_agent", "collaborative"] | None = None
    workflow_enabled: bool | None = None
    summary_agent_id: str | None = None
    live_user_input: bool = True
    max_collaboration_messages: int = Field(default=64, ge=1, le=512)
    max_agent_turns: int = Field(default=12, ge=1, le=100)
    max_open_threads: int = Field(default=24, ge=1, le=128)
    max_team_message_chars: int = Field(default=8_000, ge=100, le=32_000)


class UpdateConversationRequest(_RuntimeModeRemovedMixin, BaseModel):
    action: Literal["pin", "unpin", "archive", "unarchive", "rename", "runtime"] | None = None
    title: str | None = None
    description: str | None = None
    remark: str | None = None
    category: str | None = None
    folder: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    scheduling_strategy: Literal["workflow", "tech_lead", "single_agent", "collaborative"] | None = None
    workflow_enabled: bool | None = None
    summary_agent_id: str | None = None
    live_user_input: bool | None = None
    max_collaboration_messages: int | None = Field(default=None, ge=1, le=512)
    max_agent_turns: int | None = Field(default=None, ge=1, le=100)
    max_open_threads: int | None = Field(default=None, ge=1, le=128)
    max_team_message_chars: int | None = Field(default=None, ge=100, le=32_000)


class SendMessageRequest(BaseModel):
    client_message_id: str
    content_type: str = "text"
    content: dict[str, Any]
    reply_to_message_id: str | None = None


class CreateAgentRequest(BaseModel):
    name: str
    type: str = "custom"
    description: str = ""
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_color: str | None = None
    provider: str = "custom"
    version: str = "1.0"
    capabilities: list[Any] = Field(default_factory=list)
    system_prompt: str = ""
    base_agent_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    status: str | None = None
    avatar_url: str | None = None
    avatar_color: str | None = None
    capabilities: list[Any] | None = None
    system_prompt: str | None = None
    config: dict[str, Any] | None = None
    tools: list[str] | None = None


class ParseCapabilitiesRequest(BaseModel):
    text: str


class GenerateAgentRequest(BaseModel):
    brief: str
    base_agent_id: str | None = None
    preferred_tools: list[str] = Field(default_factory=list)


class TestAgentRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class AddParticipantRequest(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    role: str = "member"


class InviteParticipantRequest(BaseModel):
    invitee_email: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
    role: str = "member"


class ArtifactEditRequest(BaseModel):
    files: dict[str, str]
    change_summary: str = "用户在线编辑"


class DiffRequest(BaseModel):
    old_version: int | None = None
    new_version: int | None = None


class CreateDeploymentRequest(BaseModel):
    artifact_id: str
    mode: str = "preview_link"
    config: dict[str, Any] = Field(default_factory=dict)


class CreateKnowledgeBaseRequest(BaseModel):
    name: str
    description: str = ""
    scope: str = "personal"
    visibility: str = "private"
    config: dict[str, Any] = Field(default_factory=dict)


class ImportKnowledgeTextRequest(BaseModel):
    title: str
    content: str
    source_type: str = "manual"
    source_uri: str = ""


class RetrieveKnowledgeRequest(BaseModel):
    query: str
    top_k: int = 5
    mode: str = "hybrid"
    similarity_threshold: float = 0.0


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: str = ""
    type: str = "custom"
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    workflow: dict[str, Any] = Field(default_factory=dict)


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    resource_bindings: dict[str, Any] | None = None


class AddWorkspaceMemberRequest(BaseModel):
    user_id: str
    role: str = "member"
    permissions: list[str] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    type: str = "code_project"
    tags: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class UpsertProjectFileRequest(BaseModel):
    path: str
    content: str
    language: str = "text"


class CreatePromptTemplateRequest(BaseModel):
    name: str
    description: str = ""
    scope: str = "workspace"
    category: str = "general"
    content: str
    variables: list[str] = Field(default_factory=list)


class CreateShortcutCommandRequest(BaseModel):
    name: str
    description: str = ""
    prompt_template: str
    agent_route: dict[str, Any] = Field(default_factory=dict)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class CreateSkillRequest(BaseModel):
    workspace_id: str | None = None
    name: str
    description: str = ""
    category: str = "general"
    source: Literal["manual", "mcp", "ai"] = "manual"
    status: str = "active"
    version: str = "1.0.0"
    content: str = ""
    prompt: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateSkillRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None
    description: str | None = None
    category: str | None = None
    status: str | None = None
    version: str | None = None
    content: str | None = None
    prompt: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None


class ImportMcpSkillRequest(BaseModel):
    mcp_server_id: str
    workspace_id: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    name: str | None = None
    description: str | None = None
    category: str = "mcp"
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class GenerateSkillRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None
    intent: str
    requirements: str = ""
    category: str = "ai"
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class TestSkillRequest(BaseModel):
    input: Any = ""
    message: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class CreateToolRequest(BaseModel):
    workspace_id: str | None = None
    name: str
    display_name: str | None = None
    description: str = ""
    category: str = "custom"
    type: str = "custom_python"
    status: str = "active"
    version: str = "1.0.0"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    implementation: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateToolRequest(BaseModel):
    workspace_id: str | None = None
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    status: str | None = None
    version: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    permissions: list[str] | None = None
    implementation: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None


class GenerateToolRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None
    intent: str
    requirements: str = ""
    category: str = "custom"
    allowed_permissions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class InvokeToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None
    workspace_id: str | None = None


class CreateModelProviderRequest(BaseModel):
    name: str
    provider_type: str = "openai_compatible"
    base_url: str
    api_key: str | None = None
    default_model: str
    supports_streaming: bool = True
    supports_embeddings: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateModelProviderCredentialRequest(BaseModel):
    api_key: str = Field(min_length=1)


class CreateModelConfigRequest(BaseModel):
    provider_id: str | None = None
    provider_type: str | None = None
    name: str
    model_id: str
    purpose: str = "chat"
    context_window: int = 128000
    max_output_tokens: int = 8192
    temperature_default: float = 0.7
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_provider(self):
        if not self.provider_id and not self.provider_type:
            raise ValueError("provider_id 与 provider_type 至少提供一个")
        return self


class UpdateModelConfigRequest(BaseModel):
    name: str | None = None
    model_id: str | None = None
    purpose: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    temperature_default: float | None = None
    config: dict[str, Any] | None = None


class TestModelRequest(BaseModel):
    prompt: str = "你好，请用一句话介绍你自己。"
    model_config_id: str | None = None


class CreateMcpServerRequest(BaseModel):
    workspace_id: str | None = None
    name: str
    transport: str = "httpStream"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tool_filter: list[str] = Field(default_factory=list)
    timeout_ms: int = 30000
    retry: int = 1


class ImportMcpServerRequest(BaseModel):
    workspace_id: str | None = None
    source_type: Literal["manifest_url", "json"] = "json"
    source: str


class InvokeMcpToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None
    timeout_ms: int | None = None


class CreateSandboxRequest(BaseModel):
    workspace_id: str | None = None
    project_id: str | None = None
    name: str
    image: str = "python:3.11-slim"
    resource_limits: dict[str, Any] = Field(default_factory=dict)


class RunSandboxCommandRequest(BaseModel):
    command: str
    timeout_seconds: int = 30
    workdir: str = ""
    conversation_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class TerminalStartRequest(BaseModel):
    command: str
    sandbox_id: str | None = None
    workspace_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    workdir: str = ""
    timeout_seconds: int = 300
    env: dict[str, str] = Field(default_factory=dict)


class TerminalSendRequest(BaseModel):
    input: str


class TerminalWaitRequest(BaseModel):
    patterns: list[str]
    timeout_ms: int = 5000


class RunMessageCodeRequest(BaseModel):
    language: str = "python"
    code: str
    index: int = 0
    timeout_seconds: int = 10
    workspace_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None


class CreateRemoteConnectionRequest(BaseModel):
    workspace_id: str | None = None
    name: str
    connection_type: str = "browser"
    endpoint: str = ""
    capabilities: list[str] = Field(default_factory=list)


# ===== auth =====


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    name: str | None = None
    avatar_url: str | None = None
    signature: str | None = None
    settings: dict[str, Any] | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str | None = None
    old_password: str | None = None
    new_password: str | None = None
    password: str | None = None


class WorkflowUpdatePayload(BaseModel):
    """更新会话工作流请求体。"""

    mode: str | None = None
    output_mode: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[list[str] | dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkflowGeneratePayload(BaseModel):
    """生成工作流请求体。"""

    instruction: str | None = None
    prompt: str | None = None


class WorkflowRunStartPayload(BaseModel):
    """启动工作流运行请求体。"""

    workflow: dict[str, Any] | None = None
    trigger_message_id: str | None = None
    mode: str | None = None


class WorkflowNodeUpdatePayload(BaseModel):
    """更新工作流节点状态请求体。"""

    status: str | None = None
    progress: int | None = None
    output: dict[str, Any] | None = None
    matched_branch: str | None = None
    current_iteration: int | None = None
    max_iterations: int | None = None
    message: str | None = None


class ParticipantRoleUpdatePayload(BaseModel):
    """更新参与者角色请求体。"""

    role: str


# ===== messages =====


class MessageAttachment(BaseModel):
    file_id: str | None = None
    id: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size: int = 0
    parse_status: str = "pending"
    extracted_text: str = ""


class SendMessagePayload(BaseModel):
    """发送消息请求体（同时用于 /messages 和 /stream）。

    Args:
        client_message_id (str | None, optional): 客户端消息ID。默认值为None。
        content_type (str, optional): 消息内容类型。默认值为"text"。
        content (dict[str, Any], optional): 消息内容。默认值为空字典。
        text (str | None, optional): 文本消息内容。默认值为None。
        prompt (str | None, optional): 提示消息内容。默认值为None。
        attachments (list[dict[str, Any]], optional): 消息附件。默认值为空列表。
        reply_to_message_id (str | None, optional): 回复的消息ID。默认值为None。
        quotedMessageId (str | None, optional): 引用的消息ID。默认值为None。
        thinking_enabled (bool, optional): 是否启用思考状态。默认值为False。
        regenerate_message_id (str | None, optional): 重新生成的消息ID。默认值为None。
    """

    client_message_id: str | None = None
    content_type: str = "text"
    content: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None
    prompt: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    quotedMessageId: str | None = None
    thinking_enabled: bool = False
    scheduling_strategy: Literal["workflow", "tech_lead", "single_agent", "collaborative"] | None = None
    regenerate_message_id: str | None = None
    model_config_id: str | None = None  # 用户选择的模型配置ID


# ===== tasks =====


class CreateTaskRequest(BaseModel):
    conversation_id: str | None = None
    prompt: str | None = None
    title: str | None = None
    description: str | None = None


# ===== files =====


class FileUploadMeta(BaseModel):
    conversation_id: str | None = None
    purpose: str = "attachment"


# ===== artifacts =====


class CreateArtifactRequest(BaseModel):
    conversation_id: str
    content: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    name: str | None = None


class ArtifactEditPayload(BaseModel):
    files: dict[str, str]
    change_summary: str = "用户在线编辑"


class SaveArtifactRequest(BaseModel):
    files: dict[str, str] | None = None
    code: str | None = None
    content: dict[str, Any] | None = None
    change_summary: str | None = None


class DiffPayload(BaseModel):
    old_version: int | None = None
    new_version: int | None = None


# ===== conversations workflow / participants =====
