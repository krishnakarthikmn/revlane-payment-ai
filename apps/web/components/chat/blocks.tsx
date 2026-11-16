"use client";

import Link from "next/link";

import { useConsole } from "@/components/console/ConsoleProvider";
import { useAgentView } from "@/components/console/useAgent";
import { ClockIcon, Emphasis, Pill, StrokeIcon } from "@/components/ui";
import { formatInr } from "@/lib/format";
import type { Fixtures } from "@/lib/fixtures";
import type { ConsoleAgent } from "@/lib/live";

/** One bar per row of a live answer, in the leak chart's style. */
export function LiveBars({
  bars,
}: {
  bars: { label: string; display: string; share: number }[];
}) {
  return (
    <figure className="card m-0 !px-[22px] !py-[14px]" data-testid="live-bars">
      <ul className="m-0 list-none p-0">
        {bars.map((bar) => (
          <li
            key={bar.label}
            className="grid grid-cols-[minmax(0,190px)_minmax(0,1fr)_72px] items-center gap-[14px] py-[7px]"
          >
            <span className="text-sm">{bar.label}</span>
            <span
              className="h-[10px] overflow-hidden rounded-full bg-[#F1EFE8]"
              aria-hidden="true"
            >
              <span
                className="block h-[10px] rounded-full bg-[#D97757]"
                style={{ width: `${bar.share}%` }}
              />
            </span>
            <span className="num text-right text-sm font-medium">{bar.display}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function LeakChart() {
  const { fixtures, agents } = useConsole();
  const total = agents.reduce((sum, a) => sum + a.lost_minor, 0);
  const largest = Math.max(...agents.map((a) => a.lost_minor));

  return (
    <figure className="card m-0 !px-[22px] !pt-[22px] !pb-[18px]">
      <figcaption className="mb-[14px] flex items-baseline justify-between gap-3">
        <span className="serif text-lg">{fixtures.leak_chart.title}</span>
        <span className="muted num text-[13px]">{formatInr(total)} total</span>
      </figcaption>
      <div
        className="mb-4 flex h-3 gap-[2px] overflow-hidden rounded-full"
        aria-hidden="true"
      >
        {agents.map((a) => (
          <span
            key={a.key}
            className="block"
            style={{
              background: a.color,
              width: `${((a.lost_minor / total) * 100).toFixed(1)}%`,
            }}
          />
        ))}
      </div>
      <ul className="m-0 list-none p-0">
        {agents.map((a) => (
          <li
            key={a.key}
            className="grid grid-cols-[minmax(0,190px)_minmax(0,1fr)_64px] items-center gap-[14px] py-[7px]"
          >
            <span className="flex items-center gap-2 text-sm">
              <span
                className="h-[9px] w-[9px] flex-none rounded-[3px]"
                style={{ background: a.color }}
              />
              {a.leak}
            </span>
            <span
              className="h-[10px] overflow-hidden rounded-full bg-[#F1EFE8]"
              aria-hidden="true"
            >
              <span
                className="block h-[10px] rounded-full"
                style={{
                  background: a.color,
                  width: `${Math.round((a.lost_minor / largest) * 100)}%`,
                }}
              />
            </span>
            <span className="num text-right text-sm font-medium">
              {formatInr(a.lost_minor)}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-[14px] flex flex-wrap gap-4 border-t border-[#F1EFE8] pt-[14px]">
        {fixtures.leak_chart.gateway_split.map((g) => (
          <span key={g.name} className="text-[13px]">
            <span className="muted">{g.name}</span>{" "}
            <span className="num font-medium">{formatInr(g.amount_minor)}</span>
          </span>
        ))}
      </div>
    </figure>
  );
}

export function HourlyChart() {
  const { hourly_chart: chart } = useConsole().fixtures;

  return (
    <figure className="card m-0 !px-[22px] !pt-[22px] !pb-4">
      <figcaption className="mb-4 flex items-baseline justify-between gap-3">
        <span className="serif text-lg">{chart.title}</span>
        <span className="muted text-[13px]">{chart.note}</span>
      </figcaption>
      <div className="flex h-[150px] items-end gap-2 border-b border-[#EAE7DE]">
        {chart.hours.map((hour) => {
          const dip = hour.success_pct < chart.dip_below_pct;
          return (
            <div
              key={hour.label}
              className="flex h-[150px] flex-[1_1_0] flex-col items-center justify-end gap-[6px]"
            >
              <span
                className="num text-[11px]"
                style={{ color: dip ? "#7A3319" : "#6B6A63" }}
              >
                {hour.success_pct.toFixed(1)}
              </span>
              <span
                className="block w-full max-w-[34px] rounded-t-[6px] rounded-b-[2px]"
                style={{
                  height: Math.round(((hour.success_pct - 78) / 12) * 120),
                  background: dip ? "#D97757" : "#DDD8CB",
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-[6px] flex gap-2">
        {chart.hours.map((hour) => (
          <span
            key={hour.label}
            className="muted num flex-[1_1_0] text-center text-[11px]"
          >
            {hour.label}
          </span>
        ))}
      </div>
    </figure>
  );
}

export function RowsTable({ rows }: { rows: string[][] }) {
  return (
    <div className="card !px-5 !py-1" role="table">
      {rows.map((row, i) => {
        const cell = i === 0 ? "columnheader" : "cell";
        return (
          <div
            key={row.join("|")}
            role="row"
            className={`line num ${i === 0 ? "!border-t-0 !text-xs !text-[#6B6A63]" : ""}`}
          >
            <span role={cell} className="flex-auto">
              {row[0]}
            </span>
            <span role={cell} className="flex-[0_0_120px] text-right">
              {row[1]}
            </span>
            <span role={cell} className="flex-[0_0_100px] text-right font-medium">
              {row[2]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Fix({ agent }: { agent: ConsoleAgent }) {
  const { runAction } = useConsole();
  const view = useAgentView(agent);

  return (
    <article
      className="card flex flex-col gap-[10px] !px-[18px] !py-4"
      aria-label={agent.name}
      data-testid={`fix-${agent.key}`}
    >
      <div className="flex items-center justify-between gap-[10px]">
        <span className="flex items-center gap-[10px]">
          <span
            className="h-[10px] w-[10px] rounded-[3px]"
            style={{ background: agent.color }}
          />
          <span className="font-medium">{agent.name}</span>
          <span className="muted num text-[13px]">{view.amount} at stake</span>
        </span>
        <Pill tone={view.info.tone}>{view.info.label}</Pill>
      </div>
      <div className="text-sm leading-[1.55] text-[#3D3C38]">{agent.proposal}</div>
      {view.isPending ? (
        <div className="flex flex-wrap gap-2">
          <button className="btn btnc" type="button" onClick={view.approve}>
            Approve
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => runAction(`card:${agent.key}`)}
          >
            Details
          </button>
          <button className="btn" type="button" onClick={view.reject}>
            Reject
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="muted flex-[1_1_260px] text-[13px]" role="status">
            {view.doneText}
          </span>
          {view.undo && (
            <button className="btn" type="button" onClick={view.undo}>
              Undo
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export function FixList({ keys }: { keys: string[] }) {
  const { agents: all, statusOf } = useConsole();
  const agents = keys
    .map((key) => all.find((a) => a.key === key))
    .filter((a): a is ConsoleAgent => a !== undefined);
  const decided = agents.filter((a) => statusOf(a) !== "pending").length;

  return (
    <div className="flex flex-col gap-[10px]">
      {agents.map((agent) => (
        <Fix key={agent.key} agent={agent} />
      ))}
      <div className="muted text-[13px]" data-testid="fix-footer">
        {decided} of {agents.length} decided · also in Approvals
      </div>
    </div>
  );
}

const WF_TONES: Record<
  Fixtures["workflow_draft"]["steps"][number]["tone"],
  { bg: string; fg: string }
> = {
  tool: { bg: "#E7E9F2", fg: "#2E3A6B" },
  agent: { bg: "#F5E2D7", fg: "#7A3319" },
  approval: { bg: "#1F1E1D", fg: "#FAF9F5" },
  base: { bg: "#EFECE4", fg: "#3D3C38" },
};

export function WorkflowDraft() {
  const { fixtures, state, dispatch } = useConsole();
  const draft = fixtures.workflow_draft;

  return (
    <article className="card !px-[22px] !py-5" aria-label={`${draft.name} workflow draft`}>
      <div className="mb-[14px] flex items-center justify-between gap-3">
        <span className="serif text-[19px]">{draft.name}</span>
        <Pill tone="warm">{state.wfPublished ? draft.published_status : draft.status}</Pill>
      </div>
      <ol className="m-0 list-none p-0">
        {draft.steps.map((step, i) => {
          const tone = WF_TONES[step.tone];
          return (
            <li key={step.title} className="flex items-stretch gap-[14px]">
              <div className="flex flex-[0_0_28px] flex-col items-center">
                <span
                  className="num flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold"
                  style={{ background: tone.bg, color: tone.fg }}
                >
                  {i + 1}
                </span>
                <span className="w-px flex-auto bg-[#E3E0D6]" />
              </div>
              <div className="flex-auto pt-[3px] pb-[14px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{step.title}</span>
                  <span className="pill" style={{ background: tone.bg, color: tone.fg }}>
                    {step.tag}
                  </span>
                </div>
                <div className="muted mono mt-[3px] text-[11.5px]">{step.sub}</div>
              </div>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap gap-2">
        <button
          className="btn"
          type="button"
          onClick={() => dispatch({ type: "simulateWf" })}
        >
          Simulate on 90 days
        </button>
        <button
          className="btn btnp"
          type="button"
          onClick={() => dispatch({ type: "publishWf" })}
        >
          Request approval
        </button>
        <Link className="btn btn-ghost ml-auto no-underline" href="/workflows">
          Open in Workflows ↗
        </Link>
      </div>
      {state.wfSimulated && (
        <div className="soft mt-3 text-sm leading-[1.55]" role="status">
          <Emphasis text={draft.simulation} />
        </div>
      )}
      {state.wfPublished && (
        <div className="soft mt-3 !bg-[#F5E2D7] text-sm text-[#5C2814]" role="status">
          {draft.published_text}
        </div>
      )}
    </article>
  );
}

export function RoutineCard() {
  const { fixtures, state, dispatch } = useConsole();
  const draft = fixtures.routine_draft;

  return (
    <article
      className="card flex flex-wrap items-center gap-[14px] !px-[18px] !py-4"
      aria-label={`${draft.name} routine`}
    >
      <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-[#F4F2EC]">
        <ClockIcon size={18} />
      </span>
      <span className="flex flex-[1_1_240px] flex-col gap-[2px]">
        <span className="font-medium">{draft.name}</span>
        <span className="muted text-[13px]">{draft.when}</span>
      </span>
      {state.routineSaved ? (
        <span className="pill pill-positive" role="status">
          {draft.saved_label}
        </span>
      ) : (
        <button
          className="btn btnp"
          type="button"
          onClick={() => dispatch({ type: "saveRoutine" })}
        >
          Save routine
        </button>
      )}
    </article>
  );
}

/** The routines a person has, with their live status. Shared with Workflows. */
export function useRoutines() {
  const { fixtures, state, dispatch } = useConsole();
  return fixtures.routines
    .filter((r) => !r.requires_saved_draft || state.routineSaved)
    .map((r) => {
      const paused = Boolean(state.paused[r.id]);
      const ran = Boolean(state.ran[r.id]);
      return {
        id: r.id,
        name: r.name,
        when: r.when,
        status: ran ? "Ran just now" : paused ? "Paused" : "Active",
        tone: paused ? ("neutral" as const) : ("positive" as const),
        toggleLabel: paused ? "Resume" : "Pause",
        toggle: () => dispatch({ type: "togglePaused", id: r.id }),
        run: () => dispatch({ type: "runRoutine", id: r.id }),
      };
    });
}

export function RoutineList() {
  const routines = useRoutines();

  return (
    <ul className="card m-0 list-none !px-5 !py-[6px]" aria-label="Routines">
      {routines.map((routine, i) => (
        <li
          key={routine.id}
          className={`line !items-center !py-[14px] ${i === 0 ? "!border-t-0" : ""}`}
        >
          <span className="flex flex-auto flex-col gap-[3px] text-base">
            <span className="font-medium">{routine.name}</span>
            <span className="muted text-[13px]">{routine.when}</span>
          </span>
          <Pill tone={routine.tone}>{routine.status}</Pill>
          <button
            className="btn"
            type="button"
            aria-label={`Run ${routine.name} now`}
            onClick={routine.run}
          >
            Run now
          </button>
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
  );
}

export function SkillDraft({ messageId }: { messageId: number }) {
  const { fixtures, state, dispatch } = useConsole();
  const draft = fixtures.skill_draft;
  const nameId = `skname-${messageId}`;
  const rulesId = `skrules-${messageId}`;

  return (
    <article className="card flex flex-col gap-3" aria-label="New skill draft">
      <div className="flex items-center gap-[10px]">
        <span className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] bg-[#1F1E1D]">
          <StrokeIcon
            d="M5 4 H15 L19 8 V20 H5 Z M9 12 H15 M9 16 H13"
            color="#FAF9F5"
            width={1.7}
          />
        </span>
        <span className="eyebrow">New skill · draft</span>
      </div>
      <div>
        <label className="muted text-xs" htmlFor={nameId}>
          Name
        </label>
        <input id={nameId} className="fld mt-1" defaultValue={draft.name} />
      </div>
      <div>
        <label className="muted text-xs" htmlFor={rulesId}>
          Instructions
        </label>
        <textarea
          id={rulesId}
          className="fld mt-1 min-h-24 resize-y leading-[1.55]"
          defaultValue={draft.instructions}
        />
      </div>
      <div className="flex flex-wrap items-center gap-[6px]">
        <span className="muted text-xs">Applies to</span>
        {draft.applies_to.map((target) => (
          <span key={target} className="att !pr-[10px]">
            {target}
          </span>
        ))}
      </div>
      {state.skillSaved ? (
        <div className="soft text-sm" role="status">
          {draft.saved_text}
        </div>
      ) : (
        <div>
          <button
            className="btn btnp"
            type="button"
            onClick={() => dispatch({ type: "saveSkill" })}
          >
            Save skill
          </button>
        </div>
      )}
    </article>
  );
}
