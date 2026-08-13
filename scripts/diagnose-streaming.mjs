#!/usr/bin/env node

const DEFAULT_PROMPT =
  "请用 8 句中文解释为什么天空是蓝色的，每句单独成段。不要使用列表。";

const args = parseArgs(process.argv.slice(2));
const baseUrl = String(args.base || "http://127.0.0.1:8000").replace(/\/+$/, "");
const apiBase = `${baseUrl}/api/v1`;
const prompt = String(args.prompt || args._[0] || DEFAULT_PROMPT);
const timeoutMs = Number(args.timeout || 120000);
const keepConversation = Boolean(args.keep || args.conversation);

if (typeof WebSocket === "undefined") {
  throw new Error("This script needs Node.js with global WebSocket support. Current Node should be >= 22.");
}

const startedAt = performance.now();
const rows = [];
let tokenCount = 0;
let tokenChars = 0;
let firstTokenAt;
let lastTokenAt;
let ackAt;
let finalEvent;
let tokenTextPreview = "";
let createdConversation = false;
let conversationId = args.conversation ? String(args.conversation) : "";

main().catch((error) => {
  console.error(`\n[diagnose-streaming] failed: ${error?.message || error}`);
  process.exitCode = 1;
});

async function main() {
  console.log(`[diagnose-streaming] base: ${baseUrl}`);
  console.log(`[diagnose-streaming] prompt: ${prompt}`);

  const token = await demoLogin();
  if (!conversationId) {
    const agent = await pickAgent(token);
    conversationId = await createConversation(token, agent);
    createdConversation = true;
    console.log(`[diagnose-streaming] temp conversation: ${conversationId}`);
    console.log(`[diagnose-streaming] agent: ${agent.name || agent.display_name || agent.id}`);
  } else {
    console.log(`[diagnose-streaming] conversation: ${conversationId}`);
  }

  try {
    await runWebSocketProbe(token, conversationId);
  } finally {
    if (createdConversation && !keepConversation) {
      await deleteConversation(token, conversationId).catch(() => undefined);
    }
  }

  printTimeline();
  printSummary();
}

async function demoLogin() {
  const data = await apiFetch("/auth/demo", {
    method: "POST",
    body: "{}",
  });
  const token = data.access_token || data.token;
  if (!token) throw new Error("demo login did not return an access token");
  return token;
}

async function pickAgent(token) {
  const data = await apiFetch("/agents?status=all&page_size=50", { token });
  const items = Array.isArray(data) ? data : data.items || [];
  if (!items.length) throw new Error("no active agent found");
  const usableItems = items.filter((item) => !["deleted", "disabled", "offline"].includes(String(item.status || "")));
  return (
    usableItems.find((item) => item.name === "Daily Chat Agent") ||
    usableItems.find((item) => item.display_name === "Daily Chat Agent") ||
    usableItems.find((item) => item.type === "chat") ||
    usableItems[0] ||
    items[0]
  );
}

async function createConversation(token, agent) {
  const data = await apiFetch("/conversations", {
    token,
    method: "POST",
    body: JSON.stringify({
      chat_type: "single",
      title: `Streaming Probe ${new Date().toISOString()}`,
      participant_agent_ids: [agent.id],
      scheduling_strategy: "single_agent",
      workflow_enabled: false,
    }),
  });
  if (!data.id) throw new Error("conversation create did not return id");
  return data.id;
}

async function deleteConversation(token, id) {
  await apiFetch(`/conversations/${encodeURIComponent(id)}`, {
    token,
    method: "DELETE",
  });
}

