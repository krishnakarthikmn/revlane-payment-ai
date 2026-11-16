"use client";

import Link from "next/link";

import { useConsole } from "@/components/console/ConsoleProvider";
import { Page } from "@/components/console/Shell";
import { useAgentView } from "@/components/console/useAgent";
import { Pill } from "@/components/ui";
import type { ConsoleAgent } from "@/lib/live";

function AgentTile({ agent }: { agent: ConsoleAgent }) {
  const view = useAgentView(agent);

  return (
    <Link
      href={`/agents/${agent.key}`}
      className="card flex flex-col gap-3 text-left !text-inherit no-underline"
      data-testid={`agent-tile-${agent.key}`}
    >
      <div className="flex w-full items-center justify-between">
        <span className="flex items-center gap-2">
          <span
            className="h-[10px] w-[10px] rounded-[3px]"
            style={{ background: agent.color }}
          />
          <span className="eyebrow">{agent.family}</span>
        </span>
        <span className="flex items-center gap-[6px]">
          {agent.preview && <Pill tone="neutral">Preview</Pill>}
          <Pill tone={view.info.tone}>{view.info.label}</Pill>
        </span>
      </div>
      <div className="serif text-[22px]">{agent.name}</div>
      <div className="text-sm leading-[1.55] text-[#3D3C38]">{agent.headline}</div>
      <div className="mt-auto flex w-full items-baseline justify-between border-t border-[#F1EFE8] pt-[10px]">
        <span className="muted text-[13px]">Found last 7 days</span>
        <span className="serif num text-2xl">{view.amount}</span>
      </div>
    </Link>
  );
}

export default function AgentsPage() {
  const { agents } = useConsole();

  return (
    <Page>
      <div className="flex flex-col gap-[22px]">
        <div>
          <h1 className="serif m-0 text-[38px]">Agents</h1>
          <p className="muted mt-[6px] mb-0 text-[15px]">
            Six agents, each guarding one way money slips away.
          </p>
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-[14px]">
          {agents.map((agent) => (
            <AgentTile key={agent.key} agent={agent} />
          ))}
        </div>
      </div>
    </Page>
  );
}
