"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { fetchMe } from "@/lib/auth";

/** Shows its children only to someone signed in; anyone else goes to the sign-in page. */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    let current = true;
    void fetchMe().then((who) => {
      if (!current) return;
      if (who.ok) setSignedIn(true);
      else router.replace("/login");
    });
    return () => {
      current = false;
    };
  }, [router]);

  return signedIn ? <>{children}</> : null;
}
