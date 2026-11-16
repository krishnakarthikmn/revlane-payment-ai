import { API_URL } from "./api";
import type { BotDraft } from "./console/state";
import type { Agent, Attachment, Pair, RoutingRule, Status } from "./fixtures";
import { formatInr } from "./format";

/** What the console's real screens show: GET /console. */
export type LiveProposal = {
  id: string;
  action_type: string;
  status: "pending" | "approved" | "rejected" | "expired";
  title: string;
  rationale: string;
  amount_minor: number;
  claim_ids: string[];
  documents: string[];
};
export type LiveFinding = { id: string; label: string; state: string; amount_minor: number };
export type LiveRule = { key: string; label: string; value: number };
export type LiveAgent = {
  playbook: string;
  autonomy_level: number;
  enabled: boolean;
  lost_minor: number;
  headline: string | null;
  causes: { label: string; pct: number }[];
  tools: string;
  proposals: LiveProposal[];
  findings: LiveFinding[];
  meta: Pair[];
  rules: LiveRule[];
  last_run: { status: string; error: string | null } | null;
};
export type LiveNote = {
  id: string;
  title: string;
  body: string;
  dot: string;
  link: string;
  time: string;
};
export type LiveConsole = {
  merchant: { name: string };
  sample_data: boolean;
  setup: { has_data: boolean; has_contracts: boolean; has_audit: boolean };
  audit: { running: boolean; last_finished_at: string | null };
  agents: Record<string, LiveAgent>;
  notifications: LiveNote[];
};

/** An agent as the screens use it: sample data, with live data laid over the real ones. */
export type ConsoleAgent = Agent & { preview: boolean; live?: LiveAgent };

export const NOT_RUN = "No audit has run yet. Run the audit to see what this agent finds.";
export const NOTHING_WAITING = "Nothing is waiting for you.";
export const NOTHING_SENT = "Nothing has been sent: download the draft and send it yourself.";

export function currentProposal(agent: LiveAgent): LiveProposal | undefined {
  return agent.proposals.find((proposal) => proposal.status === "pending");
}

/** A real agent needs you while any proposal waits; otherwise it shows its last decision. */
export function liveStatus(agent: LiveAgent): Status {
  if (currentProposal(agent)) return "pending";
  if (agent.proposals.some((proposal) => proposal.status === "approved")) return "approved";
  if (agent.proposals.some((proposal) => proposal.status === "rejected")) return "rejected";
  return "idle";
}

export function mergeAgents(agents: Agent[], live: LiveConsole | null): ConsoleAgent[] {
  return agents.map((agent) => {
    const own = live?.agents[agent.key];
    if (!own) return { ...agent, preview: true };
    return {
      ...agent,
      preview: false,
      live: own,
      lost_minor: own.lost_minor,
      headline: own.headline ?? NOT_RUN,
      causes: own.causes,
      proposal: currentProposal(own)?.title ?? NOTHING_WAITING,
      meta: own.meta,
      tools: own.tools || agent.tools,
      settings: own.rules.map((rule) => ({ k: rule.label, v: String(rule.value) })),
    };
  });
}

/** "₹22.6L slipped away · 4 fixes waiting", from what the agents show now. */
export function summaryText(agents: ConsoleAgent[], pending: number): string {
  const total = agents.reduce((sum, agent) => sum + agent.lost_minor, 0);
  const fixes = pending === 1 ? "1 fix waiting" : `${pending} fixes waiting`;
  return `${formatInr(total)} slipped away · ${fixes}`;
}

// Sample-data replies kept for the surfaces that are still previews.
const PREVIEW_KINDS = new Set([
  "drop",
  "cb",
  "subs",
  "routing",
  "wf",
  "routine",
  "routines",
  "skill",
]);

/**
 * The sample reply a question gets, if it is about a preview surface; null if
 * it should be asked of the real backend. The prototype's "why did success
 * drop" rule matches any mention of success, so it is narrowed here: a
 * question about success rates is a real one.
 */
export function previewKind(
  question: string,
  attach: Attachment[],
  rules: RoutingRule[],
): string | null {
  const text = question.toLowerCase();
  for (const rule of rules) {
    if (rule.attachment_kind) {
      if (attach.some((a) => a.kind === rule.attachment_kind)) return null;
      continue;
    }
    if (!rule.any?.some((word) => text.includes(word))) continue;
    if (!PREVIEW_KINDS.has(rule.kind)) return null;
    if (rule.kind === "drop" && !/drop|fell|why did/.test(text)) return null;
    return rule.kind;
  }
  return null;
}

