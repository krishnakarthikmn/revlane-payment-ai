import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { API_URL } from "./api";
import type { Fixtures } from "./fixtures";
import {
  type ChatEvent,
  type LiveAgent,
  type LiveConsole,
  askChat,
  chatMessage,
  currentProposal,
  liveStatus,
  mergeAgents,
  NOT_RUN,
  NOTHING_WAITING,
  parseSse,
  previewKind,
  readRules,
  simulationText,
  summaryText,
  uploadSummary,
} from "./live";

const fixtures = JSON.parse(
  readFileSync(resolve(__dirname, "../../api/revlane/fixtures/acme_travel.json"), "utf-8"),
) as Fixtures;

const proposal = (status: "pending" | "approved" | "rejected", amount: number) => ({
  id: `p-${status}-${amount}`,
  action_type: "draft_settlement_query",
  status,
  title: `Ask about ${amount}`,
  rationale: "Because it is owed.",
  amount_minor: amount,
  claim_ids: [],
  documents: [],
});

function agent(proposals: ReturnType<typeof proposal>[], lost = 31_000_000): LiveAgent {
  return {
    playbook: "reconciliation",
    autonomy_level: 1,
    enabled: true,
    lost_minor: lost,
    headline: "₹3.1L was paid but has not reached the bank, in 3 findings.",
    causes: [{ label: "Payouts short at the marketplace", pct: 39 }],
    tools: "findings_for → evidence_for",
    proposals,
    findings: [],
    meta: [{ k: "Findings", v: "3" }],
    rules: [{ key: "grace_working_days", label: "Flag unsettled after", value: 2 }],
    last_run: { status: "succeeded", error: null },
  };
}

function live(agents: Record<string, LiveAgent>): LiveConsole {
  return {
    merchant: { name: "Acme Travel" },
    sample_data: true,
    setup: { has_data: true, has_contracts: true, has_audit: true },
    audit: { running: false, last_finished_at: null },
    agents,
    notifications: [],
  };
}

describe("a real agent's status", () => {
  it("needs you while any proposal waits, and shows the first one waiting", () => {
    const waiting = agent([proposal("pending", 12), proposal("approved", 6)]);

    expect(liveStatus(waiting)).toBe("pending");
    expect(currentProposal(waiting)?.amount_minor).toBe(12);
  });

  it("then shows the last decision, or that it has not run", () => {
    expect(liveStatus(agent([proposal("approved", 6), proposal("rejected", 1)]))).toBe("approved");
    expect(liveStatus(agent([proposal("rejected", 1)]))).toBe("rejected");
    expect(liveStatus(agent([]))).toBe("idle");
  });
});

describe("mergeAgents", () => {
  it("lays live data over the two real agents and marks the rest as previews", () => {
    const merged = mergeAgents(
      fixtures.agents,
      live({ recon: agent([proposal("pending", 12_000_000)]) }),
    );
    const byKey = Object.fromEntries(merged.map((a) => [a.key, a]));

    expect(byKey.recon?.preview).toBe(false);
    expect(byKey.recon?.lost_minor).toBe(31_000_000);
    expect(byKey.recon?.proposal).toBe("Ask about 12000000");
    expect(byKey.recon?.tools).toBe("findings_for → evidence_for");
    expect(byKey.recon?.settings).toEqual([{ k: "Flag unsettled after", v: "2" }]);
    // Names, colours and families still come from the design.
    expect(byKey.recon?.name).toBe("Reconciliation");
    expect(byKey.failure?.preview).toBe(true);
    expect(byKey.failure?.lost_minor).toBe(94_000_000);
  });

  it("says so when an agent has not run or has nothing waiting", () => {
    const idle = { ...agent([], 0), headline: null };
    const [recon] = mergeAgents(
      fixtures.agents.filter((a) => a.key === "recon"),
      live({ recon: idle }),
    );

    expect(recon?.headline).toBe(NOT_RUN);
    expect(recon?.proposal).toBe(NOTHING_WAITING);
  });

  it("is all previews before the API has answered", () => {
    expect(mergeAgents(fixtures.agents, null).every((a) => a.preview)).toBe(true);
  });

  it("adds up to the prototype's headline when the synthetic merchant is audited", () => {
    const merged = mergeAgents(
      fixtures.agents,
      live({ recon: agent([], 31_000_000), fees: agent([], 26_000_000) }),
    );

    expect(summaryText(merged, 4)).toBe("₹22.6L slipped away · 4 fixes waiting");
    expect(summaryText(merged, 1)).toBe("₹22.6L slipped away · 1 fix waiting");
  });
});

