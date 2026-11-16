"use client";

import { useRoutines } from "@/components/chat/blocks";
import { useConsole } from "@/components/console/ConsoleProvider";
import { Page } from "@/components/console/Shell";
import { Pill } from "@/components/ui";

export default function WorkflowsPage() {
  const { fixtures, state, send } = useConsole();
  const routines = useRoutines();
  const draft = fixtures.workflow_draft;

  return (
    <Page>
      <div className="flex flex-col gap-[22px]">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="serif m-0 flex items-center gap-3 text-[38px]">
              Workflows
              <Pill tone="neutral">Preview</Pill>
            </h1>
            <p className="muted mt-[6px] mb-0 text-[15px]">
              Event-driven workflows and scheduled routines, in one place. Build new
              ones from chat.
            </p>
          </div>
          <button
            className="btn btnp"
            type="button"
            onClick={() => send(fixtures.chat.prompts.build_workflow, true)}
          >
            Build in chat
          </button>
        </div>

        <section className="card !px-[22px] !py-[6px]" aria-labelledby="wf-events">
          <h2 id="wf-events" className="eyebrow m-0 pt-4 pb-1">
            Triggered by events
          </h2>
          <ul className="m-0 list-none p-0">
            {fixtures.workflows.map((workflow) => (
              <li key={workflow.name} className="line !items-center !py-[14px]">
                <span className="flex flex-auto flex-col gap-[3px] text-base">
                  <span className="font-medium">{workflow.name}</span>
                  <span className="muted text-[13px]">{workflow.sub}</span>
                </span>
                <Pill tone={workflow.is_draft ? "warm" : "positive"}>
                  {workflow.is_draft && state.wfPublished
                    ? draft.published_status
                    : workflow.status}
                </Pill>
              </li>
            ))}
          </ul>
        </section>

        <section className="card !px-[22px] !py-[6px]" aria-labelledby="wf-routines">
          <h2 id="wf-routines" className="eyebrow m-0 pt-4 pb-1">
            On a schedule (routines)
          </h2>
          <ul className="m-0 list-none p-0">
            {routines.map((routine) => (
              <li key={routine.id} className="line !items-center !py-[14px]">
                <span className="flex flex-auto flex-col gap-[3px] text-base">
                  <span className="font-medium">{routine.name}</span>
                  <span className="muted text-[13px]">{routine.when}</span>
                </span>
                <Pill tone={routine.tone}>{routine.status}</Pill>
                <button
                  className="btn"
                  type="button"
                  aria-label={`${routine.toggleLabel} ${routine.name}`}
                  onClick={routine.toggle}
                >
                  {routine.toggleLabel}
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </Page>
  );
}
