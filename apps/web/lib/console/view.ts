import type { Agent, AttachmentKind, Status } from "../fixtures";

export type Tone = "positive" | "neutral" | "warm" | "tool" | "dark";

export function statusInfo(status: Status): { label: string; tone: Tone } {
  if (status === "approved") return { label: "Running", tone: "positive" };
  if (status === "rejected") return { label: "Rejected", tone: "neutral" };
  if (status === "idle") return { label: "Not run yet", tone: "neutral" };
  return { label: "Needs you", tone: "warm" };
}

export const attachmentTone: Record<AttachmentKind, Tone> = {
  CSV: "positive",
  LINK: "tool",
  SKILL: "dark",
};

export function pendingAgents(agents: Agent[], status: Record<string, Status>): Agent[] {
  return agents.filter((a) => status[a.key] === "pending");
}

export function approvalsTitle(pending: number): string {
  if (pending === 0) return "You're all caught up";
  return pending === 1
    ? "1 fix is waiting for you"
    : `${pending} fixes are waiting for you`;
}
