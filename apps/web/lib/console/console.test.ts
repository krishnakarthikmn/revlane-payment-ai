import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { Fixtures } from "../fixtures";
import { answerFor, thinkingFor } from "./routing";
import { actionMessage, initialState, reducer, replyToMessage } from "./state";

const fixtures = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../api/revlane/fixtures/acme_travel.json"),
    "utf-8",
  ),
) as Fixtures;
const rules = fixtures.chat.routing;

describe("answerFor (the prototype's keyword routing)", () => {
  it.each([
    ["Where did we lose the most last week?", "lost"],
    ["Why did success drop on Tuesday?", "drop"],
    ["Which gateway is cheapest for cards?", "fee"],
    ["Which disputes should we contest?", "cb"],
    ["Why are subscription debits failing?", "subs"],
    ["Build a workflow for failed bookings over ₹40K", "wf"],
    ["Create a skill for our month-end close", "skill"],
    ["Show my routines", "routines"],
    ["Every Monday at 9 am, send last week's leakage to #finance", "routine"],
    ["Reconcile this statement", "file"],
    ["Which gateway for ICICI cards? Check routing", "routing"],
    ["Hello", "none"],
  ])("%s → %s", (question, kind) => {
    expect(answerFor(question, [], rules)).toBe(kind);
  });

  it("every suggestion chip and recent question has a real reply", () => {
    for (const { question } of [...fixtures.chat.quick, ...fixtures.chat.recents]) {
      expect(answerFor(question, [], rules)).not.toBe("none");
    }
  });

  it("an attached CSV wins over keywords further down the list", () => {
    const csv = [fixtures.chat.attachments.file];

    expect(answerFor("why did this fail?", csv, rules)).toBe("file");
    expect(answerFor("create a skill from this", csv, rules)).toBe("skill");
  });

  it("picks the thinking line for the kind of answer", () => {
    const t = fixtures.chat.thinking;

    expect(thinkingFor("file", t)).toBe(t.file);
    expect(thinkingFor("routine", t)).toBe(t.routines);
    expect(thinkingFor("skill", t)).toBe(t.skill);
    expect(thinkingFor("lost", t)).toBe(t.default);
  });
});

describe("console state", () => {
  const start = initialState(fixtures);

  it("starts with four fixes pending and two running", () => {
    const statuses = Object.values(start.status);

    expect(statuses.filter((s) => s === "pending")).toHaveLength(4);
    expect(statuses.filter((s) => s === "approved")).toHaveLength(2);
    expect(start.autonomy).toEqual({
      failure: 1,
      routing: 0,
      recon: 1,
      fees: 1,
      subs: 2,
      chargeback: 1,
    });
  });

  it("sending the first message folds the sidebar and starts thinking", () => {
    const withFile = reducer(start, {
      type: "addAttach",
      item: fixtures.chat.attachments.file,
    });
    const withSkill = reducer(withFile, {
      type: "addAttach",
      item: { name: "EMI rules", kind: "SKILL" },
    });
    const sent = reducer(withSkill, {
      type: "userSent",
      text: "Reconcile it",
      thinkingText: "…",
    });

    expect(sent.pinned).toBe(false);
    expect(sent.thinking).toBe(true);
    expect(sent.attach).toEqual([]);
    expect(sent.messages).toEqual([
      {
        id: 1,
        role: "user",
        text: "Reconcile it",
        files: [fixtures.chat.attachments.file],
      },
    ]);
  });

  it("a later message leaves the sidebar as it was", () => {
    let state = reducer(start, { type: "userSent", text: "a", thinkingText: "" });
    state = reducer(state, { type: "botReplied", message: { text: "b", steps: [] } });
    state = reducer(state, { type: "togglePin" });
    state = reducer(state, { type: "userSent", text: "c", thinkingText: "" });

    expect(state.pinned).toBe(true);
    expect(state.messages.map((m) => m.id)).toEqual([1, 2, 3]);
  });

  it("a fresh send replaces the conversation", () => {
    let state = reducer(start, { type: "userSent", text: "a", thinkingText: "" });
    state = reducer(state, { type: "userSent", text: "b", thinkingText: "", fresh: true });

    expect(state.messages.map((m) => m.text)).toEqual(["b"]);
  });

  it("approve, reject and undo move one agent's status", () => {
    const approved = reducer(start, { type: "setStatus", key: "recon", status: "approved" });
    const undone = reducer(approved, { type: "setStatus", key: "recon", status: "pending" });

    expect(approved.status.recon).toBe("approved");
    expect(approved.status.fees).toBe("pending");
    expect(undone.status).toEqual(start.status);
  });

  it("prefixes the tool steps with the skill in use", () => {
    const message = replyToMessage(fixtures.chat.replies.fee, "EMI rules");

    expect(message.steps[0]).toBe("skill: EMI rules");
    expect(message.action).toBe("card:fees");
  });

  it("the fixes action lists only what is still pending", () => {
    const decided = reducer(start, { type: "setStatus", key: "fees", status: "rejected" });

    expect(actionMessage("fixes", decided, fixtures)?.fixes).toEqual([
      "failure",
      "routing",
      "recon",
    ]);
  });

  it("the fixes action says so when nothing is waiting", () => {
    const state = fixtures.agents.reduce(
      (s, a) => reducer(s, { type: "setStatus", key: a.key, status: "approved" }),
      start,
    );
    const message = actionMessage("fixes", state, fixtures);

    expect(message?.fixes).toEqual([]);
    expect(message?.text).toBe(fixtures.chat.actions.fixes.empty_text);
  });

  it("a card action opens that agent's proposal", () => {
    expect(actionMessage("card:recon", start, fixtures)).toMatchObject({
      agentKey: "recon",
      steps: ["get_proposal(recon)"],
    });
  });

  it("opening the rules editor clears the last simulation", () => {
    let state = reducer(start, { type: "toggleEdit", key: "fees" });
    state = reducer(state, { type: "simulateAgent" });
    state = reducer(state, { type: "toggleEdit", key: "fees" });

    expect(state.editKey).toBe("");
    expect(state.agentSimulated).toBe(false);
  });

  it("hover only matters while the sidebar is collapsed", () => {
    expect(reducer(start, { type: "setHover", hover: true }).hover).toBe(false);

    const collapsed = reducer(start, { type: "togglePin" });
    expect(reducer(collapsed, { type: "setHover", hover: true }).hover).toBe(true);
  });
});
