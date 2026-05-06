import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const PLUGIN_ID = "openclaw-feishu-memory-plugin";
const MEMORY_TYPES = new Set(["decision", "fact", "pitfall"]);
const MEMORY_STATUSES = new Set(["pending", "active", "deprecated"]);
const execFileAsync = promisify(execFile);
const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(PLUGIN_DIR, "..", "..", "..");

function nowIso() {
  return new Date().toISOString();
}

function safeString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function asNumber(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function resolvePath(api, dataPath) {
  if (typeof api.resolvePath === "function") {
    try {
      return api.resolvePath(dataPath);
    } catch {
      return path.resolve(process.cwd(), dataPath);
    }
  }
  return path.resolve(process.cwd(), dataPath);
}

function normalizeProject(raw, index) {
  const projectId = safeString(raw?.projectId) || safeString(raw?.project_id) || `project-${index + 1}`;
  return {
    projectId,
    projectName: safeString(raw?.projectName) || safeString(raw?.project_name) || projectId,
    chatId: safeString(raw?.chatId) || safeString(raw?.chat_id),
    ownerUserId: safeString(raw?.ownerUserId) || safeString(raw?.owner_user_id),
    bitableAppToken: safeString(raw?.bitableAppToken) || safeString(raw?.bitable_app_token),
    bitableTableId: safeString(raw?.bitableTableId) || safeString(raw?.bitable_table_id),
  };
}

function parseConfig(api) {
  const raw = api.pluginConfig && typeof api.pluginConfig === "object" ? api.pluginConfig : {};
  const projects = Array.isArray(raw.projects)
    ? raw.projects.map(normalizeProject)
    : [];
  return {
    enabled: raw.enabled !== false,
    dataPath: safeString(raw.dataPath) || "./data/feishu-memory",
    autoRecall: raw.autoRecall !== false,
    autoCaptureLog: raw.autoCaptureLog !== false,
    maxRecallItems: asNumber(raw.maxRecallItems, 3),
    minRecallScore: asNumber(raw.minRecallScore, 0.18),
    marsEnginePath: safeString(raw.marsEnginePath) || safeString(process.env.MARS_ENGINE_PATH) || path.join(REPO_ROOT, "mars-memory-engine"),
    marsDbPath: safeString(raw.marsDbPath) || safeString(process.env.MARS_DB_PATH),
    pythonCommand: safeString(raw.pythonCommand) || safeString(process.env.MARS_PYTHON_COMMAND) || "py",
    projects,
  };
}

function readOpenClawConfig() {
  const configPath = safeString(process.env.OPENCLAW_CONFIG_PATH);
  if (!configPath || !existsSync(configPath)) return {};
  try {
    return JSON.parse(readFileSync(configPath, "utf8"));
  } catch {
    return {};
  }
}

function getDefaultFeishuAccountConfig() {
  const fullConfig = readOpenClawConfig();
  const accounts = fullConfig?.channels?.feishu?.accounts;
  if (!accounts || typeof accounts !== "object") return {};
  const first = Object.values(accounts).find((item) => item && typeof item === "object");
  return first || {};
}

function getFeishuAppCredentials(params = {}) {
  const account = getDefaultFeishuAccountConfig();
  const appId = safeString(params.appId) || safeString(process.env.FEISHU_APP_ID) || safeString(account.appId);
  const appSecret = safeString(params.appSecret) || safeString(process.env.FEISHU_APP_SECRET);
  return { appId, appSecret };
}

async function getFeishuTenantAccessToken(params = {}) {
  const { appId, appSecret } = getFeishuAppCredentials(params);
  if (!appId || !appSecret) {
    throw new Error("Feishu app credentials are not configured.");
  }
  const response = await fetch("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", {
    method: "POST",
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
  });
  const data = await response.json();
  if (!response.ok || data.code !== 0 || !data.tenant_access_token) {
    throw new Error(`Failed to get Feishu tenant access token: code=${data.code ?? response.status}`);
  }
  return data.tenant_access_token;
}

async function feishuOpenApi(pathname, options = {}, authParams = {}) {
  const token = await getFeishuTenantAccessToken(authParams);
  const response = await fetch(`https://open.feishu.cn${pathname}`, {
    ...options,
    headers: {
      "content-type": "application/json; charset=utf-8",
      authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || (typeof data.code === "number" && data.code !== 0)) {
    throw new Error(`Feishu OpenAPI failed: ${pathname}, code=${data.code ?? response.status}`);
  }
  return data;
}

function parseJsonOutput(stdout) {
  const text = String(stdout || "").trim();
  if (!text) return { parsed: false, text: "" };
  try {
    return { parsed: true, data: JSON.parse(text), text };
  } catch {
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start >= 0 && end > start) {
      const slice = text.slice(start, end + 1);
      try {
        return { parsed: true, data: JSON.parse(slice), text };
      } catch {
        // fall through
      }
    }
  }
  return { parsed: false, text };
}

async function runMarsCommand(cfg, args) {
  const commandArgs = ["-m", "mars_memory_engine", ...args];
  if (cfg.marsDbPath) commandArgs.push("--db-path", cfg.marsDbPath);
  try {
    const { stdout, stderr } = await execFileAsync(cfg.pythonCommand, commandArgs, {
      cwd: cfg.marsEnginePath,
      windowsHide: true,
      timeout: 120000,
      maxBuffer: 10 * 1024 * 1024,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      encoding: "utf8",
    });
    const parsed = parseJsonOutput(stdout);
    return {
      ok: true,
      command: cfg.pythonCommand,
      args: commandArgs,
      cwd: cfg.marsEnginePath,
      stdout,
      stderr,
      ...parsed,
    };
  } catch (error) {
    return {
      ok: false,
      command: cfg.pythonCommand,
      args: commandArgs,
      cwd: cfg.marsEnginePath,
      error: error?.message || String(error),
      stdout: error?.stdout || "",
      stderr: error?.stderr || "",
    };
  }
}

function formatMarsResult(label, result) {
  if (!result.ok) {
    return formatToolResult(`${label} 失败：${result.error}`, {
      ok: false,
      result,
    });
  }
  if (result.parsed) {
    return formatToolResult(`${label} 完成。`, {
      ok: true,
      parsed: true,
      data: result.data,
      stderr: result.stderr,
    });
  }
  return formatToolResult(`${label} 完成，但未解析到 JSON。\n${result.text || result.stdout}`, {
    ok: true,
    parsed: false,
    text: result.text || result.stdout,
    stderr: result.stderr,
  });
}

function buildDecisionCard(searchData, projectId, query) {
  const memories = Array.isArray(searchData?.memories) ? searchData.memories : [];
  const decisions = [];
  const evidenceIds = [];
  const participants = new Set();
  for (const memory of memories) {
    const content = safeString(memory?.content);
    if (content) decisions.push(content);
    for (const evidence of Array.isArray(memory?.evidence) ? memory.evidence : []) {
      if (safeString(evidence?.event_id)) evidenceIds.push(evidence.event_id);
      const contentText = safeString(evidence?.content);
      const speakerMatch = contentText.match(/^([^:：]{2,40})[:：]/);
      if (speakerMatch) participants.add(speakerMatch[1]);
    }
  }
  const uniqueEvidence = Array.from(new Set(evidenceIds)).slice(0, 12);
  const uniqueDecisions = Array.from(new Set(decisions)).slice(0, 8);
  return {
    title: `决策卡：${query || projectId || "项目记忆"}`,
    summary: uniqueDecisions.length
      ? uniqueDecisions.slice(0, 3).join("；")
      : `未找到与「${query}」直接相关的 active memory。`,
    decisions: uniqueDecisions,
    action_items: [],
    open_questions: uniqueDecisions.length ? [] : ["需要更多群聊上下文或先执行 mars_memory_digest。"],
    participants: Array.from(participants),
    evidence_message_ids: uniqueEvidence,
    confidence: uniqueDecisions.length ? 0.72 : 0.2,
    project_id: projectId,
    query,
  };
}

function inferType(text) {
  const normalized = text.toLowerCase();
  if (/(决定|结论|采用|不采用|选择|方案|decision)/i.test(normalized)) return "decision";
  if (/(踩坑|问题|事故|失败|风险|pitfall|bug|故障)/i.test(normalized)) return "pitfall";
  return "fact";
}

function makeTitle(text, type) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  const prefix = type === "decision" ? "决策" : type === "pitfall" ? "踩坑" : "事实";
  const body = cleaned.length > 42 ? `${cleaned.slice(0, 42)}...` : cleaned;
  return `${prefix}：${body || "未命名记忆"}`;
}

function makeSummary(text) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  return cleaned.length > 120 ? `${cleaned.slice(0, 120)}...` : cleaned;
}

function tokenize(text) {
  const lower = String(text || "").toLowerCase();
  const latin = lower.match(/[a-z0-9_+-]{2,}/g) || [];
  const cjkChars = Array.from(lower.match(/[\u4e00-\u9fa5]/g) || []);
  const cjk = [];
  for (let i = 0; i < cjkChars.length - 1; i += 1) cjk.push(`${cjkChars[i]}${cjkChars[i + 1]}`);
  return Array.from(new Set([...latin, ...cjk]));
}

function tokenScore(query, text) {
  const q = tokenize(query);
  if (q.length === 0) return 0;
  const target = new Set(tokenize(text));
  let hit = 0;
  for (const token of q) {
    if (target.has(token)) hit += 1;
  }
  return hit / q.length;
}

function formatToolResult(text, details = {}) {
  return {
    content: [{ type: "text", text }],
    details,
  };
}

function normalizeTextList(value, limit = 12) {
  const raw = Array.isArray(value) ? value : value ? [value] : [];
  const out = [];
  for (const item of raw) {
    let text = "";
    if (item && typeof item === "object") {
      text = safeString(item.text) || safeString(item.content) || safeString(item.summary) || safeString(item.decision);
    } else {
      text = safeString(String(item || ""));
    }
    if (text && !out.includes(text)) out.push(text);
    if (out.length >= limit) break;
  }
  return out;
}