// --- Ask Revlane: the chat endpoint's event stream ---------------------------

export type ChatEvent = { event: string; data: Record<string, unknown> };

/** Splits what has arrived into whole events and the unfinished remainder. */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: ChatEvent[] = [];
  for (const block of blocks) {
    const name = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    if (name && data) events.push({ event: name, data: JSON.parse(data) });
  }
  return { events, rest };
}

type Table = {
  columns: { key: string; label: string }[];
  rows: { values: Record<string, unknown>; display: Record<string, string> }[];
};

const NEXT_STEPS: Record<string, { label: string; action: string }> = {
  leakage_audit: { label: "Review fixes", action: "fixes" },
  reconciliation: { label: "Open Reconciliation", action: "card:recon" },
  fee_audit: { label: "Open Fee audit", action: "card:fees" },
};

/** One answer's events as the message the chat shows. */
export function chatMessage(events: ChatEvent[]): BotDraft {
  const message: BotDraft = { text: "", steps: [] };
  let table: Table | undefined;
  for (const { event, data } of events) {
    if (event === "query") {
      const spec = data.spec as { metrics: string[] };
      message.steps.push(`query(${spec.metrics.join(", ")})`);
    } else if (event === "step" && typeof data.tool === "string") {
      message.steps.push(data.tool);
    } else if (event === "table") {
      table = data as unknown as Table;
    } else if (event === "chart" && table) {
      const label = String(data.label_key);
      const value = String(data.value_key);
      const amounts = table.rows.map((row) => Number(row.values[value]) || 0);
      const largest = Math.max(...amounts, 1);
      message.bars = table.rows.map((row, i) => ({
        label: String(row.display[label]),
        display: String(row.display[value]),
        share: Math.round(((amounts[i] ?? 0) / largest) * 100),
      }));
    } else if (event === "answer") {
      message.text = String(data.text);
      const next = data.next_step as { playbook: string } | null;
      const step = next ? NEXT_STEPS[next.playbook] : undefined;
      if (step) {
        message.actionLabel = step.label;
        message.action = step.action;
      }
    } else if (event === "refusal") {
      message.text = String(data.text);
    } else if (event === "error") {
      message.text = String(data.message);
    } else if (event === "sources") {
      message.source = `Source: ${(data.query_ids as string[]).join(" · ")}`;
    }
  }
  // A split answer gets a table; a single total is already in the sentence.
  if (table && table.columns.length > 1 && table.rows.length > 0) {
    const keys = table.columns.map((column) => column.key);
    const wide = (cells: string[]): string[] =>
      cells.length === 2 ? [cells[0] ?? "", "", cells[1] ?? ""] : cells.slice(0, 3);
    message.rows = [
      wide(table.columns.map((column) => column.label)),
      ...table.rows.map((row) => wide(keys.map((key) => row.display[key] ?? "—"))),
    ];
  }
  if (!message.text) message.text = "I could not get an answer just now. Try again.";
  return message;
}

/** Asks one question and reports each event as it arrives. Never throws. */
export async function askChat(
  question: string,
  onEvent: (event: ChatEvent) => void,
  fetcher: typeof fetch = fetch,
): Promise<ChatEvent[]> {
  const seen: ChatEvent[] = [];
  try {
    const response = await fetcher(`${API_URL}/chat`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!response.ok || !response.body) {
      return [{ event: "error", data: { message: `The API answered ${response.status}` } }];
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const { events, rest } = parseSse(done ? `${buffer}\n\n` : buffer);
      buffer = rest;
      for (const event of events) {
        seen.push(event);
        onEvent(event);
      }
      if (done) return seen;
    }
  } catch {
    return [...seen, { event: "error", data: { message: `No answer from ${API_URL}` } }];
  }
}

// --- The other calls behind the console's buttons ---------------------------------

export type Result<T> = { ok: true; value: T } | { ok: false; message: string };
type ErrorBody = { error?: { message?: string } };

