import { API_URL } from "./api";

// "idle" is a real agent that has not run yet: there is nothing to decide.
export type Status = "pending" | "approved" | "rejected" | "idle";
export type AttachmentKind = "CSV" | "LINK" | "SKILL";
export type Attachment = { name: string; kind: AttachmentKind };
export type Pair = { k: string; v: string };

export type Agent = {
  key: string;
  name: string;
  short: string;
  leak: string;
  family: string;
  lost_minor: number;
  color: string;
  initial_status: Status;
  initial_autonomy: number;
  headline: string;
  causes: { label: string; pct: number }[];
  proposal: string;
  meta: Pair[];
  tools: string;
  done: string;
  settings: Pair[];
};

export type ReplyBlock = "chart" | "hourly" | "workflow" | "routine" | "routines" | "skill";

export type Reply = {
  steps: string[];
  text: string;
  block?: ReplyBlock;
  rows?: string[][];
  source?: string;
  action_label?: string;
  action?: string;
  agent_key?: string;
};

export type RoutingRule = {
  kind: string;
  any?: string[];
  attachment_kind?: AttachmentKind;
};

export type Prompt = { label: string; question: string };

export type Fixtures = {
  version: number;
  merchant: {
    name: string;
    user: { name: string; first_name: string; initial: string; team: string };
    second_approver: { name: string; team: string };
  };
  summary: { pill: string; text: string; question: string };
  agents: Agent[];
  rejected_text: string;
  agent_simulation: string;
  autonomy: { earn_note: string; levels: { label: string; note: string }[] };
  leak_chart: {
    title: string;
    gateway_split: { name: string; amount_minor: number }[];
  };
  hourly_chart: {
    title: string;
    note: string;
    dip_below_pct: number;
    hours: { label: string; success_pct: number }[];
  };
  chat: {
    placeholder: string;
    placeholder_with_file: string;
    quick: Prompt[];
    recents: Prompt[];
    attachments: { file: Attachment; connection: Attachment };
    prompts: {
      create_skill: string;
      start_routine: string;
      manage_routines: string;
      build_workflow: string;
    };
    thinking: { default: string; file: string; routines: string; skill: string };
    routing: RoutingRule[];
    replies: Record<string, Reply>;
    actions: {
      fixes: { step: string; text: string; empty_text: string };
      card: { text: string };
    };
  };
  notifications: {
    title: string;
    body: string;
    time: string;
    dot: string;
    question: string;
  }[];
  skills: string[];
  skill_draft: {
    name: string;
    instructions: string;
    applies_to: string[];
    saved_text: string;
  };
  routine_draft: { name: string; when: string; saved_label: string };
  routines: {
    id: string;
    name: string;
    when: string;
    requires_saved_draft: boolean;
  }[];
  workflow_draft: {
    name: string;
    status: string;
    published_status: string;
    steps: {
      title: string;
      sub: string;
      tag: string;
      tone: "base" | "tool" | "approval" | "agent";
    }[];
    simulation: string;
    published_text: string;
  };
  workflows: { name: string; sub: string; status: string; is_draft: boolean }[];
  connections: {
    name: string;
    kind: string;
    status: string;
    detail: string;
    on: boolean;
  }[];
  custom_mcp: {
    default_url: string;
    found: string;
    tools: { name: string; permission: string; on: boolean }[];
  };
};

export type FixturesResult =
  | { state: "ready"; fixtures: Fixtures }
  | { state: "down"; message: string };

/** Loads the sample data. Never throws: an unreachable API is a result. */
export async function fetchFixtures(
  fetcher: typeof fetch = fetch,
): Promise<FixturesResult> {
  try {
    const response = await fetcher(`${API_URL}/fixtures`, {
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) {
      return { state: "down", message: `The API answered ${response.status}` };
    }
    return { state: "ready", fixtures: (await response.json()) as Fixtures };
  } catch {
    return { state: "down", message: `No answer from ${API_URL}` };
  }
}
