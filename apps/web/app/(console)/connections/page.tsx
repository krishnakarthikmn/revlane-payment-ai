"use client";

import { useCallback, useEffect, useState } from "react";

import { useConsole } from "@/components/console/ConsoleProvider";
import { Page } from "@/components/console/Shell";
import { Pill } from "@/components/ui";
import {
  fetchConnectors,
  saveCredentials,
  uploadFile,
  uploadSummary,
  type Connector,
} from "@/lib/live";

// The real connections: what Revlane reads, and how each is fed in the first version.
const REAL = [
  { provider: "razorpay", name: "Razorpay", kind: "Gateway", keys: ["key_id", "key_secret"] },
  { provider: "payu", name: "PayU", kind: "Gateway", keys: ["key", "salt"] },
  { provider: "cashfree", name: "Cashfree", kind: "Gateway", keys: [] },
  { provider: "marketplace", name: "Marketplace", kind: "Payouts", keys: [] },
  { provider: "bank", name: "Bank account", kind: "Bank", keys: [] },
  { provider: "orders", name: "Order export", kind: "Orders", keys: [] },
] as const;
const REAL_NAMES = new Set(["Razorpay", "PayU", "Cashfree", "HDFC Bank", "Shopify"]);
const WORDS: Record<string, string> = {
  attempts: "payments",
  settlements: "settlements",
  refunds: "refunds",
  bank_credits: "bank credits",
  orders: "orders",
};

function detailFor(connector: Connector | undefined): string {
  if (!connector) return "Nothing received yet. Upload a file to start.";
  const counts = Object.entries(connector.records)
    .filter(([name, count]) => WORDS[name] && count > 0)
    .map(([name, count]) => `${count.toLocaleString("en-IN")} ${WORDS[name]}`);
  return counts.length ? counts.join(" · ") : "Connected, with no records yet.";
}

