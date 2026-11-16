"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { fetchMe, signIn } from "@/lib/auth";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Someone already signed in has no business here.
  useEffect(() => {
    let current = true;
    void fetchMe().then((result) => {
      if (current && result.ok) router.replace("/");
    });
    return () => {
      current = false;
    };
  }, [router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setProblem("Enter your email and password.");
      return;
    }
    setBusy(true);
    setProblem(null);
    const result = await signIn(email, password);
    setBusy(false);
    if (result.ok) {
      router.replace("/");
    } else {
      setProblem(result.message);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="card flex flex-col gap-4" noValidate>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-[13px] font-medium text-secondary">
          Email
        </label>
        <input
          id="email"
          type="email"
          className="fld"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="password" className="text-[13px] font-medium text-secondary">
          Password
        </label>
        <input
          id="password"
          type="password"
          className="fld"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>
      {problem && (
        <p role="alert" className="soft m-0 text-sm text-link">
          {problem}
        </p>
      )}
      <div>
        <button type="submit" className="btn btnp" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </form>
  );
}