describe("previewKind", () => {
  const rules = fixtures.chat.routing;

  it.each([
    ["Why did success drop on Tuesday?", "drop"],
    ["Which disputes should we contest?", "cb"],
    ["Why are subscription debits failing?", "subs"],
    ["Build a workflow for failed bookings over ₹40K", "wf"],
    ["Show my routines", "routines"],
    ["Which gateway for ICICI cards? Check routing", "routing"],
  ])("%s is a preview (%s)", (question, kind) => {
    expect(previewKind(question, [], rules)).toBe(kind);
  });

  it.each([
    "Where did we lose the most last week?",
    "Which gateway is cheapest for cards?",
    "What is our success rate by gateway last week?",
    "How much is not yet settled?",
    "Hello",
  ])("%s goes to the real backend", (question) => {
    expect(previewKind(question, [], rules)).toBeNull();
  });

  it("a question about an uploaded file is a real one", () => {
    const attach = [{ name: "hdfc.csv", kind: "CSV" as const }];

    expect(previewKind("Why did this drop?", attach, rules)).toBeNull();
  });
});

const ANSWER: ChatEvent[] = [
  { event: "step", data: { name: "understand" } },
  { event: "query", data: { query_id: "q1", spec: { metrics: ["lost_minor"] } } },
  {
    event: "table",
    data: {
      query_id: "q1",
      columns: [
        { key: "family", label: "Audit" },
        { key: "lost_minor", label: "Money lost to leaks" },
      ],
      rows: [
        {
          values: { family: "reconciliation", lost_minor: 31_000_000 },
          display: { family: "reconciliation", lost_minor: "₹3.1L" },
        },
        {
          values: { family: "fee_audit", lost_minor: 26_000_000 },
          display: { family: "fee_audit", lost_minor: "₹2.6L" },
        },
      ],
    },
  },
  { event: "chart", data: { query_id: "q1", label_key: "family", value_key: "lost_minor" } },
  {
    event: "answer",
    data: { text: "Reconciliation is highest at ₹3.1L.", next_step: { playbook: "leakage_audit" } },
  },
  { event: "sources", data: { query_ids: ["abc:q1"] } },
  { event: "done", data: {} },
];

describe("chatMessage", () => {
  it("turns an answer's events into the message the chat shows", () => {
    const message = chatMessage(ANSWER);

    expect(message.text).toBe("Reconciliation is highest at ₹3.1L.");
    expect(message.steps).toEqual(["query(lost_minor)"]);
    expect(message.rows).toEqual([
      ["Audit", "", "Money lost to leaks"],
      ["reconciliation", "", "₹3.1L"],
      ["fee_audit", "", "₹2.6L"],
    ]);
    expect(message.bars).toEqual([
      { label: "reconciliation", display: "₹3.1L", share: 100 },
      { label: "fee_audit", display: "₹2.6L", share: 84 },
    ]);
    expect(message.source).toBe("Source: abc:q1");
    expect([message.actionLabel, message.action]).toEqual(["Review fixes", "fixes"]);
    expect(message.preview).toBeUndefined();
  });

  it("shows a refusal, an error, or a tool step in its own words", () => {
    const refused = chatMessage([{ event: "refusal", data: { text: "No metric for churn." } }]);
    const failed = chatMessage([{ event: "error", data: { message: "The model is down." } }]);
    const analysed = chatMessage([
      { event: "step", data: { name: "analyse", tool: "findings_for" } },
      { event: "answer", data: { text: "Three findings.", next_step: { playbook: "fee_audit" } } },
    ]);

    expect([refused.text, refused.rows]).toEqual(["No metric for churn.", undefined]);
    expect(failed.text).toBe("The model is down.");
    expect(analysed.steps).toEqual(["findings_for"]);
    expect(analysed.action).toBe("card:fees");
    expect(chatMessage([]).text).toBe("I could not get an answer just now. Try again.");
  });

  it("leaves a single total in the sentence, with no table", () => {
    const total = chatMessage([
      {
        event: "table",
        data: {
          columns: [{ key: "lost_minor", label: "Money lost" }],
          rows: [{ values: { lost_minor: 1 }, display: { lost_minor: "₹0.01" } }],
        },
      },
      { event: "answer", data: { text: "Lost: ₹0.01.", next_step: null } },
    ]);

    expect(total.rows).toBeUndefined();
  });
});