export async function call<T>(
  path: string,
  init?: RequestInit,
  fetcher: typeof fetch = fetch,
): Promise<Result<T>> {
  try {
    const response = await fetcher(`${API_URL}${path}`, {
      cache: "no-store",
      credentials: "include",
      ...init,
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorBody;
      return {
        ok: false,
        message: body.error?.message ?? `The API answered ${response.status}`,
      };
    }
    // Signing out answers with no body at all.
    const value = response.status === 204 ? null : await response.json();
    return { ok: true, value: value as T };
  } catch {
    return { ok: false, message: `No answer from ${API_URL}` };
  }
}

export function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const fetchConsole = (fetcher?: typeof fetch) =>
  call<LiveConsole>("/console", undefined, fetcher);

export const startAudit = (fetcher?: typeof fetch) =>
  call<{ run_ids: string[] }>("/audit", json("POST", {}), fetcher);

export const decideProposal = (
  id: string,
  decision: "approved" | "rejected",
  fetcher?: typeof fetch,
) => call<unknown>(`/proposals/${id}/decisions`, json("POST", { decision }), fetcher);

export const saveAgentSettings = (
  playbook: string,
  settings: { autonomy_level: number; enabled: boolean },
  fetcher?: typeof fetch,
) => call<unknown>(`/agents/${playbook}/settings`, json("PUT", settings), fetcher);

export type Measure = { finding_count: number; amount_minor: number; record_count: number };
export type Simulation = { with_these_rules: Measure; with_current_rules: Measure };

export const simulateRules = (
  playbook: string,
  rules: Record<string, number>,
  fetcher?: typeof fetch,
) => call<Simulation>(`/agents/${playbook}/simulate`, json("POST", { rules }), fetcher);

export const saveRules = (
  playbook: string,
  rules: Record<string, number>,
  fetcher?: typeof fetch,
) => call<unknown>(`/agents/${playbook}/rules`, json("PUT", { rules }), fetcher);

/** "With these rules it would find **2 findings** worth **₹2.5L**…" */
export function simulationText(result: Simulation): string {
  const say = (m: Measure) =>
    `**${m.finding_count} ${m.finding_count === 1 ? "finding" : "findings"}** worth ` +
    `**${formatInr(m.amount_minor)}** over ${m.record_count.toLocaleString("en-IN")} records`;
  return (
    `With these rules it would find ${say(result.with_these_rules)} in the records on file. ` +
    `With the rules in force it finds ${say(result.with_current_rules)}.`
  );
}

/** Whole numbers only: a rule is days, paise or basis points. */
export function readRules(
  rules: LiveRule[],
  typed: Record<string, string>,
): Record<string, number> | null {
  const read: Record<string, number> = {};
  for (const rule of rules) {
    const text = (typed[rule.key] ?? String(rule.value)).trim();
    if (!/^\d{1,9}$/.test(text)) return null;
    read[rule.key] = Number(text);
  }
  return read;
}

export type UploadReport = {
  provider: string;
  source: string;
  read: number;
  rejected: number;
  added: Record<string, number>;
};

/** Sends a file's contents. With no provider, the API works out whose file it is. */
export const uploadFile = (file: File, provider?: string, fetcher?: typeof fetch) =>
  call<UploadReport>(
    `/connectors/${provider ? `${provider}/` : ""}uploads?filename=${encodeURIComponent(file.name)}`,
    { method: "POST", body: file },
    fetcher,
  );

export function uploadSummary(report: UploadReport): string {
  const added = Object.values(report.added).reduce((sum, n) => sum + n, 0);
  const read = report.read.toLocaleString("en-IN");
  const fresh = added.toLocaleString("en-IN");
  return `Read ${read} records from ${report.source} as a ${report.provider} file: ${fresh} new.`;
}

export type Connector = {
  provider: string;
  has_credentials: boolean;
  last_sync_at: string | null;
  records: Record<string, number>;
};

export const fetchConnectors = (fetcher?: typeof fetch) =>
  call<{ connectors: Connector[] }>("/connectors/health", undefined, fetcher);

export const saveCredentials = (
  provider: string,
  values: Record<string, string>,
  fetcher?: typeof fetch,
) => call<unknown>(`/connectors/${provider}/credentials`, json("PUT", { values }), fetcher);

export function claimUrl(claimId: string, extension: "pdf" | "txt"): string {
  return `${API_URL}/claims/${claimId}/document.${extension}`;
}
