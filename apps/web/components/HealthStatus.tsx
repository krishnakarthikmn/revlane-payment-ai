"use client";

import { useEffect, useState } from "react";

import { fetchHealth, type HealthResult } from "@/lib/api";

const PILL = "rounded-full px-2.5 py-0.5 text-xs font-medium";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between border-t border-divider py-2.5">
      <dt className="text-muted">{label}</dt>
      <dd className="font-mono text-[13px] tabular-nums">{value}</dd>
    </div>
  );
}

export function HealthStatus() {
  const [result, setResult] = useState<HealthResult | null>(null);

  useEffect(() => {
    let current = true;
    void fetchHealth().then((next) => {
      if (current) setResult(next);
    });
    return () => {
      current = false;
    };
  }, []);

  return (
    <section
      aria-labelledby="health-title"
      aria-live="polite"
      className="rounded-2xl border border-line bg-card p-5 shadow-card"
    >
      <div className="flex items-center justify-between pb-3">
        <h2 id="health-title" className="eyebrow">
          API health
        </h2>
        {result === null && (
          <span className={`${PILL} bg-neutral text-neutral-ink`}>Checking</span>
        )}
        {result?.state === "up" && result.health.status === "ok" && (
          <span
            data-testid="health-pill"
            className={`${PILL} bg-positive text-positive-ink`}
          >
            Running
          </span>
        )}
        {result?.state === "up" && result.health.status === "degraded" && (
          <span
            data-testid="health-pill"
            className={`${PILL} bg-warm text-warm-ink`}
          >
            Degraded
          </span>
        )}
        {result?.state === "down" && (
          <span
            data-testid="health-pill"
            className={`${PILL} bg-warm text-warm-ink`}
          >
            Unreachable
          </span>
        )}
      </div>

      {result?.state === "up" && (
        <dl className="text-sm">
          <Row label="Service" value={result.health.service} />
          <Row label="Version" value={result.health.version} />
          <Row label="Database" value={result.health.database} />
          <Row label="Model" value={result.health.llm_mode} />
        </dl>
      )}
      {result?.state === "down" && (
        <p className="border-t border-divider pt-3 text-sm text-secondary">
          {result.message}. Start it with <code className="font-mono">make dev</code>.
        </p>
      )}
    </section>
  );
}