describe("the event stream", () => {
  const wire = ANSWER.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");

  it("parses whole events and keeps what has not finished arriving", () => {
    const cut = wire.indexOf("event: table") + 20;
    const first = parseSse(wire.slice(0, cut));
    const second = parseSse(first.rest + wire.slice(cut));

    expect(first.events.map((e) => e.event)).toEqual(["step", "query"]);
    expect([...first.events, ...second.events]).toEqual(ANSWER);
    expect(second.rest).toBe("");
  });

  it("reads a streamed response and reports each event", async () => {
    const chunks = [wire.slice(0, 90), wire.slice(90)].map((part) => new TextEncoder().encode(part));
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    });
    const fetcher = vi.fn(async () => new Response(body)) as typeof fetch;
    const seen: string[] = [];

    const events = await askChat("Where did we lose the most?", (e) => seen.push(e.event), fetcher);

    expect(events).toEqual(ANSWER);
    expect(seen).toEqual(ANSWER.map((e) => e.event));
    expect(fetcher).toHaveBeenCalledWith(
      `${API_URL}/chat`,
      expect.objectContaining({ body: JSON.stringify({ question: "Where did we lose the most?" }) }),
    );
  });

  it("an unreachable API is an answer, not an exception", async () => {
    const down = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;

    expect(await askChat("x", () => undefined, down)).toEqual([
      { event: "error", data: { message: `No answer from ${API_URL}` } },
    ]);
  });
});

describe("rules and uploads", () => {
  const rules = [
    { key: "grace_working_days", label: "Flag unsettled after", value: 2 },
    { key: "bank_window_days", label: "Bank window", value: 3 },
  ];

  it("reads rules as whole numbers, keeping what was not touched", () => {
    expect(readRules(rules, { grace_working_days: " 15 " })).toEqual({
      grace_working_days: 15,
      bank_window_days: 3,
    });
    for (const bad of ["2.5", "-1", "two", ""]) {
      expect(readRules(rules, { grace_working_days: bad })).toBeNull();
    }
  });

  it("says what a simulation found beside what is found today", () => {
    const text = simulationText({
      with_these_rules: { finding_count: 2, amount_minor: 25_000_000, record_count: 43 },
      with_current_rules: { finding_count: 1, amount_minor: 31_000_000, record_count: 1065 },
    });

    expect(text).toBe(
      "With these rules it would find **2 findings** worth **₹2.5L** over 43 records in the " +
        "records on file. With the rules in force it finds **1 finding** worth **₹3.1L** over " +
        "1,065 records.",
    );
  });

  it("sums up an upload", () => {
    const report = {
      provider: "bank",
      source: "hdfc_later.csv",
      read: 1204,
      rejected: 0,
      added: { bank_credit: 2, settlement: 0 },
    };

    expect(uploadSummary(report)).toBe(
      "Read 1,204 records from hdfc_later.csv as a bank file: 2 new.",
    );
  });
});
