import { expect, test, type Page } from "@playwright/test";

async function registerTestUser(page: Page) {
  const suffix = `${Date.now()}${Math.random().toString(36).slice(2, 8)}`;
  const response = await page.request.post("/api/v1/auth/signup", {
    data: {
      email: `e2e-${suffix}@example.test`,
      username: `e2e${suffix}`,
      password: `E2e-${suffix}-Pass!42`,
      display_name: "E2E User",
    },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const token = body.data?.access_token ?? body.access_token;
  expect(token).toBeTruthy();
  await page.addInitScript((accessToken: string) => {
    window.localStorage.setItem("agenthub_token", accessToken);
  }, token);
}

test("registered user completes the AgentHub collaboration loop", async ({ page }) => {
  await registerTestUser(page);
  await page.goto(process.env.AGENTHUB_E2E_PATH ?? "/");
  await expect(page.getByTestId("new-group-chat")).toBeVisible();

  await page.getByTestId("new-group-chat").click();
  const createDialog = page.getByRole("dialog", { name: /新建多 Agent 群聊/ });
  await expect(createDialog).toBeVisible();
  await page.getByTestId("create-conversation-confirm").click();
  await expect(createDialog).not.toBeVisible();
  await expect(page.getByTestId("message-input")).toBeVisible();

  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByTestId("file-upload").click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "demo-requirements.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("AgentHub demo requires preview, reviewer and deployment output.")
  });
  await expect(page.getByTestId("composer-attachments")).toContainText("demo-requirements.txt");

  await page
    .getByTestId("message-input")
    .fill("请实现一个可答辩演示的多 Agent 协作任务：拆解、执行、审查、生成预览并部署。");
  await page.getByTestId("send-message").click();

  await expect(page.getByTestId("background-tasks")).toBeVisible();
  await expect(page.getByText("正在回答").first()).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("background-tasks").click();
  await expect(page.getByTestId("background-task-popover")).toBeVisible();
  await page.keyboard.press("Escape");

  await expect(page.getByTestId("message-attachments").first()).toContainText("demo-requirements.txt");
  await page.getByTestId("message-attachment-preview").first().click();
  await expect(page.getByTestId("attachment-preview-modal")).toBeVisible();
  await expect(page.getByTestId("attachment-preview-modal")).toContainText(/AgentHub|demo-requirements|text\/plain/);
  await page.getByTestId("attachment-preview-close").click();

  await expect(page.getByTestId("preview-card")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".message-card").filter({ hasText: "任务拆解" })).toHaveCount(0);
  await expect(page.getByTestId("artifact-preview-panel")).toHaveCount(0);
  await page.getByTestId("preview-card").click();
  await expect(page.getByTestId("artifact-preview-panel")).toBeVisible();
  await page.getByText("Close", { exact: true }).click();
  await expect(page.getByTestId("artifact-preview-panel")).toHaveCount(0);
  await page.getByTestId("preview-card").click();
  await expect(page.getByTestId("artifact-preview-panel")).toBeVisible();

  await page.getByTestId("artifact-tabs").locator(".ant-tabs-tab").nth(1).click();
  await page
    .getByTestId("artifact-code-editor")
    .fill("<main><h1>答辩演示已编辑</h1><p>预览、Diff、部署链路验证通过。</p></main>");
  await page.getByTestId("save-artifact").click();

  await page.getByTestId("artifact-tabs").locator(".ant-tabs-tab").nth(3).click();
  await page.getByTestId("deploy-artifact").click();

  await expect(page.getByTestId("deployment-card")).toContainText(/ready|deployed/, { timeout: 60_000 });
  await expect(page.getByTestId("deployment-card")).toContainText(/localhost|127\.0\.0\.1/);
});

