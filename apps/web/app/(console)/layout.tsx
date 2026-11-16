import { Suspense, type ReactNode } from "react";

import { ConsoleProvider } from "@/components/console/ConsoleProvider";
import { Shell } from "@/components/console/Shell";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <ConsoleProvider>
        <Shell>{children}</Shell>
      </ConsoleProvider>
    </Suspense>
  );
}
