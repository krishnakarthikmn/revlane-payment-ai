"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";


import { useConsole } from "./ConsoleProvider";
import { roleLabel } from "@/lib/auth";

const NAV = [
  { href: "/", label: "Chat", icon: "M4 5 H20 V16 H10 L6 20 V16 H4 Z" },
  {
    href: "/agents",
    label: "Agents",
    icon: "M12 3 L14 9 L20 9.5 L15.5 13.5 L17 20 L12 16.5 L7 20 L8.5 13.5 L4 9.5 L10 9 Z",
  },
  {
    href: "/workflows",
    label: "Workflows",
    icon: "M4 5 H10 V10 H4 Z M14 14 H20 V19 H14 Z M7 10 V16.5 H14",
  },
  { href: "/approvals", label: "Approvals", icon: "M4.5 12.5 L9.5 17.5 L19.5 6.5" },
  {
    href: "/connections",
    label: "Connections",
    icon: "M9 7 H7 A5 5 0 0 0 7 17 H9 M15 7 H17 A5 5 0 0 1 17 17 H15 M8 12 H16",
  },
];

function isActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function Sidebar() {
  const { fixtures, state, dispatch, send, newChat, pendingKeys, me, live, leave } =
    useConsole();
  const pathname = usePathname();
  const expanded = state.pinned || state.hover;
  const pending = pendingKeys.length;
  const pinLabel = state.pinned ? "Collapse sidebar" : "Keep sidebar open";

  return (
    <nav
      aria-label="Main"
      className="relative transition-[flex-basis] duration-200 ease-in-out"
      style={{ flex: `0 0 ${state.pinned ? 264 : 68}px` }}
    >
      <div
        onMouseEnter={() => dispatch({ type: "setHover", hover: true })}
        onMouseLeave={() => dispatch({ type: "setHover", hover: false })}
        className="sticky top-0 z-20 flex h-screen flex-col gap-[2px] overflow-hidden bg-[#F4F2EC] px-[10px] py-3 transition-[width,box-shadow] duration-200 ease-in-out"
        style={{
          width: expanded ? 264 : 68,
          boxShadow:
            !state.pinned && state.hover ? "8px 0 32px rgba(31,30,29,.10)" : undefined,
        }}
      >
        <div className="flex min-h-12 items-center justify-between gap-2 pt-1 pr-1 pb-[14px] pl-[6px]">
          {expanded && (
            <span className="flex items-center gap-[10px]">
              <svg width="24" height="24" viewBox="0 0 26 26" aria-hidden="true">
                <circle cx="13" cy="13" r="12" fill="#1F1E1D" />
                <path
                  d="M7 16.5 C10 16.5 10 9.5 13 9.5 C16 9.5 16 16.5 19 16.5"
                  stroke="#D97757"
                  strokeWidth="2.2"
                  fill="none"
                  strokeLinecap="round"
                />
              </svg>
              <span className="serif text-[21px]">Revlane</span>
            </span>
          )}
          <button
            className="icon"
            type="button"
            aria-label={pinLabel}
            aria-pressed={state.pinned}
            title={pinLabel}
            onClick={() => dispatch({ type: "togglePin" })}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
              <rect
                x="3.5"
                y="4.5"
                width="17"
                height="15"
                rx="3"
                stroke="#3D3C38"
                strokeWidth="1.6"
                fill="none"
              />
              <path d="M9.5 4.5 V19.5" stroke="#3D3C38" strokeWidth="1.6" />
            </svg>
          </button>
        </div>

        <button
          className="side !font-medium !text-[#1F1E1D]"
          type="button"
          onClick={newChat}
          title="New chat"
          aria-label="New chat"
        >
          <span className="inline-flex h-[26px] w-[26px] flex-none items-center justify-center rounded-full bg-[#D97757]">
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path
                d="M6 1 V11 M1 6 H11"
                stroke="#1F1E1D"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </span>
          {expanded && <span>New chat</span>}
        </button>
        <div className="h-[6px]" />

        {NAV.map((item) => {
          const active = isActive(item.href, pathname);
          const badge = item.href === "/approvals" && pending > 0;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`side ${active ? "side-active" : ""}`}
              title={item.label}
              aria-label={
                expanded ? undefined : badge ? `${item.label}, ${pending} waiting` : item.label
              }
              aria-current={active ? "page" : undefined}
              onClick={() => dispatch({ type: "closeOverlays" })}
            >
              <span className="inline-flex w-[26px] flex-none justify-center">
                <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d={item.icon}
                    stroke="#3D3C38"
                    strokeWidth="1.6"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
              {expanded && (
                <>
                  <span className="flex-auto">{item.label}</span>
                  {badge && (
                    <span className="pill pill-warm num" data-testid="approvals-badge">
                      {pending}
                      <span className="sr-only"> waiting</span>
                    </span>
                  )}
                </>
              )}
            </Link>
          );
        })}

        {expanded && (
          <>
            <div className="muted px-[10px] pt-5 pb-[6px] text-xs">Recents</div>
            {fixtures.chat.recents.map((recent) => (
              <button
                key={recent.label}
                className="side !block flex-none !text-[#4A4944] text-ellipsis"
                style={{ lineHeight: "38px" }}
                type="button"
                onClick={() => send(recent.question, true)}
              >
                {recent.label}
              </button>
            ))}
          </>
        )}

        <div className="mt-auto flex items-center gap-[10px] px-[6px] pt-3 pb-1">
          <span
            aria-hidden="true"
            className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-full bg-[#1F1E1D] text-[13px] font-semibold text-[#FAF9F5]"
          >
            {me.name.charAt(0).toUpperCase()}
          </span>
          {expanded && (
            <>
              <span className="flex min-w-0 flex-auto flex-col whitespace-nowrap">
                <span className="text-sm font-medium">{me.name}</span>
                <span className="muted text-xs">
                  {live?.merchant.name ?? fixtures.merchant.name} · {roleLabel(me.role)}
                </span>
              </span>
              <button
                className="btn btn-ghost !min-h-8 !px-2 text-[13px]"
                type="button"
                onClick={() => void leave()}
              >
                Sign out
              </button>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