test("settings split global models from per-group workflow and workspace control plane", async ({ page }) => {
  await registerTestUser(page);
  await page.goto(process.env.AGENTHUB_E2E_PATH ?? "/");

  await page.getByTestId("global-settings").click();
  await expect(page.getByText("全局设置")).toBeVisible();
  await page.getByRole("tab", { name: "模型 API" }).click();
  await expect(page.getByText("OpenAI 兼容供应商")).toBeVisible();
  await expect(page.getByText("模型配置与真实测试")).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByTestId("new-group-chat").click();
  await page.getByTestId("create-conversation-confirm").click();
  await page.getByTestId("conversation-settings").click();
  await expect(page.getByRole("dialog", { name: "群聊设置" })).toBeVisible();
  await page.getByRole("tab", { name: "工作流画布" }).click();
  await expect(page.getByText("AI 生成")).toBeVisible();
  await expect(page.getByText("保存画布")).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByTestId("workspace-panel").click();
  await expect(page.getByText("工作区与平台控制面")).toBeVisible();
  await expect(page.getByRole("tab", { name: "MCP" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "工具" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Skills" })).toBeVisible();

  await page.getByRole("tab", { name: "MCP" }).click();
  const mcpPanel = page.getByRole("tabpanel", { name: "MCP" });
  await expect(mcpPanel.getByText("注册 MCP 服务")).toBeVisible();
  await expect(mcpPanel.getByText("服务与工具")).toBeVisible();
  await expect(page.getByTestId("mcp-import-card")).toBeVisible();

  await page.getByRole("tab", { name: "工具" }).click();
  const toolsPanel = page.getByRole("tabpanel", { name: "工具" });
  await expect(toolsPanel.getByText("自定义工具")).toBeVisible();
  await expect(toolsPanel.getByText("AI 构建工具", { exact: true })).toBeVisible();
  await expect(toolsPanel.getByText("artifact.create_pdf")).toBeVisible();

  await page.getByRole("tab", { name: "Skills" }).click();
  const skillsPanel = page.getByRole("tabpanel", { name: "Skills" });
  await expect(skillsPanel.getByText("Skills 管理")).toBeVisible();
  await expect(skillsPanel.getByText("从 MCP 导入 Skill")).toBeVisible();

  await page.getByRole("tab", { name: "沙箱/远程" }).click();
  const sandboxPanel = page.getByRole("tabpanel", { name: "沙箱/远程" });
  await expect(sandboxPanel.getByText("沙箱控制", { exact: true })).toBeVisible();
  await expect(sandboxPanel.getByText("远程连接", { exact: true })).toBeVisible();
});

test("agent marketplace supports AI generated config and editing custom agents", async ({ page }) => {
  await registerTestUser(page);
  await page.goto(process.env.AGENTHUB_E2E_PATH ?? "/");
  await page.getByTestId("agent-directory").click();

  const agentPrefix = `E2E${Date.now()}`;
  const agentName = `${agentPrefix} 前端 React 测试部署审查专家`;
  await page.getByTestId("create-agent").click();
  await page.getByTestId("agent-capability-text").fill(agentName);
  await page.getByTestId("ai-generate-agent").click();
  await expect(page.getByTestId("agent-system-prompt")).toHaveValue(/React|前端|测试/);
  await page.getByTestId("agent-publish").click();
  await expect(page.getByTestId("agent-publish")).not.toBeVisible();

  await page.locator('[data-testid^="edit-agent-"]:not([disabled])').first().click();
  await expect(page.getByText(/编辑 Agent/)).toBeVisible();

  const token = await page.evaluate(() => window.localStorage.getItem("agenthub_token"));
  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  const listResponse = await page.request.get(`/api/v1/agents?search=${encodeURIComponent(agentPrefix)}`, { headers });
  const listBody = await listResponse.json();
  const created = (listBody.data?.items ?? listBody.items ?? []).find((item: { name?: string; description?: string }) =>
    `${item.name ?? ""} ${item.description ?? ""}`.includes(agentPrefix)
  );
  if (created?.id) {
    await page.request.delete(`/api/v1/agents/${created.id}`, { headers });
  }
});
