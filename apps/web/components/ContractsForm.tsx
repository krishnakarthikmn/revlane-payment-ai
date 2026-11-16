"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  CARD_NETWORKS,
  CARD_TYPES,
  type Contract,
  type ContractForm,
  EMPTY_FORM,
  type FormErrors,
  METHODS,
  PROVIDERS,
  deleteContract,
  describeMethod,
  formatFixedFee,
  formatRate,
  label,
  listContracts,
  saveContract,
  toForm,
  validate,
} from "@/lib/contracts";

type Option = { value: string; label: string };

function Field({
  id,
  text,
  error,
  hint,
  children,
}: {
  id: string;
  text: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium text-secondary">
        {text}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-link">
          {error}
        </p>
      )}
    </div>
  );
}

function Select({
  id,
  value,
  options,
  onChange,
}: {
  id: string;
  value: string;
  options: readonly Option[];
  onChange: (value: string) => void;
}) {
  return (
    <select
      id={id}
      className="fld"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function period(contract: Contract): string {
  return contract.effective_to
    ? `${contract.effective_from} to ${contract.effective_to}`
    : `From ${contract.effective_from}`;
}

export function ContractsForm() {
  const [contracts, setContracts] = useState<Contract[] | null>(null);
  const [form, setForm] = useState<ContractForm>(EMPTY_FORM);
  const [editing, setEditing] = useState<string | null>(null);
  const [errors, setErrors] = useState<FormErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const result = await listContracts();
    if (result.ok) {
      setContracts(result.value);
    } else {
      setContracts([]);
      setMessage(result.message);
    }
  }

  useEffect(() => {
    let current = true;
    void listContracts().then((result) => {
      if (!current) return;
      if (result.ok) {
        setContracts(result.value);
      } else {
        setContracts([]);
        setMessage(result.message);
      }
    });
    return () => {
      current = false;
    };
  }, []);

  function set<K extends keyof ContractForm>(key: K, value: ContractForm[K]) {
    setForm((previous) => {
      const next = { ...previous, [key]: value };
      // A marketplace charges a commission; a gateway charges for the payment.
      if (key === "method" && previous.allowedFeeTypes === allowedFor(previous.method)) {
        next.allowedFeeTypes = allowedFor(String(value));
      }
      return next;
    });
    setErrors((previous) => ({ ...previous, [key]: undefined }));
    setSaved(null);
  }

  function reset() {
    setForm(EMPTY_FORM);
    setEditing(null);
    setErrors({});
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    setSaved(null);
    const checked = validate(form);
    if (!checked.ok) {
      setErrors(checked.errors);
      return;
    }
    setBusy(true);
    const result = await saveContract(checked.payload, editing);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    setSaved(
      `${editing ? "Saved" : "Added"} ${label(result.value.provider)} · ${describeMethod(result.value)}. ` +
        "It applies the next time the audit runs.",
    );
    reset();
    await refresh();
  }

  async function remove(id: string) {
    setMessage(null);
    setSaved(null);
    const result = await deleteContract(id);
    setConfirming(null);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    if (editing === id) reset();
    await refresh();
  }

  const isCard = form.method === "card";

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="contracts-title" className="card">
        <h2 id="contracts-title" className="eyebrow pb-1">
          Contracts on file
        </h2>
        {contracts === null && <p className="py-3 text-sm text-muted">Loading…</p>}
        {contracts?.length === 0 && (
          <p className="py-3 text-sm text-secondary">
            No contracts yet. Until you add one, the fee audit has nothing to check
            charges against.
          </p>
        )}
        {contracts && contracts.length > 0 && (
          <ul>
            {contracts.map((contract) => (
              <li key={contract.id} className="line items-center" data-testid="contract-row">
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="font-medium">
                    {label(contract.provider)} · {describeMethod(contract)}
                  </span>
                  <span className="text-[13px] text-muted">
                    {period(contract)} · allows {contract.allowed_fee_types.join(", ")}
                    {contract.tax_on_refunded_fee ? " · keeps tax on refunded fees" : ""}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="tabular-nums">
                    {formatRate(contract.rate_bps)}
                    {contract.fixed_fee_minor > 0
                      ? ` + ${formatFixedFee(contract.fixed_fee_minor)}`
                      : ""}
                  </span>
                  {confirming === contract.id ? (
                    <>
                      <button
                        type="button"
                        className="btn btnp"
                        onClick={() => void remove(contract.id)}
                      >
                        Confirm delete
                      </button>
                      <button type="button" className="btn" onClick={() => setConfirming(null)}>
                        Keep
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn"
                        aria-label={`Edit ${label(contract.provider)} ${describeMethod(contract)}`}
                        onClick={() => {
                          setForm(toForm(contract));
                          setEditing(contract.id);
                          setErrors({});
                          setSaved(null);
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn"
                        aria-label={`Delete ${label(contract.provider)} ${describeMethod(contract)}`}
                        onClick={() => setConfirming(contract.id)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form onSubmit={(event) => void submit(event)} className="card flex flex-col gap-4" noValidate>
        <h2 className="eyebrow">{editing ? "Edit contract" : "Add a contract"}</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="provider" text="Provider">
            <Select
              id="provider"
              value={form.provider}
              options={PROVIDERS}
              onChange={(value) => set("provider", value)}
            />
          </Field>
          <Field id="method" text="Payment method">
            <Select
              id="method"
              value={form.method}
              options={METHODS}
              onChange={(value) => set("method", value)}
            />
          </Field>
          {isCard && (
            <>
              <Field id="cardNetwork" text="Card network">
                <Select
                  id="cardNetwork"
                  value={form.cardNetwork}
                  options={CARD_NETWORKS}
                  onChange={(value) => set("cardNetwork", value)}
                />
              </Field>
              <Field id="cardType" text="Card type">
                <Select
                  id="cardType"
                  value={form.cardType}
                  options={CARD_TYPES}
                  onChange={(value) => set("cardType", value)}
                />
              </Field>
            </>
          )}
          <Field id="ratePercent" text="Rate (%)" error={errors.ratePercent}>
            <input
              id="ratePercent"
              className="fld tabular-nums"
              inputMode="decimal"
              placeholder="1.86"
              value={form.ratePercent}
              aria-invalid={Boolean(errors.ratePercent)}
              aria-describedby={errors.ratePercent ? "ratePercent-error" : undefined}
              onChange={(event) => set("ratePercent", event.target.value)}
            />
          </Field>
          <Field id="fixedFeeRupees" text="Fixed fee per payment (₹)" error={errors.fixedFeeRupees}>
            <input
              id="fixedFeeRupees"
              className="fld tabular-nums"
              inputMode="decimal"
              value={form.fixedFeeRupees}
              aria-invalid={Boolean(errors.fixedFeeRupees)}
              aria-describedby={errors.fixedFeeRupees ? "fixedFeeRupees-error" : undefined}
              onChange={(event) => set("fixedFeeRupees", event.target.value)}
            />
          </Field>
          <Field id="taxPercent" text="Tax on the fee (%)" error={errors.taxPercent}>
            <input
              id="taxPercent"
              className="fld tabular-nums"
              inputMode="decimal"
              value={form.taxPercent}
              aria-invalid={Boolean(errors.taxPercent)}
              aria-describedby={errors.taxPercent ? "taxPercent-error" : undefined}
              onChange={(event) => set("taxPercent", event.target.value)}
            />
          </Field>
          <Field
            id="allowedFeeTypes"
            text="Fees the contract allows"
            error={errors.allowedFeeTypes}
            hint="Separate with commas. Any other fee type is reported."
          >
            <input
              id="allowedFeeTypes"
              className="fld"
              value={form.allowedFeeTypes}
              aria-invalid={Boolean(errors.allowedFeeTypes)}
              aria-describedby={errors.allowedFeeTypes ? "allowedFeeTypes-error" : undefined}
              onChange={(event) => set("allowedFeeTypes", event.target.value)}
            />
          </Field>
          <Field id="effectiveFrom" text="In force from" error={errors.effectiveFrom}>
            <input
              id="effectiveFrom"
              type="date"
              className="fld"
              value={form.effectiveFrom}
              aria-invalid={Boolean(errors.effectiveFrom)}
              aria-describedby={errors.effectiveFrom ? "effectiveFrom-error" : undefined}
              onChange={(event) => set("effectiveFrom", event.target.value)}
            />
          </Field>
          <Field
            id="effectiveTo"
            text="Until"
            error={errors.effectiveTo}
            hint="Leave empty while the contract is still running."
          >
            <input
              id="effectiveTo"
              type="date"
              className="fld"
              value={form.effectiveTo}
              aria-invalid={Boolean(errors.effectiveTo)}
              aria-describedby={errors.effectiveTo ? "effectiveTo-error" : undefined}
              onChange={(event) => set("effectiveTo", event.target.value)}
            />
          </Field>
        </div>
        <label className="flex items-start gap-2.5 text-sm text-secondary">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={form.providerKeepsTax}
            onChange={(event) => set("providerKeepsTax", event.target.checked)}
          />
          <span>
            The provider may keep the tax on a fee it refunds. Leave this off if the
            tax must come back with the fee.
          </span>
        </label>
        {message && (
          <p role="alert" className="soft text-sm text-link">
            {message}
          </p>
        )}
        {saved && (
          <p role="status" className="soft text-sm text-secondary">
            {saved}
          </p>
        )}
        <div className="flex gap-2">
          <button type="submit" className="btn btnp" disabled={busy}>
            {editing ? "Save changes" : "Add contract"}
          </button>
          {editing && (
            <button type="button" className="btn" onClick={reset}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function allowedFor(method: string): string {
  return method === "commission" ? "commission" : "payment";
}
