import type { ReactNode } from "react";

import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-[#FAF9F5]">
      <Sidebar />
      <main className="flex min-h-screen min-w-0 flex-auto flex-col">
        <Header />
        {children}
      </main>
    </div>
  );
}

/** The frame every view except chat sits in. */
export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-auto justify-center px-10 pt-3 pb-14">
      <div className="rise flex w-full max-w-[1040px] flex-col gap-6">{children}</div>
    </div>
  );
}
