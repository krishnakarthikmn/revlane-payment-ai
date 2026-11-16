"use client";

import Link from "next/link";

import { useConsole } from "@/components/console/ConsoleProvider";
import { useAgentView } from "@/components/console/useAgent";
import { ClaimLinks, RulesEditor } from "@/components/console/RulesEditor";
import { Pill } from "@/components/ui";
import type { ConsoleAgent } from "@/lib/live";

export function AgentCard({
  agent,
  messageId,
}: {
  agent: ConsoleAgent;
  messageId: number;
}) {
  const { state, dispatch } = useConsole();
  const view = useAgentView(agent);
  const editing = state.editKey === agent.key;
  const toggleEdit = () => dispatch({ type: "toggleEdit", key: agent.key });

  return (
    <article
      className="card !p-0 overflow-hidden"
      aria-label={`${agent.name} proposal`}
      data-testid={`agent-card-${agent.key}`}
    >
      <div className="flex items-start justify-between gap-4 border-b border-[#F1EFE8] px-[22px] py-5">
        <div>
          <div className="eyebrow">{agent.family} agent</div>
          <div className="serif mt-1 flex items-center gap-2 text-[22px]">
            {agent.name}
            {agent.preview && <Pill tone="neutral">Preview</Pill>}
          </div>
        </div>
        <div className="text-right">
          <div className="serif num text-[28px] leading-none">{view.amount}</div>
          <Pill tone={view.info.tone} className="mt-[6px]">
            {view.info.label}
          </Pill>
        </div>
      </div>

      <div className="px-[22px] py-4">
        <div className="eyebrow mb-1">Why it happened</div>
        {agent.causes.map((cause) => (
          <div
            key={cause.label}
            className="grid grid-cols-[minmax(0,1fr)_120px_40px] items-center gap-3 py-[6px] text-[13.5px]"
          >
            <span>{cause.label}</span>
            <span className="track" aria-hidden="true">
              <span
                className="block h-[6px] rounded-full bg-[#D97757]"
                style={{ width: `${cause.pct}%` }}
              />
            </span>
            <span className="num text-right">{cause.pct}%</span>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-3 border-t border-[#F1EFE8] bg-[#FAF9F5] px-[22px] py-4">
        <div className="eyebrow">Proposed fix</div>
        <div className="serif text-[17.5px] leading-[1.55]">{agent.proposal}</div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-[10px]">
          {agent.meta.map((fact) => (
            <div
              key={fact.k}
              className="rounded-xl border border-[#EAE7DE] bg-white px-3 py-[10px]"
            >
              <div className="muted text-xs">{fact.k}</div>
              <div className="num mt-[2px] text-sm font-medium">{fact.v}</div>
            </div>
          ))}
        </div>

        {view.isPending ? (
          <div className="flex flex-wrap gap-2">
            <button className="btn btnc" type="button" onClick={view.approve}>
              Approve
            </button>
            <button
              className="btn"
              type="button"
              aria-expanded={editing}
              onClick={toggleEdit}
            >
              Edit rules
            </button>
            <button className="btn" type="button" onClick={view.reject}>
              Reject
            </button>
            <Link
              className="btn btn-ghost ml-auto no-underline"
              href={`/agents/${agent.key}`}
            >
              Open agent ↗
            </Link>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex-[1_1_280px] text-sm leading-normal" role="status">
              {view.doneText}
            </span>
            {view.undo && (
              <button className="btn" type="button" onClick={view.undo}>
                Undo
              </button>
            )}
            {agent.live && (
              <button
                className="btn"
                type="button"
                aria-expanded={editing}
                onClick={toggleEdit}
              >
                Edit rules
              </button>
            )}
          </div>
        )}

        <ClaimLinks claimIds={view.claimIds} />

        {editing && (
          <RulesEditor
            agent={agent}
            idPrefix={`set-${messageId}-${agent.key}`}
            onDone={toggleEdit}
          />
        )}
      </div>
    </article>
  );
}
