"use client";

import { useState } from "react";

import { Emphasis } from "@/components/ui";
import {
  claimUrl,
  readRules,
  saveRules,
  simulateRules,
  simulationText,
  type ConsoleAgent,
  type LiveAgent,
} from "@/lib/live";

import { useConsole } from "./ConsoleProvider";

const NOT_WHOLE = "Each rule is a whole number.";

/** A real agent's thresholds: try them on the records on file, then save them. */
function LiveRules({ live, idPrefix, onDone }: { live: LiveAgent; idPrefix: string; onDone: () => void }) {
  const { refresh } = useConsole();
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(save: boolean) {
    const rules = readRules(live.rules, typed);
    if (!rules) {
      setNote(NOT_WHOLE);
      return;
    }
    setBusy(true);
    if (save) {
      const result = await saveRules(live.playbook, rules);
      setNote(result.ok ? "Saved. The rules apply the next time the audit runs." : result.message);
      await refresh();
    } else {
      const result = await simulateRules(live.playbook, rules);
      setNote(result.ok ? simulationText(result.value) : result.message);
    }
    setBusy(false);
  }

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3 pt-[6px]">
        {live.rules.map((rule) => {
          const id = `${idPrefix}-${rule.key}`;
          return (
            <div key={rule.key}>
              <label className="muted text-xs" htmlFor={id}>
                {rule.label}
              </label>
              <input
                id={id}
                className="fld num mt-1"
                inputMode="numeric"
                value={typed[rule.key] ?? String(rule.value)}
                onChange={(event) => {
                  setTyped((previous) => ({ ...previous, [rule.key]: event.target.value }));
                  setNote(null);
                }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn btnp" type="button" disabled={busy} onClick={() => void run(false)}>
          Simulate on 90 days
        </button>
        <button className="btn" type="button" disabled={busy} onClick={() => void run(true)}>
          Save rules
        </button>
        <button className="btn" type="button" onClick={onDone}>
          Done
        </button>
      </div>
      {note && (
        <div className="soft text-sm" role="status" data-testid="rules-note">
          <Emphasis text={note} />
        </div>
      )}
    </>
  );
}

/** The rules editor: real for the two real agents, sample data for the rest. */
export function RulesEditor({
  agent,
  idPrefix,
  onDone,
}: {
  agent: ConsoleAgent;
  idPrefix: string;
  onDone: () => void;
}) {
  const { fixtures, state, dispatch } = useConsole();
  if (agent.live) return <LiveRules live={agent.live} idPrefix={idPrefix} onDone={onDone} />;

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3 pt-[6px]">
        {agent.settings.map((setting, i) => {
          const id = `${idPrefix}-${i}`;
          return (
            <div key={setting.k}>
              <label className="muted text-xs" htmlFor={id}>
                {setting.k}
              </label>
              <input id={id} className="fld mt-1" defaultValue={setting.v} />
            </div>
          );
        })}
      </div>
      <div className="flex gap-2">
        <button
          className="btn btnp"
          type="button"
          onClick={() => dispatch({ type: "simulateAgent" })}
        >
          Simulate on 90 days
        </button>
        <button className="btn" type="button" onClick={onDone}>
          Done
        </button>
      </div>
      {state.agentSimulated && (
        <div className="soft text-sm" role="status">
          <Emphasis text={fixtures.agent_simulation} />
        </div>
      )}
    </>
  );
}

/** Links to the claims an agent's approved proposals drafted. */
export function ClaimLinks({ claimIds }: { claimIds: string[] }) {
  if (claimIds.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm" aria-label="Drafted claims">
      {claimIds.map((id, i) => (
        <li key={id} className="flex flex-wrap items-center gap-3">
          <span className="muted">Claim {i + 1}</span>
          <a href={claimUrl(id, "pdf")} download>
            Download claim (PDF)
          </a>
          <a href={claimUrl(id, "txt")} download>
            Email text
          </a>
        </li>
      ))}
    </ul>
  );
}
