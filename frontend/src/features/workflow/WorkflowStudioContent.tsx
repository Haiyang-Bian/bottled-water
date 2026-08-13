import { Form, Spin } from "antd";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { layoutWorkflowPositions } from "../../lib/workflowLayout";
import { conversationRoutePath } from "../../lib/workflowRoutes";
import { useConversationStore } from "../../store";
import type { ConversationWorkflow } from "../../types";
import {
  normalizeWorkflowAgentBindings,
  normalizeWorkflowForRun,
  workflowSettings,
} from "./utils";
import { validateWorkflowDefinition } from "./validation";
import { WorkflowCanvasPanel } from "./WorkflowCanvasPanel";
import {
  WorkflowFloatingPanels,
  type WorkflowFloatingPanelKey,
} from "./WorkflowFloatingPanels";
import { WorkflowNodeConfigPanel } from "./WorkflowNodeConfigPanel";
import { WorkflowNodePalette } from "./WorkflowNodePalette";
import { WorkflowStudioHeader } from "./WorkflowStudioHeader";
import { useWorkflowStudio } from "./useWorkflowStudio";

export function WorkflowStudioContent({
  workspaceId,
  conversationId,
  embedded = false,
  onBack,
  onError,
  onSuccess,
}: {
  workspaceId: string;
  conversationId: string;
  embedded?: boolean;
  onBack?: () => void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
}) {
  const [nodeForm] = Form.useForm();
  const [fitViewSignal, setFitViewSignal] = useState(0);
  const [activePanel, setActivePanel] = useState<WorkflowFloatingPanelKey>();
  const navigate = useNavigate();
  const updateConversation = useConversationStore((state) => state.updateConversation);
  const studio = useWorkflowStudio({
    workspaceId,
    conversationId,
    nodeForm,
    onError,
  });

  const backPath = conversationRoutePath(workspaceId, conversationId);
  const validationIssues = useMemo(
    () => validateWorkflowDefinition(studio.workflow),
    [studio.workflow],
  );
  const validationErrors = validationIssues.filter(
    (issue) => issue.severity === "error",
  );

  const workflowErrorText = (issues = validationErrors) =>
    `当前工作流配置不完整，请先修复画布：${issues
      .map((issue) => issue.message)
      .join("；")}`;

  const normalizeWorkflowDraft = (workflow: ConversationWorkflow) =>
    layoutWorkflowPositions(
      normalizeWorkflowAgentBindings(
        workflow,
        studio.conversation?.participants,
        studio.agents,
      ),
    );

  const persistWorkflow = async (workflow: ConversationWorkflow) => {
    const saved = await api.saveConversationWorkflow(
      conversationId,
      normalizeWorkflowDraft(workflow),
    );
    const enabled = Boolean(workflowSettings(saved).enabled);
    const patch = {
      scheduling_strategy: enabled ? "workflow" : "tech_lead",
      workflow_enabled: enabled,
    } as const;

    if (typeof api.updateConversation === "function") {
      const conversation = await api.updateConversation(conversationId, patch);
      updateConversation(conversationId, conversation);
    } else {
      updateConversation(conversationId, patch);
    }

    studio.setWorkflowDraft(saved);
    return saved;
  };

  const patchWorkflowSettings = async (patch: Record<string, unknown>) => {
    if (!studio.workflow) return;
    const { output_mode: outputMode, ...settingsPatch } = patch;
    const nextWorkflow = {
      ...studio.workflow,
      ...(typeof outputMode === "string" ? { output_mode: outputMode } : {}),
      settings: {
        ...workflowSettings(studio.workflow),
        ...settingsPatch,
      },
    };
    studio.setWorkflowDraft(nextWorkflow);
    if ("enabled" in settingsPatch) {
      try {
        await persistWorkflow(normalizeWorkflowDraft(nextWorkflow));
        onSuccess(
          settingsPatch.enabled
            ? "Workflow chat enabled"
            : "Workflow chat disabled",
        );
      } catch (error) {
        onError(error instanceof Error ? error.message : "Workflow setting save failed");
      }
    }
  };

  const saveWorkflow = async () => {
    if (!studio.workflow || studio.workflowGenerating) return;

    let parsed: ConversationWorkflow;
    if (studio.editingNode) {
      parsed = normalizeWorkflowDraft(
        (await studio.saveWorkflowNode()) ?? studio.workflow,
      );
    } else {
      try {
        parsed = normalizeWorkflowDraft(
          JSON.parse(studio.workflowJson) as ConversationWorkflow,
        );
      } catch {
        onError("工作流 JSON 格式不正确");
        return;
      }
    }

    await persistWorkflow(parsed);
    onSuccess("工作流已保存");
  };

  const generateWorkflow = async () => {
    if (!conversationId || studio.workflowGenerating) return;
    studio.setWorkflowGenerating(true);
    try {
      const generated = await api.generateConversationWorkflow(
        conversationId,
        studio.workflowInstruction,
      );
      const nextWorkflow = normalizeWorkflowDraft(generated);
      studio.setWorkflowDraft({
        ...nextWorkflow,
        settings: {
          ...workflowSettings(nextWorkflow),
          generation_instruction: studio.workflowInstruction,
        },
      });
      onSuccess("AI 已生成工作流");
    } catch (error) {
      onError(error instanceof Error ? error.message : "AI 生成工作流失败");
    } finally {
      studio.setWorkflowGenerating(false);
    }
  };

  const runWorkflow = async () => {
    if (!studio.workflow) return;

    const workflowToRun = normalizeWorkflowForRun(
      normalizeWorkflowDraft(
        studio.editingNode
          ? (await studio.saveWorkflowNode()) ?? studio.workflow
          : studio.workflow,
      ),
    );

    const nextValidationErrors = validateWorkflowDefinition(workflowToRun).filter(
      (issue) => issue.severity === "error",
    );
    if (nextValidationErrors.length) {
      onError(workflowErrorText(nextValidationErrors));
      setActivePanel("settings");
      return;
    }

    const run = await api.startWorkflowRun(conversationId, workflowToRun);
    studio.setWorkflowRuns((current) => [run, ...current]);
    studio.setWorkflowDraft(run.workflow_snapshot);
    onSuccess("工作流运行已创建");
  };

  const clearCanvasSelection = () => {
    studio.setSelectedNodeIds([]);
    studio.setSelectedEdgeIds([]);
    studio.setEditingNodeId(undefined);
    if (embedded) setActivePanel(undefined);
  };

  const openNodeEditor = (node: ConversationWorkflow["nodes"][number]) => {
    studio.openNodeEditor(node);
    if (embedded) setActivePanel("config");
  };

  const addWorkflowNode = (type: string, position?: { x: number; y: number }) => {
    studio.setNewNodeType(type);
    studio.addWorkflowNode(type, position);
    if (embedded) setActivePanel("config");
  };

  const copySelection = () => {
    studio.copySelection();
    if (embedded && studio.selectedNodeIds.length) setActivePanel("config");
  };

  const deleteSelection = async () => {
    await studio.deleteSelection();
    if (embedded) setActivePanel(undefined);
  };

  const handleDeleteSelection = async (removedNodeIds: string[]) => {
    const removedAgentIds = new Set<string>();
    for (const nodeId of removedNodeIds) {
      const node = studio.workflow?.nodes.find((item) => item.id === nodeId);
      if (node && ["agent", "review"].includes(node.type ?? "") && node.agent_id) {
        removedAgentIds.add(node.agent_id);
      }
    }

    if (removedAgentIds.size > 0 && studio.workflow) {
      const removedNodeIdsSet = new Set(removedNodeIds);
      const currentNodes = studio.workflow.nodes.filter(
        (item) => !removedNodeIdsSet.has(item.id),
      );
      await studio.syncAgentsAfterNodeRemoval(
        Array.from(removedAgentIds),
        currentNodes,
      );
    }
  };

  if (studio.loading) {
    return (
      <main
        className={embedded ? "workflow-embedded-loading" : "workflow-studio-loading"}
      >
        <Spin />
      </main>
    );
  }

  const canvasPanel = (
    <WorkflowCanvasPanel
      workflow={studio.workflow}
      latestRun={studio.latestRun}
      workflowGenerating={studio.workflowGenerating}
      selectedNodeIds={studio.selectedNodeIds}
      selectedEdgeIds={studio.selectedEdgeIds}
      editingNodeState={studio.editingNodeState}
      editingNodeLastState={studio.editingNodeLastState}
      editingNodeLastRun={studio.editingNodeLastRun}
      workflowRuns={studio.workflowRuns}
      validationIssues={validationIssues}
      fitViewSignal={fitViewSignal}
      showRuntimePanel={!embedded}
      onCopySelection={copySelection}
      onDropNodeType={addWorkflowNode}
      onWorkflowChange={studio.setWorkflowDraft}
      onOpenNode={openNodeEditor}
      onClearSelection={clearCanvasSelection}
      onSelectionChange={(nodeIds, edgeIds) => {
        studio.setSelectedNodeIds(nodeIds);
        studio.setSelectedEdgeIds(edgeIds);

        if (nodeIds.length === 1 && studio.workflow) {
          const selectedId = nodeIds[0];
          if (studio.editingNode?.id !== selectedId) {
            const node = studio.workflow.nodes.find((item) => item.id === selectedId);
            if (node) studio.openNodeEditor(node);
          }
        } else if (nodeIds.length > 1 || edgeIds.length) {
          studio.setEditingNodeId(undefined);
        }
      }}
      onDeleteSelection={handleDeleteSelection}
    />
  );

  const nodeConfigPanel = (
    <WorkflowNodeConfigPanel
      nodeForm={nodeForm}
      editingNode={studio.editingNode}
      editingNodeState={studio.editingNodeState}
      editingNodeLastState={studio.editingNodeLastState}
      editingNodeLastRun={studio.editingNodeLastRun}
      latestRun={studio.latestRun}
      workflowEdges={studio.workflowEdges}
      workflowJson={studio.workflowJson}
      agentOptions={studio.agentOptions}
      toolOptions={studio.toolOptions}
      skillOptions={studio.skillOptions}
      mcpServerOptions={studio.mcpServerOptions}
      mcpToolOptions={studio.mcpToolOptions}
      onSaveNode={studio.saveWorkflowNode}
      onWorkflowJsonChange={studio.setWorkflowJson}
    />
  );

  if (embedded) {
    return (
      <section className="workflow-studio-embedded">
        {canvasPanel}
        <WorkflowFloatingPanels
          activePanel={activePanel}
          workflow={studio.workflow}
          generating={studio.workflowGenerating}
          workflowInstruction={studio.workflowInstruction}
          selectedNodeIds={studio.selectedNodeIds}
          selectedEdgeIds={studio.selectedEdgeIds}
          editingNode={studio.editingNode}
          editingNodeState={studio.editingNodeState}
          editingNodeLastState={studio.editingNodeLastState}
          editingNodeLastRun={studio.editingNodeLastRun}
          latestRun={studio.latestRun}
          workflowRuns={studio.workflowRuns}
          workflowEdges={studio.workflowEdges}
          workflowJson={studio.workflowJson}
          validationIssues={validationIssues}
          nodeForm={nodeForm}
          agentOptions={studio.agentOptions}
          toolOptions={studio.toolOptions}
          skillOptions={studio.skillOptions}
          mcpServerOptions={studio.mcpServerOptions}
          mcpToolOptions={studio.mcpToolOptions}
          onActivePanelChange={setActivePanel}
          onBack={onBack ?? (() => navigate(backPath))}
          onFitView={() => setFitViewSignal((value) => value + 1)}
          onInstructionChange={studio.setWorkflowInstruction}
          onPatchSettings={patchWorkflowSettings}
          onSave={saveWorkflow}
          onGenerate={generateWorkflow}
          onRun={runWorkflow}
          onNodeTypeChange={studio.setNewNodeType}
          onAddNode={addWorkflowNode}
          onCopySelection={copySelection}
          onDeleteSelection={deleteSelection}
          onSaveNode={studio.saveWorkflowNode}
          onWorkflowJsonChange={studio.setWorkflowJson}
        />
      </section>
    );
  }

  return (
    <section className="workflow-studio-page">
      <WorkflowStudioHeader
        conversation={studio.conversation}
        workflow={studio.workflow}
        generating={studio.workflowGenerating}
        onBack={() => navigate(backPath)}
        onGenerate={generateWorkflow}
        onRun={runWorkflow}
        onSave={saveWorkflow}
        onPatchSettings={patchWorkflowSettings}
      />
      <div className="workflow-studio-main">
        <WorkflowNodePalette
          disabled={studio.workflowGenerating}
          onAdd={addWorkflowNode}
        />
        {canvasPanel}
        {nodeConfigPanel}
      </div>
    </section>
  );
}
