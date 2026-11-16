import type { Attachment, Fixtures, RoutingRule } from "../fixtures";

/** The prototype's keyword routing: the first rule that matches wins. */
export function answerFor(
  text: string,
  attach: Attachment[],
  rules: RoutingRule[],
): string {
  const t = text.toLowerCase();
  for (const rule of rules) {
    if (rule.attachment_kind) {
      if (attach.some((a) => a.kind === rule.attachment_kind)) return rule.kind;
      continue;
    }
    if (rule.any?.some((word) => t.includes(word))) return rule.kind;
  }
  return "none";
}

export function thinkingFor(
  kind: string,
  thinking: Fixtures["chat"]["thinking"],
): string {
  if (kind === "file") return thinking.file;
  if (kind === "routine" || kind === "routines") return thinking.routines;
  if (kind === "skill") return thinking.skill;
  return thinking.default;
}