async function runWebSocketProbe(token, id) {
  const wsUrl = `${baseUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:")}/ws/conversations/${encodeURIComponent(id)}?token=${encodeURIComponent(token)}`;
  const ws = new WebSocket(wsUrl);
  const requestId = `probe-${Date.now()}`;

  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      try {
        ws.close();
      } catch {}
      reject(new Error(`timeout after ${timeoutMs}ms`));
    }, timeoutMs);

    ws.addEventListener("open", () => {
      mark("ws.open", {});
      ws.send(
        JSON.stringify({
          event: "chat.send",
          request_id: requestId,
          data: {
            client_message_id: `diagnose-${Date.now()}`,
            content_type: "text",
            content: { text: prompt },
            scheduling_strategy: "single_agent",
            thinking_enabled: false,
          },
        }),
      );
      mark("client.chat_send", {});
    });

    ws.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        mark("ws.unparsed", { preview: String(event.data).slice(0, 120) });
        return;
      }
      const eventName = String(message.event || "message");
      const data = message.data || {};
      mark(eventName, data);

      if (eventName === "chat.ack") {
        ackAt = elapsed();
      }
      if (eventName === "agent.token") {
        const token = String(data.token || "");
        tokenCount += 1;
        tokenChars += token.length;
        tokenTextPreview += token;
        firstTokenAt ??= elapsed();
        lastTokenAt = elapsed();
      }
      if (isTerminal(eventName)) {
        finalEvent = eventName;
        clearTimeout(timeout);
        ws.close();
        resolve();
      }
    });

    ws.addEventListener("error", () => {
      clearTimeout(timeout);
      reject(new Error("websocket error"));
    });

    ws.addEventListener("close", () => {
      if (finalEvent) return;
      clearTimeout(timeout);
      reject(new Error("websocket closed before terminal event"));
    });
  });
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    method: options.method || "GET",
    body: options.body,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${JSON.stringify(payload).slice(0, 300)}`);
  }
  if (payload && typeof payload === "object" && "data" in payload) return payload.data;
  return payload;
}

function mark(event, data) {
  const now = elapsed();
  rows.push({
    t: now,
    event,
    agent: data.agent_name || data.agent_id || data.sender_name || "",
    len: data.token ? String(data.token).length : data.content ? String(data.content).length : "",
    preview: eventPreview(event, data),
  });
}

function eventPreview(event, data) {
  if (event === "agent.token") return String(data.token || "").replace(/\s+/g, " ").slice(0, 80);
  if (event === "message:new" || event === "message:updated") {
    return String(data.content || data.kind || "").replace(/\s+/g, " ").slice(0, 80);
  }
  if (event === "agent.tool_call") return JSON.stringify(data.tools || data.tool || data.tool_name || "").slice(0, 80);
  if (event === "agent.tool_result") return JSON.stringify({ tool: data.tool || data.tool_name, success: data.success }).slice(0, 80);
  if (data.status || data.error) return JSON.stringify({ status: data.status, error: data.error }).slice(0, 120);
  return "";
}

function printTimeline() {
  console.log("\nTimeline:");
  console.log("ms\tgap\tevent\tagent\tlen\tpreview");
  let previous = 0;
  for (const row of rows) {
    const gap = row.t - previous;
    previous = row.t;
    console.log(
      `${row.t.toFixed(0)}\t${gap.toFixed(0)}\t${row.event}\t${row.agent}\t${row.len}\t${row.preview}`,
    );
  }
}

function printSummary() {
  const tokenRows = rows.filter((row) => row.event === "agent.token");
  const gaps = tokenRows.slice(1).map((row, index) => row.t - tokenRows[index].t);
  const tokenSpan = firstTokenAt === undefined || lastTokenAt === undefined ? 0 : lastTokenAt - firstTokenAt;
  const medianGap = gaps.length ? percentile(gaps, 0.5) : 0;
  const maxGap = gaps.length ? Math.max(...gaps) : 0;
  const firstTokenLatency = firstTokenAt ?? 0;

  console.log("\nSummary:");
  console.log(`ack latency: ${fmt(ackAt)} ms`);
  console.log(`first token latency: ${firstTokenAt === undefined ? "NO TOKEN" : `${fmt(firstTokenLatency)} ms`}`);
  console.log(`token count/chars: ${tokenCount}/${tokenChars}`);
  console.log(`token span: ${fmt(tokenSpan)} ms`);
  console.log(`median/max token gap: ${fmt(medianGap)} / ${fmt(maxGap)} ms`);
  console.log(`terminal event: ${finalEvent || "none"}`);
  if (tokenTextPreview) {
    console.log(`token text preview: ${tokenTextPreview.replace(/\s+/g, " ").slice(0, 180)}`);
  }

  console.log("\nDiagnosis:");
  if (!tokenCount) {
    console.log("- 没收到 agent.token。前端看起来非流式，不是 token 太快，而是这条链路没有把模型 token 流出来。");
    console.log("- 重点查后端 agent loop 是否只在最终 message:new/message:updated 时写入完整回答。");
    return;
  }
  if (firstTokenLatency > 2000 && tokenSpan < 300) {
    console.log("- 首 token 等待很久，随后 token 几乎瞬间到齐。更像后端/模型调用前段阻塞或被缓冲后成批释放。");
    console.log("- 重点查 provider.chat_stream 是否真的边收边 yield，以及 agent loop/tool loop 是否 collect 完再 emit。");
    return;
  }
  if (tokenSpan < 300 && medianGap < 30) {
    console.log("- token 是流出来的，但总跨度很短、间隔很小。前端视觉上会像一次性出现，主要是 token 太快/后端 chunk 太大。");
    console.log("- 如果想肉眼更明显，需要让后端按更小 chunk 或模型输出更长文本；不建议前端 fake typewriter。");
    return;
  }
  if (tokenSpan >= 1000 && tokenCount >= 3) {
    console.log("- 后端 token 是分散到达的，链路是真流式。若前端仍像非流式，重点查 React 渲染/合并逻辑或 markdown 渲染成本。");
    return;
  }
  console.log("- 有 token 事件，但样本不够典型。看 Timeline 里的 first token latency、token span 和 gap 判断。");
}

function isTerminal(event) {
  return [
    "system.session_completed",
    "system.session_cancelled",
    "system.session_error",
    "generation_finished",
    "generation:finished",
    "generation:cancelled",
    "generation:failed",
    "cancelled",
    "failed",
    "control.cancel",
    "control.watchdog_triggered",
  ].includes(event);
}

function elapsed() {
  return performance.now() - startedAt;
}

function fmt(value) {
  return Number.isFinite(value) ? value.toFixed(0) : "n/a";
}

function percentile(values, p) {
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * p)));
  return sorted[index] || 0;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      out._.push(arg);
      continue;
    }
    const key = arg.slice(2);
    if (key === "keep") {
      out.keep = true;
      continue;
    }
    out[key] = argv[i + 1];
    i += 1;
  }
  return out;
}
