"use client";

import Link from "next/link";
import { useState } from "react";

import { Pill } from "@/components/ui";

import { useConsole } from "./ConsoleProvider";

function Step({
  done,
  title,
  detail,
  children,
}: {
  done: boolean;
  title: string;
  detail: string;
  children?: React.ReactNode;
}) {
  return (
    <li className="line !items-center">
      <span className="flex flex-auto flex-col gap-[2px]">
        <span className="font-medium">{title}</span>
        <span className="muted text-[13px]">{detail}</span>
      </span>
      {done ? <Pill tone="positive">Done</Pill> : children}
    </li>
  );
}

/** The first run: connect or upload, enter contracts, run the audit. */
export function SetupCard() {
  const { live, runAudit } = useConsole();
  const [problem, setProblem] = useState<string | null>(null);
  if (!live) return null;
  const { has_data, has_contracts } = live.setup;
  const running = live.audit.running;

  return (
    <section
      className="card w-full max-w-[620px] !px-[22px] !py-[10px] text-left"
      aria-labelledby="setup-h"
      data-testid="setup"
    >
      <h2 id="setup-h" className="eyebrow m-0 pt-3 pb-1">
        Three steps to your first audit
      </h2>
      <ol className="m-0 list-none p-0">
        <Step
          done={has_data}
          title="Connect a gateway or upload files"
          detail="Payments, settlements, a bank statement and your orders."
        >
          <Link className="btn no-underline" href="/connections">
            Connections
          </Link>
        </Step>
        <Step
          done={has_contracts}
          title="Enter your fee contracts"
          detail="The rates you agreed with each provider."
        >
          <Link className="btn no-underline" href="/contracts">
            Fee contracts
          </Link>
        </Step>
        <Step
          done={false}
          title="Run the audit"
          detail="Matches every record, checks every fee, and proposes what to claim."
        >
          <button
            className="btn btnc"
            type="button"
            disabled={!has_data || running}
            onClick={() => void runAudit().then(setProblem)}
          >
            {running ? "Audit running…" : "Run the audit"}
          </button>
        </Step>
      </ol>
      {problem && (
        <p role="alert" className="soft mt-2 mb-2 text-sm">
          {problem}
        </p>
      )}
    </section>
  );
}
