"use client";

import { useConsole } from "@/components/console/ConsoleProvider";
import { Page } from "@/components/console/Shell";
import { useAgentView } from "@/components/console/useAgent";
import { Pill } from "@/components/ui";
import { approvalsTitle, statusInfo } from "@/lib/console/view";
import type { ConsoleAgent } from "@/lib/live";

function Pending({ agent }: { agent: ConsoleAgent }) {
  const view = useAgentView(agent);

  return (
    <li
      className="card flex flex-wrap items-center gap-[14px]"
      data-testid={`approval-${agent.key}`}
    >
      <div className="flex-[1_1_360px]">
        <div className="flex items-baseline gap-[10px]">
          <span className="serif text-[19px]">{agent.name}</span>
          <span className="muted num text-[13px]">{view.amount} at stake</span>
          {agent.preview && <Pill tone="neutral">Preview</Pill>}
        </div>
        <div className="mt-1 text-sm leading-[1.55] text-[#3D3C38]">{agent.proposal}</div>
      </div>
      <div className="flex gap-2">
        <button
          className="btn"
          type="button"
          aria-label={`Reject ${agent.name}`}
          onClick={view.reject}
        >
          Reject
        </button>
        <button
          className="btn btnc"
          type="button"
          aria-label={`Approve ${agent.name}`}
          onClick={view.approve}
        >
          Approve
        </button>
      </div>
    </li>
  );
}

export default function ApprovalsPage() {
  const { agents, statusOf } = useConsole();
  const pending = agents.filter((a) => statusOf(a) === "pending");
  // An agent that has not run has nothing to decide and nothing decided.
  const decided = agents.filter((a) => !["pending", "idle"].includes(statusOf(a)));

  return (
    <Page>
      <div className="flex max-w-[900px] flex-col gap-[18px]">
        <div>
          <h1 className="serif m-0 text-[38px]">{approvalsTitle(pending.length)}</h1>
          <p className="muted mt-[6px] mb-0 text-[15px]">
            Nothing that touches money or customers runs without you, until you give
            an agent more autonomy.
          </p>
        </div>
        {pending.length > 0 && (
          <ul className="m-0 flex list-none flex-col gap-[18px] p-0" aria-label="Waiting">
            {pending.map((agent) => (
              <Pending key={agent.key} agent={agent} />
            ))}
          </ul>
        )}
        {decided.length > 0 && (
          <section aria-labelledby="decided-h">
            <h2 id="decided-h" className="eyebrow m-0">
              Decided
            </h2>
            <ul className="m-0 list-none p-0">
              {decided.map((agent) => {
                const info = statusInfo(statusOf(agent));
                return (
                  <li
                    key={agent.key}
                    className="line text-base"
                    data-testid={`decided-${agent.key}`}
                  >
                    <span className="text-sm">{agent.name}</span>
                    <Pill tone={info.tone}>{info.label}</Pill>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </Page>
  );
}
