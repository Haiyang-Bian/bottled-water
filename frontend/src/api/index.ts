export {
  API_BASE,
  ApiError,
  request,
  get,
  post,
  patch,
  del,
  requestWithTimeout,
  requestFile,
  wait,
  requestInterceptors,
  responseInterceptors,
  errorInterceptors,
} from "./client";

export {
  login,
  register,
  updateProfile,
  changePassword,
  me,
  logout,
} from "./auth";

export {
  conversations,
  createConversation,
  createConversationWithAgents,
  updateConversation,
  deleteConversation,
  addParticipants,
  removeParticipant,
  teamMessages,
  conversationRepository,
  bindConversationRepository,
  createAgentWorktree,
  releaseAgentWorktree,
  integrateAgentWorktree,
} from "./conversation";

export {
  messages,
  sendMessage,
  sendMessageWs,
  cancelAssistantReply,
  cancelAssistantReplyWs,
} from "./message";

export { tasks, createBackgroundTask, cancelTask } from "./task";

export {
  artifact,
  artifactById,
  saveArtifact,
  exportArtifact,
  previewArtifactPdf,
} from "./artifact";

export {
  conversationWorkflow,
  saveConversationWorkflow,
  generateConversationWorkflow,
  workflowRuns,
  startWorkflowRun,
  updateWorkflowNode,
} from "./workflow";

export { deploy, deploymentsForArtifact } from "./deploy";

export {
  agents,
  createAgent,
  updateAgent,
  deleteAgent,
  parseCapabilities,
  generateAgentConfig,
  generateAgent,
  testAgent,
} from "./agent";

export { uploadFile, files, previewFile } from "./file";

export {
  knowledgeBases,
  createKnowledgeBase,
  importKnowledgeText,
  retrieveKnowledge,
} from "./knowledge";

export {
  workspaces,
  createWorkspace,
  projects,
  createProject,
  saveProjectFile,
} from "./workspace";

export {
  workspaceFileTree,
  previewWorkspaceFile,
  downloadWorkspaceFile,
  downloadWorkspaceFilePreviewPdf,
  deleteWorkspaceFile,
  renameWorkspaceFile,
  createWorkspaceFolder,
  moveWorkspaceFiles,
  favoriteWorkspaceFile,
  bulkDeleteWorkspaceFiles,
} from "./workspaceFile";

export {
  modelProviders,
  createModelProvider,
  updateModelProvider,
  updateModelProviderCredential,
  deleteModelProvider,
  modelConfigs,
  createModelConfig,
  updateModelConfig,
  deleteModelConfig,
  testModel,
  availableModels,
  builtinProviders,
  activateModelConfig,
} from "./model";

export {
  mcpServers,
  createMcpServer,
  importMcpServer,
  probeMcpServer,
  invokeMcpTool,
  deleteMcpServer,
  mcpInvocations,
} from "./mcp";

export {
  skills,
  createSkill,
  importMcpAsSkill,
  generateSkill,
  testSkill,
  deleteSkill,
} from "./skill";

export {
  tools,
  createTool,
  generateTool,
  invokeTool,
  deleteTool,
} from "./tool";

export {
  sandboxes,
  createSandbox,
  runSandboxCommand,
  runMessageCodeBlock,
  startTerminal,
  sendTerminalInput,
  waitTerminalOutput,
  terminalSnapshot,
  stopTerminal,
} from "./sandbox";

export {
  externalAgentProbe,
  reprobeExternalAgent,
  externalAgentRuns,
} from "./externalAgent";

export {
  remoteConnections,
  createRemoteConnection,
  connectRemote,
} from "./remote";

export {
  auditLogs,
  auditStats,
  securityRoles,
  securityPermissions,
  securityUsers,
  updateSecurityUserRole,
} from "./security";

// 兼容旧版 api 对象用法
import * as auth from "./auth";
import * as conversation from "./conversation";
import * as message from "./message";
import * as task from "./task";
import * as artifact from "./artifact";
import * as workflow from "./workflow";
import * as deploy from "./deploy";
import * as agent from "./agent";
import * as file from "./file";
import * as knowledge from "./knowledge";
import * as workspace from "./workspace";
import * as model from "./model";
import * as mcp from "./mcp";
import * as skill from "./skill";
import * as tool from "./tool";
import * as sandbox from "./sandbox";
import * as externalAgent from "./externalAgent";
import * as remote from "./remote";
import * as security from "./security";
import * as workspaceFile from "./workspaceFile";

export const api = {
  ...auth,
  ...conversation,
  ...message,
  sendMessageWs: message.sendMessageWs,
  cancelAssistantReplyWs: message.cancelAssistantReplyWs,
  ...task,
  ...artifact,
  ...workflow,
  ...deploy,
  ...agent,
  ...file,
  ...knowledge,
  ...workspace,
  ...workspaceFile,
  ...model,
  builtinProviders: model.builtinProviders,
  activateModelConfig: model.activateModelConfig,
  ...mcp,
  ...skill,
  ...tool,
  ...sandbox,
  ...externalAgent,
  ...remote,
  ...security,
};