function RealCard({
  spec,
  connector,
  onChanged,
}: {
  spec: (typeof REAL)[number];
  connector: Connector | undefined;
  onChanged: () => Promise<void>;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const has = connector !== undefined && Object.values(connector.records).some((n) => n > 0);
  const inputId = `upload-${spec.provider}`;

  async function upload(file: File) {
    setNote(`Reading ${file.name}…`);
    const result = await uploadFile(file, spec.provider);
    setNote(result.ok ? uploadSummary(result.value) : result.message);
    await onChanged();
  }

  async function save() {
    const result = await saveCredentials(spec.provider, values);
    setNote(result.ok ? "Sandbox keys stored in the vault." : result.message);
    if (result.ok) setShowKeys(false);
    setValues({});
    await onChanged();
  }

  return (
    <li
      className="card flex flex-col gap-[6px] !p-[18px]"
      data-testid={`connection-${spec.provider}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-[10px]">
          <span
            aria-hidden="true"
            className="serif flex h-8 w-8 items-center justify-center rounded-[9px] bg-[#F4F2EC] text-base"
          >
            {spec.name.charAt(0)}
          </span>
          <span className="font-medium">{spec.name}</span>
        </span>
        <Pill tone={has ? "positive" : "neutral"}>{has ? "Connected" : "Not connected"}</Pill>
      </div>
      <div className="eyebrow !text-[11px]">{spec.kind}</div>
      <div className="muted text-[13px] leading-normal">{detailFor(connector)}</div>
      <div className="mt-1 flex flex-wrap gap-2">
        <label htmlFor={inputId} className="btn cursor-pointer">
          Upload a file
        </label>
        <input
          id={inputId}
          type="file"
          accept=".csv,.jsonl"
          className="sr-label"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />
        {spec.keys.length > 0 && (
          <button
            className="btn"
            type="button"
            aria-expanded={showKeys}
            onClick={() => setShowKeys((open) => !open)}
          >
            Sandbox keys
          </button>
        )}
      </div>
      {showKeys && (
        <div className="mt-1 flex flex-col gap-2">
          {spec.keys.map((key) => (
            <div key={key}>
              <label className="muted text-xs" htmlFor={`${spec.provider}-${key}`}>
                {key}
              </label>
              <input
                id={`${spec.provider}-${key}`}
                className="fld mono mt-1 !text-[13px]"
                type="password"
                autoComplete="off"
                value={values[key] ?? ""}
                onChange={(event) =>
                  setValues((previous) => ({ ...previous, [key]: event.target.value }))
                }
              />
            </div>
          ))}
          <div>
            <button className="btn btnp" type="button" onClick={() => void save()}>
              Store keys
            </button>
          </div>
        </div>
      )}
      {note && (
        <div className="soft text-[13px]" role="status">
          {note}
        </div>
      )}
    </li>
  );
}

export default function ConnectionsPage() {
  const { fixtures, state, dispatch, refresh } = useConsole();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const mcp = fixtures.custom_mcp;
  // The gateways, bank and orders are real; the other sample connections stay as previews.
  const previews = fixtures.connections.filter((c) => !REAL_NAMES.has(c.name));

  const load = useCallback(async () => {
    const result = await fetchConnectors();
    if (result.ok) setConnectors(result.value.connectors);
    await refresh();
  }, [refresh]);

  useEffect(() => {
    let current = true;
    void fetchConnectors().then((result) => {
      if (current && result.ok) setConnectors(result.value.connectors);
    });
    return () => {
      current = false;
    };
  }, []);

  return (
    <Page>
      <div className="flex flex-col gap-[22px]">
        <div>
          <h1 className="serif m-0 text-[38px]">Connections</h1>
          <p className="muted mt-[6px] mb-0 text-[15px]">
            Revlane reads with your credentials and acts only through your own
            accounts. It never holds your money.
          </p>
        </div>

        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(230px,1fr))] gap-3 p-0">
          {REAL.map((spec) => (
            <RealCard
              key={spec.provider}
              spec={spec}
              connector={connectors.find((c) => c.provider === spec.provider)}
              onChanged={load}
            />
          ))}
          {previews.map((connection) => (
            <li key={connection.name} className="card flex flex-col gap-[6px] !p-[18px]">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-[10px]">
                  <span
                    aria-hidden="true"
                    className="serif flex h-8 w-8 items-center justify-center rounded-[9px] bg-[#F4F2EC] text-base"
                  >
                    {connection.name.charAt(0)}
                  </span>
                  <span className="font-medium">{connection.name}</span>
                </span>
                <Pill tone="neutral">Preview</Pill>
              </div>
              <div className="eyebrow !text-[11px]">{connection.kind}</div>
              <div className="muted text-[13px] leading-normal">{connection.detail}</div>
            </li>
          ))}
        </ul>

        <section className="card" aria-labelledby="mcp-h">
          <h2 id="mcp-h" className="serif m-0 flex items-center gap-2 text-[21px]">
            Bring any system
            <Pill tone="neutral">Preview</Pill>
          </h2>
          <p className="muted mt-1 mb-[14px] text-sm">
            Connect your own MCP server or API. You decide which tools agents may use.
          </p>
          <div className="flex flex-wrap gap-2">
            <label htmlFor="mcpurl" className="sr-label">
              MCP server URL
            </label>
            <input
              id="mcpurl"
              className="fld mono flex-[1_1_320px] !text-[13px]"
              value={state.mcpUrl}
              onChange={(event) => dispatch({ type: "setMcpUrl", url: event.target.value })}
            />
            <button
              className="btn btnp"
              type="button"
              onClick={() => dispatch({ type: "connectMcp" })}
            >
              Connect
            </button>
          </div>
          {state.customAdded && (
            <div className="mt-4" role="status" data-testid="mcp-tools">
              <div className="font-medium">{mcp.found}</div>
              <ul className="m-0 list-none p-0">
                {mcp.tools.map((tool) => (
                  <li key={tool.name} className="line">
                    <span className="mono text-[13px]">{tool.name}</span>
                    <Pill tone={tool.on ? "positive" : "neutral"}>{tool.permission}</Pill>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </Page>
  );
}
