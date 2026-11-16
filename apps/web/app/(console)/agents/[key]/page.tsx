"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { useConsole } from "@/components/console/ConsoleProvider";
import { ClaimLinks, RulesEditor } from "@/components/console/RulesEditor";
import { Page } from "@/components/console/Shell";
import { useAgentView } from "@/components/console/useAgent";
import { Pill } from "@/components/ui";
import { formatInr } from "@/lib/format";
import { saveAgentSettings, type ConsoleAgent } from "@/lib/live";

// The first version implements Observe and Suggest; the rest are shown, not offered.
const BUILT_LEVELS = 2;
const NOT_BUILT = "Not available yet: agents earn this level.";
const STATE_WORDS: Record<string, string> = {
  recoverable: "Recoverable",
  claimed: "Claimed",
  recovered: "Recovered",
  written_off: "Written off",
  lost: "Lost",
};

function AgentDetail({ agent }: { agent: ConsoleAgent }) {
  const { fixtures, state, dispatch, runAction, runAudit, refresh, live } = useConsole();
  const view = useAgentView(agent);
  const [editing, setEditing] = useState(false);
  const level = agent.live ? agent.live.autonomy_level : (state.autonomy[agent.key] ?? 0);
  const { levels, earn_note } = fixtures.autonomy;
  const auditing = live?.audit.running ?? false;

  async function setLevel(next: number) {
    if (!agent.live) {
      dispatch({ type: "setAutonomy", key: agent.key, level: next });
      return;
    }
    await saveAgentSettings(agent.live.playbook, {
      autonomy_level: next,
      enabled: agent.live.enabled,
    });
    await refresh();
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap gap-2">
        <Link className="btn no-underline" href="/agents">
          ← Agents
        </Link>
        <button
          className="btn"
          type="button"
          onClick={() => runAction(`card:${agent.key}`, true)}
        >
          Discuss in chat
        </button>
        {agent.live && (
          <button
            className="btn"
            type="button"
            disabled={auditing}
            onClick={() => void runAudit()}
          >
            {auditing ? "Audit running…" : "Run the audit"}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-end justify-between gap-6">
        <div className="flex-[1_1_480px]">
          <div className="eyebrow flex items-center gap-2">
            {agent.family} agent
            {agent.preview && <Pill tone="neutral">Preview</Pill>}
          </div>
          <h1 className="serif mt-2 mb-0 text-[38px]">{agent.name}</h1>
          <p className="serif mt-3 mb-0 text-xl leading-normal text-[#3D3C38]">
            {agent.headline}
          </p>
        </div>
        <div className="text-right">
          <div className="eyebrow">Lost last 7 days</div>
          <div className="serif num text-5xl leading-none">{view.amount}</div>
          <div className="mt-2">
            <Pill tone={view.info.tone}>{view.info.label}</Pill>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-[14px]">
        <section className="card" aria-labelledby="diag-h">
          <h2 id="diag-h" className="serif mt-0 mb-[6px] text-[21px]">
            Why it happened
          </h2>
          {agent.causes.map((cause) => (
            <div key={cause.label} className="border-t border-[#F1EFE8] py-3">
              <div className="flex justify-between gap-3 text-sm">
                <span>{cause.label}</span>
                <span className="num font-medium">{cause.pct}%</span>
              </div>
              <div className="track mt-2" aria-hidden="true">
                <span
                  className="block h-[6px] rounded-full bg-[#D97757]"
                  style={{ width: `${cause.pct}%` }}
                />
              </div>
            </div>
          ))}
          <div className="soft mono mt-3 text-[11.5px] text-[#3D3C38]">{agent.tools}</div>
        </section>

        <section className="card flex flex-col gap-[14px]" aria-labelledby="prop-h">
          <div className="eyebrow">Proposed fix</div>
          <h2 id="prop-h" className="serif m-0 text-xl leading-normal font-normal">
            {agent.proposal}
          </h2>
          <div>
            {agent.meta.map((fact) => (
              <div key={fact.k} className="line">
                <span className="muted">{fact.k}</span>
                <span className="num">{fact.v}</span>
              </div>
            ))}
          </div>
          {view.isPending ? (
            <div className="flex flex-wrap gap-2">
              <button className="btn btnc" type="button" onClick={view.approve}>
                Approve
              </button>
              <button className="btn" type="button" onClick={view.reject}>
                Reject
              </button>
            </div>
          ) : (
            <>
              <div className="soft text-sm leading-normal" role="status">
                {view.doneText}
              </div>
              {view.undo && (
                <div>
                  <button className="btn" type="button" onClick={view.undo}>
                    Undo
                  </button>
                </div>
              )}
            </>
          )}
          <ClaimLinks claimIds={view.claimIds} />
        </section>
      </div>

      {agent.live && agent.live.findings.length > 0 && (
        <section className="card !px-[22px] !py-[6px]" aria-labelledby="findings-h">
          <h2 id="findings-h" className="eyebrow m-0 pt-4 pb-1">
            What it found
          </h2>
          <ul className="m-0 list-none p-0">
            {agent.live.findings.map((finding) => (
              <li key={finding.id} className="line !items-center" data-testid="finding">
                <span className="flex-auto text-sm">{finding.label}</span>
                <span className="num text-sm font-medium">
                  {formatInr(finding.amount_minor)}
                </span>
                <Pill tone={finding.state === "recovered" ? "positive" : "neutral"}>
                  {STATE_WORDS[finding.state] ?? finding.state}
                </Pill>
              </li>
            ))}
          </ul>
        </section>
      )}

      {agent.live && (
        <section className="card flex flex-col gap-3" aria-labelledby="rules-h">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="rules-h" className="serif m-0 text-[21px]">
              Rules
            </h2>
            {!editing && (
              <button className="btn" type="button" onClick={() => setEditing(true)}>
                Edit rules
              </button>
            )}
          </div>
          {editing ? (
            <RulesEditor
              agent={agent}
              idPrefix={`rules-${agent.key}`}
              onDone={() => setEditing(false)}
            />
          ) : (
            <div>
              {agent.live.rules.map((rule) => (
                <div key={rule.key} className="line">
                  <span className="muted">{rule.label}</span>
                  <span className="num">{rule.value.toLocaleString("en-IN")}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="card" aria-labelledby="auto-h">
        <div className="flex flex-wrap items-center justify-between gap-[14px]">
          <div className="flex-[1_1_340px]">
            <h2 id="auto-h" className="serif m-0 text-[21px]">
              How much it can do on its own
            </h2>
            <p className="muted mt-1 mb-0 text-[13px]">{earn_note}</p>
          </div>
          <div
            role="group"
            aria-label="Autonomy level"
            className="flex gap-1 rounded-xl bg-[#F4F2EC] p-1"
          >
            {levels.map((item, i) => {
              const built = i < BUILT_LEVELS;
              return (
                <button
                  key={item.label}
                  className={`btn !border-transparent ${
                    i === level
                      ? "!bg-white shadow-[0_1px_2px_rgba(31,30,29,.12)]"
                      : "!bg-transparent"
                  } ${built ? "" : "cursor-not-allowed opacity-50"}`}
                  type="button"
                  aria-pressed={i === level}
                  disabled={!built}
                  title={built ? undefined : NOT_BUILT}
                  onClick={() => void setLevel(i)}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
        <p className="mt-3 mb-0 text-sm text-[#3D3C38]" data-testid="level-note">
          {levels[level]?.note}
        </p>
      </section>
    </div>
  );
}

export default function AgentPage() {
  const { agents } = useConsole();
  const { key } = useParams<{ key: string }>();
  const agent = agents.find((a) => a.key === key);

  return (
    <Page>
      {agent ? (
        <AgentDetail agent={agent} />
      ) : (
        <div className="flex flex-col items-start gap-4">
          <h1 className="serif m-0 text-[38px]">No such agent</h1>
          <Link className="btn no-underline" href="/agents">
            ← Agents
          </Link>
        </div>
      )}
    </Page>
  );
}
