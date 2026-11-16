import type { Metadata } from "next";

import { ContractsForm } from "@/components/ContractsForm";
import { RequireSignIn } from "@/components/RequireSignIn";

export const metadata: Metadata = { title: "Fee contracts · Revlane" };

export default function Contracts() {
  return (
    <main className="mx-auto flex w-full max-w-[860px] flex-1 flex-col gap-8 px-6 py-16">
      <header className="flex flex-col gap-3">
        <p className="eyebrow">Fee audit</p>
        <h1 className="serif text-[46px] leading-[1.1]">Fee contracts</h1>
        <p className="text-[15px] leading-relaxed text-secondary">
          The rates you agreed with each provider. The fee audit recomputes the fee on
          every settlement line from the contract in force that day and reports
          anything charged above it.
        </p>
      </header>
      <RequireSignIn>
        <ContractsForm />
      </RequireSignIn>
    </main>
  );
}