function truncateText(value, maxLength = 280) {
  const text = safeString(String(value || "")).replace(/\s+/g, " ");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 3))}...`;
}

function extractEvidenceSnippets(text, needles, limit = 8) {
  const source = safeString(text);
  if (!source) return [];
  const paragraphs = source
    .split(/\n{2,}|\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
  const queryTerms = normalizeTextList(needles, 12)
    .flatMap((item) => tokenize(item).filter((token) => token.length >= 2))
    .slice(0, 40);
  const scored = paragraphs.map((paragraph, index) => {
    const score = queryTerms.length > 0 ? tokenScore(queryTerms.join(" "), paragraph) : 0;
    return { paragraph, index, score };
  });
  scored.sort((a, b) => b.score - a.score || a.index - b.index);
  const picked = scored
    .filter((item) => item.score > 0)
    .slice(0, limit)
    .map((item) => ({
      evidence_id: `ctx_${item.index + 1}`,
      source_type: "context",
      source_id: "",
      source_url: "",
      quote: truncateText(item.paragraph, 360),
      reason: "Matched against extracted decisions/reasons/topics.",
    }));
  if (picked.length === 0 && paragraphs.length > 0) {
    return paragraphs.slice(0, Math.min(limit, 3)).map((paragraph, index) => ({
      evidence_id: `ctx_${index + 1}`,
      source_type: "context",
      source_id: "",
      source_url: "",
      quote: truncateText(paragraph, 360),
      reason: "Fallback source excerpt from provided context.",
    }));
  }
  return picked;
}

function buildEvidenceChain(card, params = {}) {
  const structured = params?.agentStructuredCard && typeof params.agentStructuredCard === "object"
    ? params.agentStructuredCard
    : {};
  const provided = Array.isArray(structured.evidence_items) ? structured.evidence_items : [];
  const normalizedProvided = provided.slice(0, 12).map((item, index) => ({
    evidence_id: safeString(item?.evidence_id) || safeString(item?.id) || `agent_${index + 1}`,
    source_type: safeString(item?.source_type) || safeString(params?.sourceType) || "feishu",
    source_id: safeString(item?.source_id) || safeString(params?.sourceId),
    source_url: safeString(item?.source_url) || safeString(params?.sourceUrl),
    quote: truncateText(item?.quote || item?.content || item?.text || item?.summary, 500),
    reason: truncateText(item?.reason || item?.supports || item?.claim, 240),
  })).filter((item) => item.quote || item.source_id || item.source_url);

  const needles = [
    ...(card?.decisions || []),
    ...(card?.reasons || []),
    ...(card?.conclusions || []),
    ...(card?.topic_links || []),
  ];
  const inferred = extractEvidenceSnippets(
    safeString(params?.contextText) || safeString(params?.agentSummary),
    needles,
    Math.max(0, 12 - normalizedProvided.length),
  );
  const messageIds = normalizeTextList(card?.evidence_message_ids, 12);
  return {
    source_id: safeString(params?.sourceId),
    source_url: safeString(params?.sourceUrl),
    source_title: safeString(params?.title),
    source_scope: safeString(card?.source_scope) || safeString(structured?.source_scope),
    evidence_message_ids: messageIds,
    evidence_items: [...normalizedProvided, ...inferred],
    coverage: {
      has_decisions: normalizeTextList(card?.decisions || card?.decision_items).length > 0,
      has_reasons: normalizeTextList(card?.reasons).length > 0,
      has_objections: normalizeTextList(card?.objections).length > 0,
      has_conclusions: normalizeTextList(card?.conclusions).length > 0,
      has_time_points: normalizeTextList(card?.time_points).length > 0,
      has_source_quotes: normalizedProvided.length + inferred.length > 0 || messageIds.length > 0,
    },
  };
}

function normalizeDecisionCard(card, params = {}) {
  const normalized = card && typeof card === "object" ? { ...card } : {};
  normalized.project_id = safeString(normalized.project_id) || safeString(params.projectId);
  normalized.title = safeString(normalized.title) || safeString(params.title) || safeString(params.commandText) || "Decision card";
  normalized.decisions = normalizeTextList(normalized.decisions, 12);
  normalized.reasons = normalizeTextList(normalized.reasons, 12);
  normalized.objections = normalizeTextList(normalized.objections, 12);
  normalized.conclusions = normalizeTextList(normalized.conclusions, 12);
  normalized.time_points = normalizeTextList(normalized.time_points, 12);
  normalized.topic_links = normalizeTextList(normalized.topic_links, 12);
  normalized.open_questions = normalizeTextList(normalized.open_questions, 8);
  normalized.evidence_chain = normalized.evidence_chain || buildEvidenceChain(normalized, params);
  return normalized;
}

function joinForBitable(value, fallback = "") {
  const list = normalizeTextList(value, 20);
  return list.length > 0 ? list.join("\n") : fallback;
}

function buildBitableGovernanceFields(card, options = {}) {
  const lifecycle = card?.lifecycle || {};
  const evidence = card?.evidence_chain || {};
  const sourceItems = Array.isArray(evidence.evidence_items) ? evidence.evidence_items : [];
  return {
    memory_id: safeString(options.memoryId) || safeString(card?.memory_id),
    project_id: safeString(card?.project_id) || safeString(options.projectId),
    title: safeString(card?.title),
    lifecycle_status: safeString(lifecycle.status) || "new",
    governance_status: safeString(options.governanceStatus) || "pending_review",
    recommended_action: safeString(lifecycle.recommended_action),
    target_memory_id: safeString(lifecycle.agent_decision?.target_memory_id),
    confidence: asNumber(card?.confidence, asNumber(lifecycle.agent_decision?.confidence, 0)),
    decisions: joinForBitable(card?.decisions),
    reasons: joinForBitable(card?.reasons),
    objections: joinForBitable(card?.objections),
    conclusions: joinForBitable(card?.conclusions),
    project_phase: safeString(card?.project_phase),
    time_points: joinForBitable(card?.time_points),
    topic_links: joinForBitable(card?.topic_links),
    source_scope: safeString(evidence.source_scope) || safeString(card?.source_scope),
    source_id: safeString(evidence.source_id),
    source_url: safeString(evidence.source_url),
    evidence_message_ids: joinForBitable(evidence.evidence_message_ids),
    evidence_quotes: sourceItems.map((item, index) => `${index + 1}. ${item.quote}${item.source_url ? ` (${item.source_url})` : ""}`).join("\n"),
    similar_decisions: (Array.isArray(lifecycle.similar_decisions) ? lifecycle.similar_decisions : [])
      .slice(0, 8)
      .map((item, index) => `${index + 1}. [${item.relation || "similar"}] ${item.decision?.title || item.memory_id || ""} (${Math.round(asNumber(item.confidence, 0) * 100)}%)`)
      .join("\n"),
    conflict_notes: joinForBitable((Array.isArray(lifecycle.conflicts) ? lifecycle.conflicts : []).map((item) => item.reason || item.decision?.title || item.memory_id)),
    created_at: nowIso(),
    updated_at: nowIso(),
  };
}

function makeDecisionKey(card, fields = {}) {
  const sourceId = safeString(fields.source_id) || safeString(card?.evidence_chain?.source_id);
  if (sourceId) return `source:${sourceId}`;
  const projectId = safeString(fields.project_id) || safeString(card?.project_id);
  const title = safeString(fields.title) || safeString(card?.title);
  const decisions = normalizeTextList(card?.decisions || fields.decisions, 3).join("|");
  return `decision:${projectId}:${title}:${decisions}`.toLowerCase().slice(0, 240);
}

function parseBitableUrl(value) {
  const text = safeString(value);
  if (!text) return {};
  try {
    const url = new URL(text);
    const appToken = safeString(url.pathname.match(/\/base\/([^/?#]+)/)?.[1]);
    const tableId = safeString(url.searchParams.get("table"));
    return { appToken, tableId };
  } catch {
    const appToken = safeString(text.match(/\/base\/([^/?#\s]+)/)?.[1]);
    const tableId = safeString(text.match(/[?&]table=([^&#\s]+)/)?.[1]);
    return { appToken, tableId };
  }
}

function resolveBitableConfig(cfg, store, params = {}, projectId = "") {
  const project = store?.findProject?.(projectId, "") || null;
  const parsedUrl = parseBitableUrl(params.bitableUrl);
  return {
    appToken: safeString(params.bitableAppToken) || safeString(parsedUrl.appToken) || safeString(project?.bitableAppToken),
    tableId: safeString(params.bitableTableId) || safeString(parsedUrl.tableId) || safeString(project?.bitableTableId),
  };
}

function buildBitableSchema() {
  return [
    { field_name: "memory_id", type: 1, description: "MARS/OpenClaw memory identifier." },
    { field_name: "project_id", type: 1, description: "Project namespace." },
    { field_name: "title", type: 1, description: "Decision card title." },
    { field_name: "lifecycle_status", type: 3, description: "new/update/conflict/duplicate." },
    { field_name: "governance_status", type: 3, description: "pending_review/active/deprecated/ignored/conflict_review." },
    { field_name: "recommended_action", type: 1, description: "Suggested next operation." },
    { field_name: "target_memory_id", type: 1, description: "Existing memory to update/review." },
    { field_name: "confidence", type: 2, description: "Confidence score." },
    { field_name: "decisions", type: 1, description: "Extracted decisions." },
    { field_name: "reasons", type: 1, description: "Reasons and rationale." },
    { field_name: "objections", type: 1, description: "Opposing views and risks." },
    { field_name: "conclusions", type: 1, description: "Final conclusions/outcomes." },
    { field_name: "project_phase", type: 1, description: "Project phase." },
    { field_name: "time_points", type: 1, description: "Dates, deadlines, phases." },
    { field_name: "topic_links", type: 1, description: "Related topics." },
    { field_name: "source_scope", type: 1, description: "Source coverage description." },
    { field_name: "source_id", type: 1, description: "Feishu document/message identifier." },
    { field_name: "source_url", type: 15, description: "Feishu source URL." },
    { field_name: "evidence_message_ids", type: 1, description: "Raw evidence message/event IDs." },
    { field_name: "evidence_quotes", type: 1, description: "Quoted evidence snippets." },
    { field_name: "similar_decisions", type: 1, description: "Similar historical decisions." },
    { field_name: "conflict_notes", type: 1, description: "Conflict or duplicate notes." },
    { field_name: "created_at", type: 5, description: "Created timestamp." },
    { field_name: "updated_at", type: 5, description: "Updated timestamp." },
  ];
}

function cardMarkdownList(title, items, emptyText = "None") {
  const list = normalizeTextList(items, 6);
  const body = list.length > 0 ? list.map((item, index) => `${index + 1}. ${item}`).join("\n") : emptyText;
  return `**${title}**\n${body}`;
}

function buildFeishuInteractiveDecisionCard(card, options = {}) {
  const lifecycle = card?.lifecycle || {};
  const template = {
    conflict: "red",
    duplicate: "yellow",
    update: "orange",
    new: "blue",
  }[lifecycle.status] || "blue";
  const evidence = card?.evidence_chain || {};
  const evidenceItems = Array.isArray(evidence.evidence_items) ? evidence.evidence_items : [];
  const div = (content) => ({ tag: "div", text: { tag: "lark_md", content } });
  const elements = [
    div(cardMarkdownList("Decision", card?.decisions, safeString(card?.summary) || "No decision extracted.")),
    div(cardMarkdownList("Reason", card?.reasons)),
    div(cardMarkdownList("Objection / Risk", card?.objections)),
    div(cardMarkdownList("Conclusion", card?.conclusions)),
    div(
      [
        `**Lifecycle**: ${lifecycle.status || "unknown"}`,
        `**Recommended action**: ${lifecycle.recommended_action || "none"}`,
        `**Confidence**: ${Math.round(asNumber(card?.confidence, 0) * 100)}%`,
        card?.project_phase ? `**Phase**: ${card.project_phase}` : "",
        normalizeTextList(card?.time_points).length ? `**Time**: ${normalizeTextList(card.time_points).join(", ")}` : "",
      ].filter(Boolean).join("\n"),
    ),
  ];
  if (evidenceItems.length > 0 || normalizeTextList(evidence.evidence_message_ids).length > 0) {
    elements.push(
      div(cardMarkdownList(
        "Evidence",
        evidenceItems.map((item) => item.quote || item.source_id || item.source_url).concat(evidence.evidence_message_ids || []),
      )),
    );
  }
  const operationId = safeString(options.operationId) || `decision:${safeString(card?.project_id)}:${Date.now()}`;
  elements.push({
    tag: "action",
    actions: [
      {
        tag: "button",
        text: { tag: "plain_text", content: "Confirm" },
        type: "primary",
        value: { action: "confirm_decision", operation_id: operationId, project_id: card?.project_id || "", lifecycle_status: lifecycle.status || "" },
      },
      {
        tag: "button",
        text: { tag: "plain_text", content: "Update Existing" },
        type: "default",
        value: { action: "update_decision", operation_id: operationId, target_memory_id: lifecycle.agent_decision?.target_memory_id || "" },
      },
      {
        tag: "button",
        text: { tag: "plain_text", content: "Ignore" },
        type: "default",
        value: { action: "ignore_decision", operation_id: operationId },
      },
    ],
  });
  return {
    config: { wide_screen_mode: true, update_multi: true },
    header: {
      title: { tag: "plain_text", content: truncateText(card?.title || "Decision Card", 80) },
      template,
    },
    elements,
  };
}

function normalizeFeishuHistoryMessage(message, index) {
  const sender = message?.sender && typeof message.sender === "object" ? message.sender : {};
  const senderName = safeString(sender.name) || safeString(sender.id) || safeString(message?.sender_name) || "unknown";
  const rawTime = message?.create_time || message?.timestamp || message?.time || "";
  let timestamp = safeString(rawTime);
  if (/^\d+$/.test(timestamp)) {
    const numeric = Number(timestamp);
    const millis = timestamp.length >= 13 ? numeric : numeric * 1000;
    timestamp = new Date(millis).toISOString();
  }
  return {
    message_id: safeString(message?.message_id) || safeString(message?.id) || `feishu_history_${index + 1}`,
    actor_id: senderName,
    content: safeString(message?.content) || safeString(message?.text) || safeString(message?.plain_text),
    timestamp,
    message_type: safeString(message?.msg_type) || safeString(message?.message_type) || "text",
    attachments: Array.isArray(message?.attachments) ? message.attachments : [],
    mentions: Array.isArray(message?.mentions) ? message.mentions : [],
  };
}

function secondsFromDate(value) {
  const text = safeString(value);
  if (!text) return "";
  const millis = Date.parse(text);
  if (Number.isNaN(millis)) return "";
  return String(Math.floor(millis / 1000));
}

function resolveMessageTimeRange(params = {}) {
  if (params?.relativeTime) {
    const now = Date.now();
    const match = safeString(params.relativeTime).match(/^last_(\d+)_(minutes|hours|days)$/i);
    if (match) {
      const amount = Number(match[1]);
      const unitMs = { minutes: 60000, hours: 3600000, days: 86400000 }[match[2].toLowerCase()];
      return {
        start: String(Math.floor((now - amount * unitMs) / 1000)),
        end: String(Math.floor(now / 1000)),
      };
    }
    if (safeString(params.relativeTime).toLowerCase() === "today") {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return {
        start: String(Math.floor(start.getTime() / 1000)),
        end: String(Math.floor(now / 1000)),
      };
    }
  }
  return {
    start: secondsFromDate(params?.startTime),
    end: secondsFromDate(params?.endTime),
  };
}

function parseFeishuMessageContent(content) {
  const text = safeString(content);
  if (!text) return "";
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed.text === "string") return parsed.text;
    if (typeof parsed.content === "string") return parsed.content;
    if (Array.isArray(parsed.content)) {
      return parsed.content.flat().map((item) => item?.text || "").filter(Boolean).join("");
    }
  } catch {
    // Return raw text below.
  }
  return text;
}

function normalizeFeishuOpenApiMessages(items) {
  return (Array.isArray(items) ? items : []).map((item, index) => ({
    message_id: safeString(item?.message_id) || `feishu_message_${index + 1}`,
    msg_type: safeString(item?.msg_type) || safeString(item?.message_type) || "text",
    content: parseFeishuMessageContent(item?.body?.content || item?.content),
    create_time: safeString(item?.create_time),
    update_time: safeString(item?.update_time),
    sender: {
      id: safeString(item?.sender?.id) || safeString(item?.sender?.sender_id?.open_id),
      name: safeString(item?.sender?.name),
      id_type: safeString(item?.sender?.id_type),
      sender_type: safeString(item?.sender?.sender_type),
    },
    thread_id: safeString(item?.thread_id),
    parent_id: safeString(item?.parent_id),
    raw: item,
  }));
}

class JsonMemoryStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.data = {
      memories: [],
      evidences: [],
      projects: [],
      events: [],
      bitableRecords: [],
    };
    this.loaded = false;
    this.writeQueue = Promise.resolve();
  }

  async load(defaultProjects) {
    if (this.loaded) return;
    await mkdir(path.dirname(this.filePath), { recursive: true });
    if (existsSync(this.filePath)) {
      const text = await readFile(this.filePath, "utf8");
      const parsed = text.trim() ? JSON.parse(text) : {};
      this.data = {
        memories: Array.isArray(parsed.memories) ? parsed.memories : [],
        evidences: Array.isArray(parsed.evidences) ? parsed.evidences : [],
        projects: Array.isArray(parsed.projects) ? parsed.projects : [],
        events: Array.isArray(parsed.events) ? parsed.events : [],
        bitableRecords: Array.isArray(parsed.bitableRecords) ? parsed.bitableRecords : [],
      };
    }
    for (const project of defaultProjects) {
      if (!this.data.projects.some((p) => p.projectId === project.projectId)) {
        this.data.projects.push({ ...project, createdAt: nowIso(), updatedAt: nowIso() });
      }
    }
    this.loaded = true;
    await this.save();
  }

  async save() {
    this.writeQueue = this.writeQueue.then(async () => {
      await mkdir(path.dirname(this.filePath), { recursive: true });
      await writeFile(this.filePath, `${JSON.stringify(this.data, null, 2)}\n`, "utf8");
    });
    return this.writeQueue;
  }

  getDefaultProject() {
    return this.data.projects[0];
  }

  findProject(projectId, chatId) {
    if (projectId) return this.data.projects.find((p) => p.projectId === projectId);
    if (chatId) return this.data.projects.find((p) => p.chatId === chatId);
    return this.getDefaultProject();
  }

  async createMemory(input) {
    const ts = nowIso();
    const project = this.findProject(input.projectId, input.chatId);
    const projectId = project?.projectId || input.projectId || "default";
    const chatId = project?.chatId || input.chatId || "";
    const type = MEMORY_TYPES.has(input.type) ? input.type : inferType(input.text);
    const status = MEMORY_STATUSES.has(input.status) ? input.status : "pending";
    const memory = {
      memoryId: randomUUID(),
      title: input.title || makeTitle(input.text, type),
      type,
      summary: input.summary || makeSummary(input.text),
      content: input.text,
      status,
      projectId,
      chatId,
      ownerUserId: input.ownerUserId || project?.ownerUserId || "",
      confidence: typeof input.confidence === "number" ? input.confidence : 0.7,
      sourceType: input.sourceType || "message",
      sourceUrl: input.sourceUrl || "",
      bitableRecordId: "",
      createdBy: input.createdBy || input.requesterUserId || "openclaw",
      confirmedBy: status === "active" ? input.confirmedBy || input.requesterUserId || "" : "",
      reviewAt: input.reviewAt || "",
      expiresAt: input.expiresAt || "",
      createdAt: ts,
      updatedAt: ts,
    };
    const evidence = {
      evidenceId: randomUUID(),
      memoryId: memory.memoryId,
      sourceType: memory.sourceType,
      sourceId: input.sourceId || "",
      sourceUrl: memory.sourceUrl,
      sourceTitle: input.sourceTitle || "",
      sourceAuthor: input.sourceAuthor || input.requesterUserId || "",
      sourceTime: input.sourceTime || ts,
      summary: input.evidenceSummary || memory.summary,
      createdAt: ts,
    };
    this.data.memories.push(memory);
    this.data.evidences.push(evidence);
    this.logEvent("memory_created", { memoryId: memory.memoryId, projectId, status });
    await this.save();
    return { memory, evidence };
  }

  async confirmMemory(memoryId, userId, status = "active") {
    const memory = this.data.memories.find((item) => item.memoryId === memoryId);
    if (!memory) return null;
    memory.status = MEMORY_STATUSES.has(status) ? status : "active";
    memory.confirmedBy = userId || memory.confirmedBy || "";
    memory.updatedAt = nowIso();
    this.logEvent("memory_status_updated", { memoryId, status: memory.status });
    await this.save();
    return memory;
  }

  search(query, options = {}) {
    const limit = Math.max(1, Math.min(10, Math.floor(options.limit || 3)));
    const includePending = options.includePending === true;
    const projectId = options.projectId || "";
    const chatId = options.chatId || "";
    const candidates = this.data.memories.filter((memory) => {
      if (projectId && memory.projectId !== projectId) return false;
      if (chatId && memory.chatId && memory.chatId !== chatId) return false;
      if (memory.status === "deprecated") return false;
      if (!includePending && memory.status !== "active") return false;
      return true;
    });
    return candidates
      .map((memory) => {
        const haystack = `${memory.title}\n${memory.summary}\n${memory.content}`;
        const score = tokenScore(query, haystack);
        return { memory, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || String(b.memory.updatedAt).localeCompare(String(a.memory.updatedAt)))
      .slice(0, limit);
  }

  list(options = {}) {
    const projectId = options.projectId || "";
    const status = options.status || "";
    return this.data.memories.filter((memory) => {
      if (projectId && memory.projectId !== projectId) return false;
      if (status && memory.status !== status) return false;
      return true;
    });
  }

  getEvidence(memoryId) {
    return this.data.evidences.filter((item) => item.memoryId === memoryId);
  }

  logEvent(eventType, payload) {
    this.data.events.push({
      eventId: randomUUID(),
      eventType,
      payload,
      createdAt: nowIso(),
    });
    if (this.data.events.length > 1000) {
      this.data.events = this.data.events.slice(-1000);
    }
  }

  findBitableMapping({ projectId, decisionKey, appToken, tableId }) {
    return this.data.bitableRecords.find((item) => {
      if (projectId && item.projectId !== projectId) return false;
      if (decisionKey && item.decisionKey !== decisionKey) return false;
      if (appToken && item.appToken !== appToken) return false;
      if (tableId && item.tableId !== tableId) return false;
      return true;
    });
  }

  async upsertBitableMapping(input) {
    const ts = nowIso();
    const existing = this.findBitableMapping(input);
    if (existing) {
      Object.assign(existing, input, { updatedAt: ts });
      await this.save();
      return existing;
    }
    const record = {
      mappingId: randomUUID(),
      projectId: input.projectId || "",
      decisionKey: input.decisionKey || "",
      appToken: input.appToken || "",
      tableId: input.tableId || "",
      recordId: input.recordId || "",
      sourceId: input.sourceId || "",
      title: input.title || "",
      createdAt: ts,
      updatedAt: ts,
    };
    this.data.bitableRecords.push(record);
    await this.save();
    return record;
  }
}

function buildMemoryContext(results) {
  if (results.length === 0) return "";
  const lines = results.map(({ memory }, index) => {
    const source = memory.sourceUrl ? `\n   来源：${memory.sourceUrl}` : "";
    return `${index + 1}. [${memory.type}] ${memory.title}\n   摘要：${memory.summary}${source}\n   更新时间：${memory.updatedAt}`;
  });
  return `<enterprise_memory_context>\n以下是当前项目中已确认的相关记忆。仅在有帮助时使用，并在回答中保留来源。\n\n${lines.join("\n\n")}\n</enterprise_memory_context>`;
}

function buildDecisionWorkflowContext(prompt) {
  const text = safeString(prompt);
  if (!/(决策|文档|总结|记忆|更新|冲突|重复|卡片|decision|document|summary|memory|conflict|duplicate|update|card)/i.test(text)) {
    return "";
  }
  return [
    "<feishu_decision_memory_workflow>",
    "当用户要求从飞书文档或群聊生成决策卡、更新记忆、判断重复/冲突时，必须按以下流程执行：",
    "1. 先读取完整原文。长文档必须由 OpenClaw 自己做覆盖全文的结构化总结，覆盖所有周期、主题、决策、风险和行动项，不要只总结最近内容。",
    "2. 调用 feishu_memory_lifecycle_probe，传入 projectId、query、summaryText，获取 MARS 返回的相似决策、证据和 heuristic_status。",
    "3. OpenClaw 基于全文总结和 probe 证据自行判断最终 lifecycle：new / update / conflict / duplicate。",
    "4. 调用 feishu_memory_decision_command，传入 contextText、agentSummary、agentLifecycleDecision、autoCommit=false，生成最终预览卡片。",
    "5. agentSummary 不只是摘要，还要提取决策、理由、反对意见、结论、项目阶段、时间点、相关主题。可通过 agentStructuredCard 传给 feishu_memory_decision_command。",
    "6. 当当前讨论触及历史方案、截止日期、项目阶段或旧决策时，调用 feishu_memory_active_push_probe 判断是否主动推送历史决策卡。",
    "7. MARS 只负责摄入、候选抽取、相似检索、启发式建议、证据保留和卡片结构化；最终是否合并/更新/冲突/重复由 OpenClaw 判断。",
    "8. 不要把 mars_memory_doc_decision_card 作为面向用户的最终流程；它只是低级兼容工具。",
    "9. 当用户要求读取飞书群聊历史、最近消息或上下文时，先调用官方飞书工具 feishu_im_user_get_messages(chat_id, relative_time/page_size/sort_rule) 或 feishu_im_user_get_thread_messages(thread_id)。然后把返回的 messages 数组传给 mars_memory_ingest_feishu_messages，再进行 digest/search/decision card。",
    "10. 如果 feishu_im_user_get_messages 可用，不要声称无法读取群历史；如果需要授权，请让用户点击飞书授权卡。",
    "11. 如果官方 IM 历史工具没有出现在当前工具列表，调用 feishu_memory_get_chat_messages(chatId, relativeTime/pageSize) 作为兜底，再把 messages 传给 feishu_memory_chat_history_decision_command。",
    "</feishu_decision_memory_workflow>",
  ].join("\n");
}

function makePlugin() {
  let cfg;
  let store;

  return {
    id: PLUGIN_ID,
    name: "Feishu Enterprise Memory",
    description: "Companion memory plugin for Feishu project decisions, facts, and pitfalls.",
    configSchema: {
      parse(value) {
        return value && typeof value === "object" ? value : {};
      },
    },
    register(api) {
      cfg = parseConfig(api);
      const dataDir = resolvePath(api, cfg.dataPath);
      store = new JsonMemoryStore(path.join(dataDir, "memories.json"));
      const ready = store.load(cfg.projects);

      api.logger.info(`${PLUGIN_ID}: registered with ${cfg.projects.length} configured project(s)`);

      api.registerTool(
        {
          name: "feishu_memory_ping",
          label: "Feishu Memory Ping",
          description: "Connectivity test for the Feishu enterprise memory plugin.",
          parameters: {
            type: "object",
            properties: {
              text: { type: "string", description: "Optional text to echo." },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            return formatToolResult("feishu_memory_ping: pong", {
              ok: true,
              text: safeString(params?.text),
              projects: store.data.projects.length,
              time: nowIso(),
            });
          },
        },
        { name: "feishu_memory_ping" },
      );

      api.registerTool(
        {
          name: "feishu_memory_create",
          label: "Create Feishu Project Memory",
          description: "Create a candidate project memory. Use for explicit decisions, facts, and pitfalls worth remembering.",
          parameters: {
            type: "object",
            required: ["text"],
            properties: {
              text: { type: "string", description: "Memory content to store." },
              type: { type: "string", enum: ["decision", "fact", "pitfall"] },
              status: { type: "string", enum: ["pending", "active"] },
              projectId: { type: "string" },
              chatId: { type: "string" },
              requesterUserId: { type: "string" },
              sourceUrl: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const text = safeString(params?.text);
            if (!text) return formatToolResult("创建失败：text 不能为空。", { ok: false });
            const { memory } = await store.createMemory({
              text,
              type: safeString(params?.type),
              status: safeString(params?.status) || "pending",
              projectId: safeString(params?.projectId),
              chatId: safeString(params?.chatId),
              requesterUserId: safeString(params?.requesterUserId),
              sourceUrl: safeString(params?.sourceUrl),
            });
            return formatToolResult(
              `已创建候选记忆：${memory.title}\n类型：${memory.type}\n状态：${memory.status}\nMemory ID：${memory.memoryId}`,
              { ok: true, memory },
            );
          },
        },
        { name: "feishu_memory_create" },
      );

      api.registerTool(
        {
          name: "mars_memory_ingest_file",
          label: "MARS Ingest Chat File",
          description: "Import a local Feishu-style JSON chat file into the MARS memory engine.",
          parameters: {
            type: "object",
            required: ["filePath"],
            properties: {
              filePath: { type: "string", description: "Path to a local JSON chat file." },
            },
          },
          async execute(_toolCallId, params) {
            const filePath = safeString(params?.filePath);
            if (!filePath) return formatToolResult("导入失败：filePath 不能为空。", { ok: false });
            const result = await runMarsCommand(cfg, ["ingest", "--file", filePath, "--json"]);
            return formatMarsResult("MARS ingest", result);
          },
        },
        { name: "mars_memory_ingest_file" },
      );

      api.registerTool(
        {
          name: "mars_memory_ingest_feishu_messages",
          label: "MARS Ingest Feishu Message History",
          description: "Ingest messages returned by feishu_im_user_get_messages or feishu_im_user_get_thread_messages into MARS raw ledger while preserving message IDs as evidence IDs.",
          parameters: {
            type: "object",
            required: ["projectId", "messages"],
            properties: {
              projectId: { type: "string", description: "Project ID for the Feishu chat history." },
              chatId: { type: "string", description: "Optional Feishu chat_id (oc_xxx)." },
              messages: { type: "array", description: "Messages array returned by official Feishu history tools." },
              autoDigest: { type: "boolean", description: "Whether to run mars_memory_digest after ingest (default false)." },
              autoCommit: { type: "boolean", description: "Whether digest should auto-commit high-confidence candidates." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const messages = Array.isArray(params?.messages) ? params.messages : [];
            if (!projectId || messages.length === 0) {
              return formatToolResult("Feishu history ingest failed: projectId and non-empty messages are required.", { ok: false });
            }
            const normalized = messages
              .map((message, index) => normalizeFeishuHistoryMessage(message, index))
              .filter((message) => message.message_id && message.content);
            if (normalized.length === 0) {
              return formatToolResult("Feishu history ingest failed: no text messages could be normalized.", { ok: false });
            }
            const tempDir = path.join(REPO_ROOT, ".temp", "mars-feishu-history");
            await mkdir(tempDir, { recursive: true });
            const tempFilePath = path.join(tempDir, `feishu-history-${Date.now()}.json`);
            await writeFile(tempFilePath, JSON.stringify({
              project_id: projectId,
              chat_id: safeString(params?.chatId) || "feishu_chat",
              messages: normalized,
            }), "utf8");

            const ingestResult = await runMarsCommand(cfg, ["ingest", "--file", tempFilePath, "--json"]);
            try { await unlink(tempFilePath); } catch {}
            if (!ingestResult.ok || !ingestResult.parsed) {
              return formatMarsResult("MARS Feishu history ingest", ingestResult);
            }

            let digest = null;
            if (params?.autoDigest === true) {
              const digestArgs = ["digest", "--project-id", projectId, "--json"];
              if (params?.autoCommit === true) digestArgs.push("--auto-commit");
              const digestResult = await runMarsCommand(cfg, digestArgs);
              digest = digestResult.parsed ? digestResult.data : digestResult;
            }

            return formatToolResult(
              `Feishu history ingested into MARS: ${ingestResult.data?.imported_count || 0} imported, ${ingestResult.data?.skipped_count || 0} skipped.`,
              {
                ok: true,
                project_id: projectId,
                chat_id: safeString(params?.chatId),
                normalized_count: normalized.length,
                ingest: ingestResult.data,
                digest,
              },
            );
          },
        },
        { name: "mars_memory_ingest_feishu_messages" },
      );

      api.registerTool(
        {
          name: "feishu_memory_get_chat_messages",
          label: "Feishu Memory Get Chat Messages",
          description: "Fallback app-identity Feishu chat history reader for the memory workflow. Use when official feishu_im_user_get_messages is not visible to the agent. Then pass returned messages to feishu_memory_chat_history_decision_command.",
          parameters: {
            type: "object",
            required: ["chatId"],
            properties: {
              chatId: { type: "string", description: "Feishu chat_id / oc_xxx." },
              relativeTime: { type: "string", description: "today or last_{N}_minutes/hours/days, for example last_3_days." },
              startTime: { type: "string", description: "ISO datetime. Ignored when relativeTime is provided." },
              endTime: { type: "string", description: "ISO datetime. Ignored when relativeTime is provided." },
              sortRule: { type: "string", enum: ["create_time_asc", "create_time_desc"] },
              pageSize: { type: "number", description: "1-50, default 50." },
              pageToken: { type: "string" },
              appId: { type: "string", description: "Optional Feishu app_id override." },
            },
          },
          async execute(_toolCallId, params) {
            const chatId = safeString(params?.chatId);
            if (!chatId) {
              return formatToolResult("Feishu chat message read failed: chatId is required.", { ok: false });
            }
            const time = resolveMessageTimeRange(params || {});
            const query = new URLSearchParams({
              container_id_type: "chat",
              container_id: chatId,
              sort_type: safeString(params?.sortRule) === "create_time_asc" ? "ByCreateTimeAsc" : "ByCreateTimeDesc",
              page_size: String(Math.max(1, Math.min(50, Math.floor(asNumber(params?.pageSize, 50))))),
              card_msg_content_type: "raw_card_content",
            });
            if (time.start) query.set("start_time", time.start);
            if (time.end) query.set("end_time", time.end);
            if (params?.pageToken) query.set("page_token", safeString(params.pageToken));
            try {
              const data = await feishuOpenApi(
                `/open-apis/im/v1/messages?${query.toString()}`,
                { method: "GET" },
                { appId: safeString(params?.appId) },
              );
              const messages = normalizeFeishuOpenApiMessages(data?.data?.items || []);
              return formatToolResult(`Fetched ${messages.length} Feishu chat messages.`, {
                ok: true,
                chat_id: chatId,
                messages,
                has_more: data?.data?.has_more === true,
                page_token: safeString(data?.data?.page_token),
                next_step: "Pass messages to feishu_memory_chat_history_decision_command.",
              });
            } catch (error) {
              return formatToolResult(`Feishu chat message read failed: ${error?.message || String(error)}`, {
                ok: false,
                chat_id: chatId,
                permission_hint: "Requires Feishu app message-read permissions such as im:message:readonly or equivalent approved scopes.",
              });
            }
          },
        },
        { name: "feishu_memory_get_chat_messages" },
      );

      api.registerTool(
        {
          name: "feishu_memory_chat_history_decision_command",
          label: "Feishu Chat History Decision Command",
          description: "One-stop workflow after OpenClaw has fetched Feishu group/thread history: ingest messages into MARS, run digest, probe duplicate/update/conflict candidates, and generate a decision card with evidence chain, interactive card, and Bitable payload.",
          parameters: {
            type: "object",
            required: ["projectId", "commandText", "messages"],
            properties: {
              projectId: { type: "string" },
              chatId: { type: "string" },
              commandText: { type: "string" },
              messages: { type: "array", description: "Messages returned by feishu_im_user_get_messages or feishu_im_user_get_thread_messages." },
              agentSummary: { type: "string", description: "OpenClaw full-history summary. If omitted, a transcript fallback is used and marked review_required." },
              agentLifecycleDecision: { type: "object", description: "OpenClaw final lifecycle judgment. If omitted, MARS heuristic is used as a review-required fallback." },
              agentStructuredCard: { type: "object", description: "OpenClaw structured extraction: decisions, reasons, objections, conclusions, time points, topics, evidence." },
              sourceId: { type: "string" },
              sourceUrl: { type: "string" },
              title: { type: "string" },
              query: { type: "string" },
              autoCommit: { type: "boolean" },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const projectId = safeString(params?.projectId);
            const commandText = safeString(params?.commandText);
            const rawMessages = Array.isArray(params?.messages) ? params.messages : [];
            if (!projectId || !commandText || rawMessages.length === 0) {
              return formatToolResult("Chat history decision command failed: projectId, commandText, and messages are required.", { ok: false });
            }
            const normalized = rawMessages
              .map((message, index) => normalizeFeishuHistoryMessage(message, index))
              .filter((message) => message.message_id && message.content);
            if (normalized.length === 0) {
              return formatToolResult("Chat history decision command failed: no readable text messages were found.", { ok: false });
            }

            const transcript = normalized
              .map((message) => `[${message.timestamp || "unknown"}] ${message.actor_id}: ${message.content}`)
              .join("\n");
            const tempDir = path.join(REPO_ROOT, ".temp", "mars-feishu-history");
            await mkdir(tempDir, { recursive: true });
            const tempFilePath = path.join(tempDir, `feishu-chat-workflow-${Date.now()}.json`);
            await writeFile(tempFilePath, JSON.stringify({
              project_id: projectId,
              chat_id: safeString(params?.chatId) || "feishu_chat",
              messages: normalized,
            }), "utf8");
            const ingestResult = await runMarsCommand(cfg, ["ingest", "--file", tempFilePath, "--json"]);
            try { await unlink(tempFilePath); } catch {}
            if (!ingestResult.ok || !ingestResult.parsed) {
              return formatMarsResult("Feishu chat history ingest", ingestResult);
            }

            const digestArgs = ["digest", "--project-id", projectId, "--json"];
            if (params?.autoCommit === true) digestArgs.push("--auto-commit");
            const digestResult = await runMarsCommand(cfg, digestArgs);

            const query = safeString(params?.query) || commandText;
            const summaryText = safeString(params?.agentSummary) || transcript.slice(0, 6000);
            const similarResult = await runMarsCommand(cfg, [
              "similar-decisions",
              "--project-id",
              projectId,
              "--query",
              query,
              "--text",
              summaryText,
              "--top-k",
              "5",
              "--json",
            ]);
            const similar = similarResult.parsed && Array.isArray(similarResult.data?.similar_decisions)
              ? similarResult.data.similar_decisions
              : [];
            const heuristic = similar[0]?.relation || "new";
            const lifecycleDecision = params?.agentLifecycleDecision && typeof params.agentLifecycleDecision === "object"
              ? params.agentLifecycleDecision
              : {
                  status: heuristic,
                  reason: "MARS heuristic fallback was used because OpenClaw did not provide an explicit lifecycle judgment.",
                  target_memory_id: similar[0]?.memory_id || "",
                  recommended_action: heuristic === "duplicate" ? "review_duplicate" : heuristic === "conflict" ? "conflict_review" : heuristic === "update" ? "update_existing" : "create_new",
                  confidence: asNumber(similar[0]?.confidence, 0.4),
                  requires_review: true,
                };

            const commandTempDir = path.join(REPO_ROOT, ".temp", "mars-command");
            await mkdir(commandTempDir, { recursive: true });
            const commandArgs = ["command", "--project-id", projectId, "--command", commandText, "--json"];
            const contextFilePath = path.join(commandTempDir, `chat-context-${Date.now()}.txt`);
            await writeFile(contextFilePath, transcript, "utf8");
            commandArgs.push("--context-file", contextFilePath);
            const summaryFilePath = path.join(commandTempDir, `chat-summary-${Date.now()}.txt`);
            await writeFile(summaryFilePath, summaryText, "utf8");
            commandArgs.push("--agent-summary-file", summaryFilePath);
            const lifecycleFilePath = path.join(commandTempDir, `chat-lifecycle-${Date.now()}.json`);
            await writeFile(lifecycleFilePath, JSON.stringify(lifecycleDecision), "utf8");
            commandArgs.push("--agent-lifecycle-file", lifecycleFilePath);
            if (params?.agentStructuredCard && typeof params.agentStructuredCard === "object") {
              const structuredFilePath = path.join(commandTempDir, `chat-structured-card-${Date.now()}.json`);
              await writeFile(structuredFilePath, JSON.stringify(params.agentStructuredCard), "utf8");
              commandArgs.push("--agent-structured-card-file", structuredFilePath);
            }
            if (params?.title) commandArgs.push("--title", safeString(params.title));
            if (params?.sourceId) commandArgs.push("--source-id", safeString(params.sourceId));
            if (query) commandArgs.push("--query", query);
            if (params?.autoCommit === true) commandArgs.push("--auto-commit");

            const commandResult = await runMarsCommand(cfg, commandArgs);
            if (!commandResult.ok || !commandResult.parsed) {
              return formatMarsResult("Feishu chat decision command", commandResult);
            }
            const card = normalizeDecisionCard(commandResult.data?.decision_card || {}, {
              projectId,
              commandText,
              contextText: transcript,
              agentSummary: summaryText,
              agentStructuredCard: params?.agentStructuredCard,
              title: safeString(params?.title),
              sourceId: safeString(params?.sourceId) || safeString(params?.chatId),
              sourceUrl: safeString(params?.sourceUrl),
              sourceType: "feishu_chat",
            });

            return formatToolResult("Feishu chat history decision workflow completed.", {
              ok: true,
              normalized_count: normalized.length,
              ingest: ingestResult.data,
              digest: digestResult.parsed ? digestResult.data : digestResult,
              lifecycle_probe: {
                heuristic_status: heuristic,
                similar_decisions: similar,
              },
              lifecycle_decision: lifecycleDecision,
              decision_card: card,
              feishu_interactive_card: buildFeishuInteractiveDecisionCard(card),
              bitable_record_fields: buildBitableGovernanceFields(card, { projectId }),
              bitable_schema_fields: buildBitableSchema(),
              review_required: lifecycleDecision.requires_review === true || !params?.agentLifecycleDecision,
            });
          },
        },
        { name: "feishu_memory_chat_history_decision_command" },
      );

      api.registerTool(
        {
          name: "mars_memory_ingest_text",
          label: "MARS Ingest Text Document",
          description: "Ingest a text document into the MARS memory engine. The text will be split into chunks and stored as messages.",
          parameters: {
            type: "object",
            required: ["projectId", "text"],
            properties: {
              projectId: { type: "string", description: "Project ID for the document." },
              text: { type: "string", description: "Text content to ingest (use --file for long texts)." },
              title: { type: "string", description: "Optional document title for idempotency." },
              sourceId: { type: "string", description: "Optional source ID for idempotency." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const text = safeString(params?.text);
            if (!projectId || !text) return formatToolResult("摄入失败：projectId 和 text 不能为空。", { ok: false });

            // For long texts, write to temp file and use --file
            const useTempFile = text.length > 2000;
            let tempFilePath = null;
            const args = [
              "ingest-text",
              "--project-id",
              projectId,
              "--json",
            ];
            if (params?.title) args.push("--title", safeString(params.title));
            if (params?.sourceId) args.push("--source-id", safeString(params.sourceId));

            if (useTempFile) {
              const tempDir = path.join(REPO_ROOT, ".temp", "mars-ingest");
              await mkdir(tempDir, { recursive: true });
              tempFilePath = path.join(tempDir, `doc-${Date.now()}.txt`);
              await writeFile(tempFilePath, text, "utf8");
              args.push("--file", tempFilePath);
            } else {
              args.push("--text", text);
            }

            const result = await runMarsCommand(cfg, args);

            // Clean up temp file if created
            if (tempFilePath) {
              try {
                const fs = await import("node:fs/promises");
                await fs.unlink(tempFilePath);
              } catch {
                // ignore cleanup errors
              }
            }

            return formatMarsResult("MARS ingest text", result);
          },
        },
        { name: "mars_memory_ingest_text" },
      );

      api.registerTool(
        {
          name: "mars_memory_digest",
          label: "MARS Digest Project Messages",
          description: "Extract candidate memories and optionally commit them for a project.",
          parameters: {
            type: "object",
            required: ["projectId"],
            properties: {
              projectId: { type: "string" },
              messageCount: { type: "number" },
              autoCommit: { type: "boolean" },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            if (!projectId) return formatToolResult("提取失败：projectId 不能为空。", { ok: false });
            const args = [
              "digest",
              "--project-id",
              projectId,
              "--message-count",
              String(Math.max(1, Math.floor(asNumber(params?.messageCount, 100)))),
              "--json",
            ];
            if (params?.autoCommit !== false) args.push("--auto-commit");
            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS digest", result);
          },
        },
        { name: "mars_memory_digest" },
      );

      api.registerTool(
        {
          name: "mars_memory_search",
          label: "MARS Search Project Memory",
          description: "Search active MARS memories for a project.",
          parameters: {
            type: "object",
            required: ["projectId", "query"],
            properties: {
              projectId: { type: "string" },
              query: { type: "string" },
              topK: { type: "number" },
              timeScope: { type: "string", enum: ["current", "all", "history"] },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const query = safeString(params?.query);
            if (!projectId || !query) return formatToolResult("查询失败：projectId 和 query 不能为空。", { ok: false });
            const result = await runMarsCommand(cfg, [
              "search",
              "--project-id",
              projectId,
              "--query",
              query,
              "--time-scope",
              safeString(params?.timeScope) || "current",
              "--top-k",
              String(Math.max(1, Math.floor(asNumber(params?.topK, 5)))),
              "--json",
            ]);
            return formatMarsResult("MARS search", result);
          },
        },
        { name: "mars_memory_search" },
      );

      api.registerTool(
        {
          name: "mars_memory_retrieval_logs",
          label: "MARS Retrieval Logs",
          description: "Inspect recent MARS retrieval audit logs, including selected memory IDs and scoring details.",
          parameters: {
            type: "object",
            properties: {
              projectId: { type: "string", description: "Optional project ID filter." },
              limit: { type: "number", description: "Maximum logs to return (default: 20)." },
            },
          },
          async execute(_toolCallId, params) {
            const args = [
              "retrieval-logs",
              "--limit",
              String(Math.max(1, Math.floor(asNumber(params?.limit, 20)))),
              "--json",
            ];
            if (params?.projectId) args.push("--project-id", safeString(params.projectId));
            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS retrieval logs", result);
          },
        },
        { name: "mars_memory_retrieval_logs" },
      );

      api.registerTool(
        {
          name: "mars_memory_reconcile",
          label: "MARS Reconcile Project Memory",
          description: "Auto-reconcile supersede relationships for a MARS project.",
          parameters: {
            type: "object",
            required: ["projectId"],
            properties: {
              projectId: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            if (!projectId) return formatToolResult("协调失败：projectId 不能为空。", { ok: false });
            const result = await runMarsCommand(cfg, ["reconcile", "--project-id", projectId, "--auto", "--json"]);
            return formatMarsResult("MARS reconcile", result);
          },
        },
        { name: "mars_memory_reconcile" },
      );

      api.registerTool(
        {
          name: "mars_memory_consolidate",
          label: "MARS Consolidation Proposals",
          description: "Inspect advisory duplicate, update, conflict, and support proposals before OpenClaw decides how to merge or push a decision memory.",
          parameters: {
            type: "object",
            required: ["projectId"],
            properties: {
              projectId: { type: "string" },
              memoriesOnly: { type: "boolean" },
              limit: { type: "number" },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            if (!projectId) return formatToolResult("Consolidation failed: projectId is required.", { ok: false });
            const args = [
              "consolidate",
              "--project-id",
              projectId,
              "--limit",
              String(Math.max(1, Math.floor(asNumber(params?.limit, 30)))),
              "--json",
            ];
            if (params?.memoriesOnly) args.push("--memories-only");
            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS consolidate", result);
          },
        },
        { name: "mars_memory_consolidate" },
      );

      api.registerTool(
        {
          name: "mars_memory_run_benchmark",
          label: "MARS Quality Benchmark",
          description: "Run the local MARS quality benchmark and return report paths plus pass/fail metrics.",
          parameters: {
            type: "object",
            properties: {
              outputDir: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            const args = ["run-benchmark", "--json"];
            if (params?.outputDir) args.push("--output-dir", safeString(params.outputDir));
            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS benchmark", result);
          },
        },
        { name: "mars_memory_run_benchmark" },
      );

      api.registerTool(
        {
          name: "mars_memory_consolidation_eval",
          label: "MARS Consolidation Eval",
          description: "Run the local consolidation evaluation for duplicate/update/conflict/support proposal quality.",
          parameters: {
            type: "object",
            properties: {
              outputDir: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            const args = ["consolidation-eval", "--json"];
            if (params?.outputDir) args.push("--output-dir", safeString(params.outputDir));
            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS consolidation eval", result);
          },
        },
        { name: "mars_memory_consolidation_eval" },
      );

      api.registerTool(
        {
          name: "mars_memory_decision_card",
          label: "MARS Decision Card",
          description: "Search MARS and format the result as a decision-card JSON object.",
          parameters: {
            type: "object",
            required: ["projectId", "query"],
            properties: {
              projectId: { type: "string" },
              query: { type: "string" },
              topK: { type: "number" },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const query = safeString(params?.query);
            if (!projectId || !query) return formatToolResult("决策卡生成失败：projectId 和 query 不能为空。", { ok: false });
            const result = await runMarsCommand(cfg, [
              "search",
              "--project-id",
              projectId,
              "--query",
              query,
              "--top-k",
              String(Math.max(1, Math.floor(asNumber(params?.topK, 5)))),
              "--json",
            ]);
            if (!result.ok || !result.parsed) return formatMarsResult("MARS decision card search", result);
            const card = buildDecisionCard(result.data, projectId, query);
            return formatToolResult(JSON.stringify(card, null, 2), {
              ok: true,
              card,
              search: result.data,
            });
          },
        },
        { name: "mars_memory_decision_card" },
      );

      api.registerTool(
        {
          name: "mars_memory_doc_decision_card",
          label: "MARS Document Decision Card (Low-level Legacy)",
          description: "Low-level legacy shortcut: ingest a document, digest, reconcile, and search in one step. Do NOT use as the final user-facing Feishu decision-card flow when OpenClaw must summarize the full document or judge duplicate/update/conflict. Prefer feishu_memory_lifecycle_probe followed by feishu_memory_decision_command.",
          parameters: {
            type: "object",
            required: ["projectId", "text", "query"],
            properties: {
              projectId: { type: "string", description: "Project ID for the document." },
              text: { type: "string", description: "Text content to ingest (use --file for long texts)." },
              title: { type: "string", description: "Optional document title." },
              sourceId: { type: "string", description: "Optional source ID." },
              query: { type: "string", description: "Query for decision card generation." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const text = safeString(params?.text);
            const query = safeString(params?.query);
            if (!projectId || !text || !query) return formatToolResult("文档决策卡生成失败：projectId、text 和 query 不能为空。", { ok: false });

            // Step 1: Ingest text
            const ingestArgs = [
              "ingest-text",
              "--project-id",
              projectId,
              "--json",
            ];
            if (params?.title) ingestArgs.push("--title", safeString(params.title));
            if (params?.sourceId) ingestArgs.push("--source-id", safeString(params.sourceId));

            const useTempFile = text.length > 2000;
            let tempFilePath = null;
            if (useTempFile) {
              const tempDir = path.join(REPO_ROOT, ".temp", "mars-ingest");
              await mkdir(tempDir, { recursive: true });
              tempFilePath = path.join(tempDir, `doc-${Date.now()}.txt`);
              await writeFile(tempFilePath, text, "utf8");
              ingestArgs.push("--file", tempFilePath);
            } else {
              ingestArgs.push("--text", text);
            }

            const ingestResult = await runMarsCommand(cfg, ingestArgs);
            if (!ingestResult.ok) {
              if (tempFilePath) {
                try { await unlink(tempFilePath); } catch {}
              }
              return formatMarsResult("MARS doc decision card - ingest", ingestResult);
            }

            // Step 2: Digest with auto-commit
            const digestResult = await runMarsCommand(cfg, [
              "digest",
              "--project-id",
              projectId,
              "--auto-commit",
              "--json",
            ]);
            if (!digestResult.ok) {
              if (tempFilePath) {
                try { await unlink(tempFilePath); } catch {}
              }
              return formatMarsResult("MARS doc decision card - digest", digestResult);
            }

            // Step 3: Reconcile
            const reconcileResult = await runMarsCommand(cfg, [
              "reconcile",
              "--project-id",
              projectId,
              "--auto",
              "--json",
            ]);

            // Step 4: Generate decision card
            const searchResult = await runMarsCommand(cfg, [
              "search",
              "--project-id",
              projectId,
              "--query",
              query,
              "--top-k",
              "8",
              "--json",
            ]);

            // Clean up temp file
            if (tempFilePath) {
              try { await unlink(tempFilePath); } catch {}
            }

            if (!searchResult.ok || !searchResult.parsed) {
              return formatMarsResult("MARS doc decision card - search", searchResult);
            }

            const card = buildDecisionCard(searchResult.data, projectId, query);
            return formatToolResult(
              `文档决策卡生成完成。\n摄入：${ingestResult.data?.imported_count || 0} 块\n记忆提取：${digestResult.data?.committed_count || 0} 条\n协调：${reconcileResult.data?.count || 0} 条\n\n${JSON.stringify(card, null, 2)}`,
              {
                ok: true,
                card,
                ingest: ingestResult.data,
                digest: digestResult.data,
                reconcile: reconcileResult.data,
                search: searchResult.data,
              },
            );
          },
        },
        { name: "mars_memory_doc_decision_card" },
      );

      api.registerTool(
        {
          name: "feishu_memory_decision_command",
          label: "Feishu Memory Decision Command",
          description: "Finalize a Feishu decision card after OpenClaw has summarized the full context and made the final lifecycle judgment. For document/chat decision cards, call feishu_memory_lifecycle_probe first, then pass agentSummary and agentLifecycleDecision here.",
          parameters: {
            type: "object",
            required: ["projectId", "commandText", "agentLifecycleDecision"],
            properties: {
              projectId: { type: "string", description: "Project ID for the decision." },
              commandText: { type: "string", description: "Natural language command text." },
              contextText: { type: "string", description: "Optional context for the command." },
              agentSummary: { type: "string", description: "Optional OpenClaw-generated full-coverage structured summary. For long docs, summarize all periods/topics first and pass it here." },
              agentLifecycleDecision: {
                type: "object",
                description: "Required OpenClaw judgment for lifecycle status after comparing the full summary with probe evidence.",
                properties: {
                  status: { type: "string", enum: ["new", "update", "conflict", "duplicate"] },
                  reason: { type: "string" },
                  target_memory_id: { type: "string" },
                  recommended_action: { type: "string" },
                  confidence: { type: "number" },
                },
              },
              agentStructuredCard: {
                type: "object",
                description: "Optional OpenClaw structured extraction. Include decisions, reasons/rationale, objections/opposing views, conclusions, project_phase, time_points, and topic_links.",
                properties: {
                  decision_items: { type: "array" },
                  reasons: { type: "array" },
                  objections: { type: "array" },
                  conclusions: { type: "array" },
                  project_phase: { type: "string" },
                  time_points: { type: "array" },
                  topic_links: { type: "array" },
                  source_scope: { type: "string" },
                  evidence_items: { type: "array" },
                },
              },
              title: { type: "string", description: "Optional title for document source." },
              sourceId: { type: "string", description: "Optional source ID for idempotency." },
              sourceUrl: { type: "string", description: "Optional Feishu document/message URL." },
              sourceType: { type: "string", description: "Optional source type, for example feishu_doc or feishu_chat." },
              query: { type: "string", description: "Optional query for searching similar decisions (defaults to commandText)." },
              autoCommit: { type: "boolean", description: "Whether to auto-commit high-confidence candidates (default: false)." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const commandText = safeString(params?.commandText);
            if (!projectId || !commandText) {
              return formatToolResult("命令处理失败：projectId 和 commandText 不能为空。", { ok: false });
            }

            const contextText = safeString(params?.contextText);
            const agentSummaryText = safeString(params?.agentSummary);
            const hasAgentLifecycleDecision = params?.agentLifecycleDecision && typeof params.agentLifecycleDecision === "object";
            if (!hasAgentLifecycleDecision) {
              return formatToolResult(
                "决策卡生成被拒绝：OpenClaw 必须先调用 feishu_memory_lifecycle_probe 获取相似决策和 MARS 启发式结果，然后自行判断 new/update/conflict/duplicate，并把判断作为 agentLifecycleDecision 传入本工具。",
                {
                  ok: false,
                  required_flow: [
                    "read full source",
                    "OpenClaw full-coverage summary",
                    "feishu_memory_lifecycle_probe",
                    "OpenClaw lifecycle decision",
                    "feishu_memory_decision_command",
                  ],
                },
              );
            }
            if (contextText.length > 200 && !agentSummaryText) {
              return formatToolResult(
                "决策卡生成被拒绝：长文档/长上下文必须由 OpenClaw 先做覆盖全文的结构化总结，并通过 agentSummary 传入，避免只保留最近周期或局部内容。",
                { ok: false, required_field: "agentSummary" },
              );
            }

            // Prepare args for MARS command
            const args = [
              "command",
              "--project-id",
              projectId,
              "--command",
              commandText,
              "--json",
            ];

            if (contextText) {
              const context = contextText;
              // For long context, write to temp file
              if (context.length > 500) {
                const tempDir = path.join(REPO_ROOT, ".temp", "mars-command");
                await mkdir(tempDir, { recursive: true });
                const tempFilePath = path.join(tempDir, `context-${Date.now()}.txt`);
                await writeFile(tempFilePath, context, "utf8");
                args.push("--context-file", tempFilePath);
                // Note: temp files are periodically cleaned by system maintenance
              } else {
                args.push("--context", context);
              }
            }

            if (agentSummaryText) {
              const agentSummary = agentSummaryText;
              if (agentSummary.length > 500) {
                const tempDir = path.join(REPO_ROOT, ".temp", "mars-command");
                await mkdir(tempDir, { recursive: true });
                const tempFilePath = path.join(tempDir, `agent-summary-${Date.now()}.txt`);
                await writeFile(tempFilePath, agentSummary, "utf8");
                args.push("--agent-summary-file", tempFilePath);
              } else {
                args.push("--agent-summary", agentSummary);
              }
            }

            if (params?.title) args.push("--title", safeString(params.title));
            if (params?.sourceId) args.push("--source-id", safeString(params.sourceId));
            if (params?.query) args.push("--query", safeString(params.query));
            if (params?.agentLifecycleDecision && typeof params.agentLifecycleDecision === "object") {
              const tempDir = path.join(REPO_ROOT, ".temp", "mars-command");
              await mkdir(tempDir, { recursive: true });
              const tempFilePath = path.join(tempDir, `agent-lifecycle-${Date.now()}.json`);
              await writeFile(tempFilePath, JSON.stringify(params.agentLifecycleDecision), "utf8");
              args.push("--agent-lifecycle-file", tempFilePath);
            }
            if (params?.agentStructuredCard && typeof params.agentStructuredCard === "object") {
              const tempDir = path.join(REPO_ROOT, ".temp", "mars-command");
              await mkdir(tempDir, { recursive: true });
              const tempFilePath = path.join(tempDir, `agent-structured-card-${Date.now()}.json`);
              await writeFile(tempFilePath, JSON.stringify(params.agentStructuredCard), "utf8");
              args.push("--agent-structured-card-file", tempFilePath);
            }
            if (params?.autoCommit === true) args.push("--auto-commit");

            const result = await runMarsCommand(cfg, args);

            if (!result.ok || !result.parsed) {
              return formatMarsResult("Feishu memory decision command", result);
            }

            const data = result.data;
            const card = normalizeDecisionCard(data.decision_card || {}, {
              projectId,
              commandText,
              contextText,
              agentSummary: agentSummaryText,
              agentStructuredCard: params?.agentStructuredCard,
              title: safeString(params?.title),
              sourceId: safeString(params?.sourceId),
              sourceUrl: safeString(params?.sourceUrl),
              sourceType: safeString(params?.sourceType),
            });
            const lifecycle = card.lifecycle || {};
            const similarDecisions = data.similar_decisions || [];
            const feishuInteractiveCard = buildFeishuInteractiveDecisionCard(card);
            const bitableRecordFields = buildBitableGovernanceFields(card, { projectId });

            // Build human-readable response
            let responseText = `决策卡预览：${card.title || commandText}\n`;
            responseText += `状态：${lifecycle.status || "unknown"}\n`;
            if (lifecycle.agent_decision) {
              responseText += `OpenClaw判断：${lifecycle.agent_decision.status} - ${lifecycle.agent_decision.reason || "无理由"}\n`;
            }
            if (lifecycle.heuristic_status && lifecycle.heuristic_status !== lifecycle.status) {
              responseText += `MARS启发式判断：${lifecycle.heuristic_status}\n`;
            }
            responseText += `建议操作：${lifecycle.recommended_action || "none"}\n`;
            responseText += `需要确认：${lifecycle.requires_confirmation ? "是" : "否"}\n`;

            if (similarDecisions.length > 0) {
              responseText += `\n相似决策 (${similarDecisions.length} 条)：\n`;
              similarDecisions.slice(0, 5).forEach((sim, i) => {
                responseText += `${i + 1}. [${sim.relation}] ${sim.decision?.title || "N/A"} (${(sim.confidence * 100).toFixed(0)}%)\n`;
              });
            }

            if (card.decisions && card.decisions.length > 0) {
              responseText += `\n决策内容：\n`;
              card.decisions.slice(0, 3).forEach((dec, i) => {
                responseText += `${i + 1}. ${dec.substring(0, 100)}${dec.length > 100 ? "..." : ""}\n`;
              });
            }

            if (card.open_questions && card.open_questions.length > 0) {
              responseText += `\n待确认问题：\n${card.open_questions.map(q => `- ${q}`).join("\n")}\n`;
            }

            return formatToolResult(responseText, {
              ok: true,
              decision_card: card,
              feishu_interactive_card: feishuInteractiveCard,
              bitable_record_fields: bitableRecordFields,
              bitable_schema_fields: buildBitableSchema(),
              similar_decisions: similarDecisions,
            });
          },
        },
        { name: "feishu_memory_decision_command" },
      );

      api.registerTool(
        {
          name: "feishu_memory_render_interactive_card",
          label: "Render Feishu Memory Interactive Card",
          description: "Render a decision_card object as Feishu interactive card JSON. Send the returned card through the OpenClaw message tool with the card parameter.",
          parameters: {
            type: "object",
            required: ["decisionCard"],
            properties: {
              decisionCard: { type: "object", description: "Decision card returned by feishu_memory_decision_command." },
              operationId: { type: "string", description: "Optional stable operation ID for card button callbacks." },
            },
          },
          async execute(_toolCallId, params) {
            const card = normalizeDecisionCard(params?.decisionCard || {});
            const feishuCard = buildFeishuInteractiveDecisionCard(card, { operationId: safeString(params?.operationId) });
            return formatToolResult("Feishu interactive card JSON generated.", {
              ok: true,
              feishu_interactive_card: feishuCard,
              send_hint: "Use OpenClaw message send with the returned object as card. Button callbacks still need the governance action handler.",
            });
          },
        },
        { name: "feishu_memory_render_interactive_card" },
      );

      api.registerTool(
        {
          name: "feishu_memory_bitable_governance_schema",
          label: "Feishu Memory Bitable Governance Schema",
          description: "Return the recommended Bitable fields for decision-card governance.",
          parameters: {
            type: "object",
            properties: {
              projectId: { type: "string", description: "Optional project ID for documentation." },
            },
          },
          async execute(_toolCallId, params) {
            return formatToolResult("Bitable governance schema generated.", {
              ok: true,
              project_id: safeString(params?.projectId),
              fields: buildBitableSchema(),
              tool_hint: "Create fields with feishu_bitable_app_table_field.create, then write records with feishu_bitable_app_table_record.create/update.",
            });
          },
        },
        { name: "feishu_memory_bitable_governance_schema" },
      );

      api.registerTool(
        {
          name: "feishu_memory_bitable_record_payload",
          label: "Feishu Memory Bitable Record Payload",
          description: "Build the Bitable record fields payload for a decision card, including lifecycle and evidence-chain governance fields.",
          parameters: {
            type: "object",
            required: ["decisionCard"],
            properties: {
              decisionCard: { type: "object", description: "Decision card returned by feishu_memory_decision_command." },
              projectId: { type: "string", description: "Optional project ID override." },
              governanceStatus: { type: "string", description: "pending_review/active/deprecated/ignored/conflict_review." },
              memoryId: { type: "string", description: "Optional existing memory ID." },
            },
          },
          async execute(_toolCallId, params) {
            const card = normalizeDecisionCard(params?.decisionCard || {}, {
              projectId: safeString(params?.projectId),
            });
            const fields = buildBitableGovernanceFields(card, {
              projectId: safeString(params?.projectId),
              governanceStatus: safeString(params?.governanceStatus),
              memoryId: safeString(params?.memoryId),
            });
            return formatToolResult("Bitable record payload generated.", {
              ok: true,
              fields,
              record: { fields },
              tool_hint: "Use feishu_bitable_app_table_record.create for new rows or update with record_id for existing rows.",
            });
          },
        },
        { name: "feishu_memory_bitable_record_payload" },
      );

      api.registerTool(
        {
          name: "feishu_memory_bitable_sync",
          label: "Sync Feishu Memory Decision To Bitable",
          description: "Create or update a Feishu Bitable governance record for a decision card. Uses configured project bitableAppToken/bitableTableId and stores a local record_id mapping for future updates.",
          parameters: {
            type: "object",
            required: ["decisionCard"],
            properties: {
              decisionCard: { type: "object", description: "Decision card returned by feishu_memory_decision_command." },
              projectId: { type: "string" },
              bitableUrl: { type: "string", description: "Optional Feishu Bitable URL; app token and table ID will be parsed from it." },
              bitableAppToken: { type: "string", description: "Override app token when project config does not provide it." },
              bitableTableId: { type: "string", description: "Override table ID when project config does not provide it." },
              recordId: { type: "string", description: "Existing Bitable record_id to update." },
              governanceStatus: { type: "string", description: "pending_review/active/deprecated/ignored/conflict_review." },
              dryRun: { type: "boolean", description: "Return payload and mapping decision without calling Feishu OpenAPI." },
              appId: { type: "string", description: "Optional Feishu app_id override." },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const card = normalizeDecisionCard(params?.decisionCard || {}, {
              projectId: safeString(params?.projectId),
            });
            const fields = buildBitableGovernanceFields(card, {
              projectId: safeString(params?.projectId),
              governanceStatus: safeString(params?.governanceStatus),
            });
            const projectId = safeString(fields.project_id) || safeString(params?.projectId);
            const { appToken, tableId } = resolveBitableConfig(cfg, store, params || {}, projectId);
            const decisionKey = makeDecisionKey(card, fields);
            const mapping = store.findBitableMapping({ projectId, decisionKey, appToken, tableId });
            const recordId = safeString(params?.recordId) || safeString(mapping?.recordId);
            const operation = recordId ? "update" : "create";

            if (!appToken || !tableId) {
              return formatToolResult("Bitable sync is not configured for this project.", {
                ok: false,
                missing: {
                  bitableAppToken: !appToken,
                  bitableTableId: !tableId,
                },
                fields,
              });
            }

            if (params?.dryRun === true) {
              return formatToolResult(`Bitable sync dry run: ${operation}.`, {
                ok: true,
                dry_run: true,
                operation,
                decision_key: decisionKey,
                record_id: recordId,
                app_token: appToken,
                table_id: tableId,
                record: { fields },
              });
            }

            try {
              const encodedApp = encodeURIComponent(appToken);
              const encodedTable = encodeURIComponent(tableId);
              const body = JSON.stringify({ fields });
              const data = recordId
                ? await feishuOpenApi(
                    `/open-apis/bitable/v1/apps/${encodedApp}/tables/${encodedTable}/records/${encodeURIComponent(recordId)}`,
                    { method: "PUT", body },
                    { appId: safeString(params?.appId) },
                  )
                : await feishuOpenApi(
                    `/open-apis/bitable/v1/apps/${encodedApp}/tables/${encodedTable}/records`,
                    { method: "POST", body },
                    { appId: safeString(params?.appId) },
                  );
              const syncedRecordId = safeString(data?.data?.record?.record_id) || recordId;
              const savedMapping = await store.upsertBitableMapping({
                projectId,
                decisionKey,
                appToken,
                tableId,
                recordId: syncedRecordId,
                sourceId: safeString(fields.source_id),
                title: safeString(fields.title),
              });
              store.logEvent("bitable_governance_synced", {
                projectId,
                decisionKey,
                operation,
                recordId: syncedRecordId,
              });
              await store.save();
              return formatToolResult(`Bitable governance record ${operation} succeeded.`, {
                ok: true,
                operation,
                record_id: syncedRecordId,
                mapping: savedMapping,
                feishu_data: data?.data || {},
              });
            } catch (error) {
              return formatToolResult(`Bitable sync failed: ${error?.message || String(error)}`, {
                ok: false,
                operation,
                record_id: recordId,
                decision_key: decisionKey,
                record: { fields },
              });
            }
          },
        },
        { name: "feishu_memory_bitable_sync" },
      );

      api.registerTool(
        {
          name: "feishu_memory_governance_action",
          label: "Feishu Memory Governance Action",
          description: "Apply or prepare a governance action from an interactive decision card: confirm, update existing, ignore, or mark conflict.",
          parameters: {
            type: "object",
            required: ["action"],
            properties: {
              action: { type: "string", enum: ["confirm_decision", "update_decision", "ignore_decision", "mark_conflict"] },
              decisionCard: { type: "object", description: "Optional decision card related to the action." },
              memoryId: { type: "string", description: "Optional local memory ID to update." },
              targetMemoryId: { type: "string", description: "Optional existing memory ID for update/conflict actions." },
              projectId: { type: "string", description: "Optional project ID." },
              userId: { type: "string", description: "Optional reviewer user ID." },
              reason: { type: "string", description: "Optional human or OpenClaw action reason." },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const action = safeString(params?.action);
            const memoryId = safeString(params?.memoryId);
            const targetMemoryId = safeString(params?.targetMemoryId);
            const userId = safeString(params?.userId) || "openclaw";
            const reason = safeString(params?.reason);
            const statusByAction = {
              confirm_decision: "active",
              update_decision: "active",
              ignore_decision: "ignored",
              mark_conflict: "conflict_review",
            };
            const governanceStatus = statusByAction[action] || "pending_review";
            let updatedMemory = null;
            if (memoryId && action === "confirm_decision") {
              updatedMemory = await store.confirmMemory(memoryId, userId, "active");
            } else if (memoryId && action === "ignore_decision") {
              updatedMemory = await store.confirmMemory(memoryId, userId, "deprecated");
            }
            const card = normalizeDecisionCard(params?.decisionCard || {}, {
              projectId: safeString(params?.projectId),
            });
            const fields = buildBitableGovernanceFields(card, {
              projectId: safeString(params?.projectId),
              governanceStatus,
              memoryId: memoryId || targetMemoryId,
            });
            fields.governance_status = governanceStatus;
            fields.target_memory_id = targetMemoryId || fields.target_memory_id;
            fields.conflict_notes = [fields.conflict_notes, reason].filter(Boolean).join("\n");
            fields.updated_at = nowIso();
            store.logEvent("memory_governance_action", {
              action,
              memoryId,
              targetMemoryId,
              projectId: safeString(params?.projectId),
              governanceStatus,
            });
            await store.save();
            return formatToolResult(`Governance action prepared: ${action} -> ${governanceStatus}`, {
              ok: true,
              action,
              governance_status: governanceStatus,
              updated_memory: updatedMemory,
              bitable_update_fields: fields,
              record: { fields },
              next_step: "Use feishu_bitable_app_table_record.update when a Bitable record_id exists; otherwise create a governance row first.",
            });
          },
        },
        { name: "feishu_memory_governance_action" },
      );

      api.registerTool(
        {
          name: "mars_memory_similar_decisions",
          label: "MARS Similar Decisions",
          description: "Find similar decisions and classify their relationship (duplicate/update/conflict/new).",
          parameters: {
            type: "object",
            required: ["projectId", "query"],
            properties: {
              projectId: { type: "string", description: "Project ID." },
              query: { type: "string", description: "Search query for finding similar decisions." },
              text: { type: "string", description: "Optional full text for more detailed comparison." },
              topK: { type: "number", description: "Maximum results (default: 5)." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const query = safeString(params?.query);
            if (!projectId || !query) {
              return formatToolResult("相似决策查询失败：projectId 和 query 不能为空。", { ok: false });
            }

            const args = [
              "similar-decisions",
              "--project-id",
              projectId,
              "--query",
              query,
              "--top-k",
              String(Math.max(1, Math.floor(asNumber(params?.topK, 5)))),
              "--json",
            ];

            if (params?.text) args.push("--text", safeString(params.text));

            const result = await runMarsCommand(cfg, args);
            return formatMarsResult("MARS similar decisions", result);
          },
        },
        { name: "mars_memory_similar_decisions" },
      );

      api.registerTool(
        {
          name: "feishu_memory_lifecycle_probe",
          label: "Feishu Memory Lifecycle Probe",
          description: "Probe existing memories before OpenClaw decides whether a decision is new, duplicate, update, or conflict. This tool returns evidence and MARS heuristic only; OpenClaw must make the final lifecycle judgment.",
          parameters: {
            type: "object",
            required: ["projectId", "query"],
            properties: {
              projectId: { type: "string", description: "Project ID." },
              query: { type: "string", description: "Query extracted from the current document/message." },
              summaryText: { type: "string", description: "Optional OpenClaw full-coverage summary for better comparison." },
              topK: { type: "number", description: "Maximum similar decisions to return (default: 5)." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const query = safeString(params?.query);
            if (!projectId || !query) {
              return formatToolResult("生命周期探查失败：projectId 和 query 不能为空。", { ok: false });
            }

            const args = [
              "similar-decisions",
              "--project-id",
              projectId,
              "--query",
              query,
              "--top-k",
              String(Math.max(1, Math.floor(asNumber(params?.topK, 5)))),
              "--json",
            ];
            if (params?.summaryText) {
              args.push("--text", safeString(params.summaryText));
            }

            const result = await runMarsCommand(cfg, args);
            if (!result.ok || !result.parsed) {
              return formatMarsResult("Feishu memory lifecycle probe", result);
            }

            const similar = result.data?.similar_decisions || [];
            const heuristic = similar[0]?.relation || "new";
            const responseText = [
              `MARS启发式建议：${heuristic}`,
              `相似决策数量：${similar.length}`,
              "请 OpenClaw 基于全文总结和以下证据自行判断最终 lifecycle，再调用 feishu_memory_decision_command 传入 agentLifecycleDecision。",
            ].join("\n");

            return formatToolResult(responseText, {
              ok: true,
              heuristic_status: heuristic,
              similar_decisions: similar,
            });
          },
        },
        { name: "feishu_memory_lifecycle_probe" },
      );

      api.registerTool(
        {
          name: "feishu_memory_active_push_probe",
          label: "Feishu Memory Active Push Probe",
          description: "Check whether the current Feishu discussion should trigger an active push of historical decision cards. Use when new chat/document text touches prior decisions, reasons, objections, conclusions, project phases, or deadlines.",
          parameters: {
            type: "object",
            required: ["projectId", "currentText"],
            properties: {
              projectId: { type: "string", description: "Project ID." },
              currentText: { type: "string", description: "Current chat/document context to check." },
              query: { type: "string", description: "Optional focused query; defaults to currentText." },
              topK: { type: "number", description: "Maximum historical decisions to return (default: 3)." },
              minConfidence: { type: "number", description: "Minimum similarity confidence for push (default: 0.25)." },
            },
          },
          async execute(_toolCallId, params) {
            const projectId = safeString(params?.projectId);
            const currentText = safeString(params?.currentText);
            const query = safeString(params?.query) || currentText.slice(0, 160);
            if (!projectId || !currentText) {
              return formatToolResult("主动推送探测失败：projectId 和 currentText 不能为空。", { ok: false });
            }

            const topK = Math.max(1, Math.floor(asNumber(params?.topK, 3)));
            const minConfidence = Math.max(0, Math.min(1, asNumber(params?.minConfidence, 0.25)));
            const result = await runMarsCommand(cfg, [
              "similar-decisions",
              "--project-id",
              projectId,
              "--query",
              query,
              "--text",
              currentText,
              "--top-k",
              String(topK),
              "--json",
            ]);

            if (!result.ok || !result.parsed) {
              return formatMarsResult("Feishu active push probe", result);
            }

            const similar = Array.isArray(result.data?.similar_decisions) ? result.data.similar_decisions : [];
            const pushCandidates = similar.filter((item) => asNumber(item?.confidence, 0) >= minConfidence);
            const shouldPush = pushCandidates.length > 0;
            const reason = shouldPush
              ? `当前讨论命中 ${pushCandidates.length} 条历史决策，建议主动推送历史决策卡，避免重复讨论或遗漏旧约束。`
              : "未发现足够相似的历史决策，暂不主动推送。";
            const cards = pushCandidates.map((item) => ({
              memory_id: item.memory_id,
              relation: item.relation,
              confidence: item.confidence,
              reason: item.reason,
              title: item.decision?.title || "",
              topic: item.decision?.topic || "",
              content: item.decision?.content || "",
            }));

            return formatToolResult(
              [`主动推送：${shouldPush ? "是" : "否"}`, `原因：${reason}`].join("\n"),
              {
                ok: true,
                shouldPush,
                reason,
                cards,
                similar_decisions: similar,
              },
            );
          },
        },
        { name: "feishu_memory_active_push_probe" },
      );

      api.registerTool(
        {
          name: "feishu_memory_confirm",
          label: "Confirm Feishu Project Memory",
          description: "Confirm or deprecate a Feishu project memory.",
          parameters: {
            type: "object",
            required: ["memoryId"],
            properties: {
              memoryId: { type: "string" },
              status: { type: "string", enum: ["active", "deprecated", "pending"] },
              userId: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const memory = await store.confirmMemory(
              safeString(params?.memoryId),
              safeString(params?.userId),
              safeString(params?.status) || "active",
            );
            if (!memory) return formatToolResult("未找到对应记忆。", { ok: false });
            return formatToolResult(`记忆状态已更新：${memory.title} -> ${memory.status}`, { ok: true, memory });
          },
        },
        { name: "feishu_memory_confirm" },
      );

      api.registerTool(
        {
          name: "feishu_memory_search",
          label: "Search Feishu Project Memory",
          description: "Search confirmed Feishu project memories by query.",
          parameters: {
            type: "object",
            required: ["query"],
            properties: {
              query: { type: "string" },
              projectId: { type: "string" },
              chatId: { type: "string" },
              limit: { type: "number" },
              includePending: { type: "boolean" },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const query = safeString(params?.query);
            if (!query) return formatToolResult("查询失败：query 不能为空。", { ok: false });
            const results = store.search(query, {
              projectId: safeString(params?.projectId),
              chatId: safeString(params?.chatId),
              limit: asNumber(params?.limit, cfg.maxRecallItems),
              includePending: params?.includePending === true,
            });
            if (results.length === 0) return formatToolResult("没有找到相关项目记忆。", { ok: true, count: 0 });
            const text = results
              .map(({ memory, score }, index) => {
                const source = memory.sourceUrl ? `\n   来源：${memory.sourceUrl}` : "";
                return `${index + 1}. ${memory.title}\n   类型：${memory.type} / 状态：${memory.status} / 分数：${score.toFixed(2)}\n   摘要：${memory.summary}${source}`;
              })
              .join("\n\n");
            return formatToolResult(`找到 ${results.length} 条项目记忆：\n\n${text}`, {
              ok: true,
              count: results.length,
              results,
            });
          },
        },
        { name: "feishu_memory_search" },
      );

      api.registerTool(
        {
          name: "feishu_memory_onboarding_pack",
          label: "Generate Feishu Project Onboarding Pack",
          description: "Generate a short onboarding pack from active project memories.",
          parameters: {
            type: "object",
            properties: {
              projectId: { type: "string" },
              chatId: { type: "string" },
            },
          },
          async execute(_toolCallId, params) {
            await ready;
            const project = store.findProject(safeString(params?.projectId), safeString(params?.chatId));
            const projectId = project?.projectId || safeString(params?.projectId);
            const memories = store
              .list({ projectId, status: "active" })
              .sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
            const grouped = {
              decision: memories.filter((m) => m.type === "decision").slice(0, 5),
              fact: memories.filter((m) => m.type === "fact").slice(0, 5),
              pitfall: memories.filter((m) => m.type === "pitfall").slice(0, 5),
            };
            const section = (title, items) =>
              items.length
                ? `\n${title}\n${items.map((m, i) => `${i + 1}. ${m.summary}${m.sourceUrl ? `（来源：${m.sourceUrl}）` : ""}`).join("\n")}`
                : `\n${title}\n暂无`;
            const text = `新人上下文包：${project?.projectName || projectId || "默认项目"}\n${section("关键决策", grouped.decision)}\n${section("项目事实", grouped.fact)}\n${section("常见踩坑", grouped.pitfall)}`;
            return formatToolResult(text, { ok: true, projectId, count: memories.length });
          },
        },
        { name: "feishu_memory_onboarding_pack" },
      );

      if (cfg.autoRecall) {
        api.on("before_agent_start", async (event, ctx) => {
          await ready;
          const prompt = safeString(event?.prompt);
          if (prompt.length < 5) return;
          const workflowContext = buildDecisionWorkflowContext(prompt);
          const results = store.search(prompt, { limit: cfg.maxRecallItems });
          const strongResults = results.filter((item) => item.score >= cfg.minRecallScore);
          if (strongResults.length === 0 && !workflowContext) return;
          const memoryContext = strongResults.length > 0 ? buildMemoryContext(strongResults) : "";
          const prependContext = [workflowContext, memoryContext].filter(Boolean).join("\n\n");
          api.logger.info(`${PLUGIN_ID}: injecting decision workflow and ${strongResults.length} enterprise memories, session=${ctx?.sessionKey || ""}`);
          return { prependContext };
        });
      }

      if (cfg.autoCaptureLog) {
        api.on("agent_end", async (event, ctx) => {
          await ready;
          const messages = Array.isArray(event?.messages) ? event.messages : [];
          store.logEvent("agent_end_observed", {
            success: event?.success === true,
            messageCount: messages.length,
            sessionKey: ctx?.sessionKey || "",
            agentId: ctx?.agentId || "",
          });
          await store.save();
          api.logger.info(`${PLUGIN_ID}: agent_end observed, messages=${messages.length}`);
        });
      }

      api.registerService({
        id: PLUGIN_ID,
        async start() {
          await ready;
          api.logger.info(`${PLUGIN_ID}: service started, store=${store.filePath}`);
        },
        async stop() {
          await store?.save();
          api.logger.info(`${PLUGIN_ID}: service stopped`);
        },
      });
    },
  };
}

export default definePluginEntry(makePlugin());
