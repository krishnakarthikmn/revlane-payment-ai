import type { Metadata } from "next";

import { LoginForm } from "@/components/LoginForm";

export const metadata: Metadata = { title: "Sign in · Revlane" };

export default function Login() {
  return (
    <main className="mx-auto flex w-full max-w-[420px] flex-1 flex-col justify-center gap-6 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="serif m-0 text-[38px] leading-[1.1]">Sign in to Revlane</h1>
        <p className="muted m-0 text-[15px]">
          What happened to every rupee between checkout and the bank account.
        </p>
      </header>
      <LoginForm />
    </main>
  );
}
