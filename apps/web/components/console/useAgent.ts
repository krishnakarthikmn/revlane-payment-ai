"use client";

import { formatInr } from "@/lib/format";
import { statusInfo } from "@/lib/console/view";
import { NOTHING_SENT, type ConsoleAgent } from "@/lib/live";

import { useConsole } from "./ConsoleProvider";

/** One agent with its status and the decisions a person can take on it. */
export function useAgentView(agent: ConsoleAgent) {
  const { fixtures, dispatch, statusOf, decide } = useConsole();
  const status = statusOf(agent);
  const approved = agent.live?.proposals.filter((p) => p.status === "approved") ?? [];
  const documents = approved.reduce((sum, p) => sum + p.documents.length, 0);
  const liveDone =
    status === "approved"
      ? `Approved. ${documents} ${documents === 1 ? "document" : "documents"} drafted. ${NOTHING_SENT}`
      : status === "rejected"
        ? fixtures.rejected_text
        : "It makes its proposals when the audit runs.";

  return {
    status,
    info: statusInfo(status),
    amount: formatInr(agent.lost_minor),
    isPending: status === "pending",
    doneText: agent.live
      ? liveDone
      : status === "approved"
        ? agent.done
        : fixtures.rejected_text,
    approve: () => void decide(agent, "approved"),
    reject: () => void decide(agent, "rejected"),
    // A real approval has drafted documents and moved findings on: it is not undone here.
    undo: agent.live
      ? undefined
      : () => dispatch({ type: "setStatus", key: agent.key, status: "pending" }),
    /** Claims drafted by this agent's approved proposals. */
    claimIds: approved.flatMap((p) => p.claim_ids),
  };
}
