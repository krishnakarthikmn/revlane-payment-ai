"use client";

import { usePathname, useRouter } from "next/navigation";

import { useConsole } from "./ConsoleProvider";

function when(time: string): string {
  return new Date(time).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function useTitle(): string {
  const { fixtures, state } = useConsole();
  const pathname = usePathname();
  if (pathname === "/") return state.messages.length ? "Revenue chat" : "New chat";
  if (pathname.startsWith("/agents/")) {
    const key = pathname.slice("/agents/".length);
    const agent = fixtures.agents.find((a) => a.key === key);
    return agent ? `Agents / ${agent.name}` : "Agents";
  }
  if (pathname === "/agents") return "Agents";
  if (pathname === "/workflows") return "Workflows";
  if (pathname === "/approvals") return "Approvals";
  if (pathname === "/connections") return "Connections";
  return "";
}

export function Header() {
  const { state, dispatch, live } = useConsole();
  const router = useRouter();
  const title = useTitle();
  const notes = live?.notifications ?? [];
  const unread = state.unread && notes.length > 0;

  return (
    <header className="relative flex items-center justify-between gap-3 px-5 py-3">
      <span className="text-sm text-[#4A4944]" data-testid="view-title">
        {title}
      </span>
      <div className="relative flex items-center gap-[6px]">
        {live?.sample_data && (
          <span className="pill bg-[#EFECE4] text-[#4A4944]">Sample data</span>
        )}
        <button
          className="icon relative"
          type="button"
          aria-label={unread ? "Notifications, unread" : "Notifications"}
          aria-expanded={state.notifOpen}
          onClick={() => dispatch({ type: "toggleNotif" })}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M6 16 V11 A6 6 0 0 1 18 11 V16 L20 18 H4 Z M10 21 H14"
              stroke="#3D3C38"
              strokeWidth="1.6"
              fill="none"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
          {unread && (
            <span className="absolute top-[7px] right-[7px] h-2 w-2 rounded-full border-2 border-[#FAF9F5] bg-[#D97757] box-content" />
          )}
        </button>
        {state.notifOpen && (
          <div
            role="region"
            aria-label="Notifications"
            className="rise absolute top-[46px] right-0 z-30 w-[380px] rounded-2xl border border-[#E3E0D6] bg-white p-[6px] shadow-[0_16px_40px_rgba(31,30,29,.14)]"
          >
            <div className="eyebrow px-[10px] pt-[10px] pb-[6px]">Notifications</div>
            {notes.length === 0 && (
              <p className="muted m-0 px-[10px] pb-3 text-[13px]">
                Nothing yet. Findings and fixes waiting for you will show here.
              </p>
            )}
            {notes.map((note) => (
              <button
                key={note.id}
                className="mi !items-start"
                type="button"
                onClick={() => {
                  dispatch({ type: "markRead" });
                  router.push(note.link);
                }}
              >
                <span
                  className="mt-[7px] h-2 w-2 flex-none rounded-full"
                  style={{ background: note.dot }}
                />
                <span className="flex flex-col gap-[2px]">
                  <span className="font-medium">{note.title}</span>
                  <span className="muted text-[13px] leading-[1.45]">{note.body}</span>
                  <span className="muted text-xs">{when(note.time)}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </header>
  );
}
