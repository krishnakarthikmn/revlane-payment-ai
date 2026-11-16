"use client";

import { useEffect, useRef } from "react";

import { useConsole } from "@/components/console/ConsoleProvider";
import { SetupCard } from "@/components/console/SetupCard";
import { Star } from "@/components/ui";
import { summaryText } from "@/lib/live";

import { Composer } from "./Composer";
import { BotReply, UserBubble } from "./Message";

export function ChatView() {
  const { fixtures, state, dispatch, send, live, agents, pendingKeys } = useConsole();
  const isEmpty = state.messages.length === 0 && !state.thinking;
  const { summary, merchant, chat } = fixtures;
  const needsSetup = live !== null && !live.setup.has_audit;

  // Keep the newest message in view, above the sticky composer.
  const end = useRef<HTMLDivElement>(null);
  const count = state.messages.length;
  useEffect(() => {
    if (count > 0) end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [count, state.thinking]);

  return (
    <div className="flex flex-auto flex-col items-center px-6 pb-6">
      <div
        className={`flex w-full max-w-[740px] flex-auto flex-col gap-7 ${
          isEmpty ? "justify-center pt-[10vh]" : "pt-5"
        }`}
      >
        {isEmpty && (
          <div className="rise flex flex-col items-center gap-[22px]">
            {needsSetup ? (
              <SetupCard />
            ) : (
              <button
                type="button"
                onClick={() => {
                  dispatch({ type: "markRead" });
                  send(summary.question);
                }}
                className="inline-flex min-h-9 items-center gap-[10px] rounded-full border border-[#E6E3D9] bg-white px-[6px] text-[13px] text-[#3D3C38] shadow-[0_1px_2px_rgba(31,30,29,.04)]"
              >
                <span className="pill pill-warm">{summary.pill}</span>
                {live ? summaryText(agents, pendingKeys.length) : summary.text}
                <span className="pr-2" aria-hidden="true">
                  →
                </span>
              </button>
            )}
            <h1 className="serif m-0 flex items-center gap-4 text-[46px] font-normal">
              <Star size={34} spin />
              Good morning, {merchant.user.first_name}
            </h1>
          </div>
        )}

        <div
          className="contents"
          role="log"
          aria-label="Conversation"
          aria-live="polite"
        >
          {state.messages.map((message) =>
            message.role === "user" ? (
              <UserBubble key={message.id} message={message} />
            ) : (
              <BotReply key={message.id} message={message} />
            ),
          )}
          {state.thinking && (
            <div className="rise flex items-center gap-[14px]" role="status">
              <Star size={22} spin />
              <span className="muted text-sm">{state.thinkingText}</span>
            </div>
          )}
        </div>
        <div ref={end} aria-hidden="true" className="-mt-7 h-px scroll-mb-40" />

        <div className={isEmpty ? "" : "sticky bottom-4 z-[4] mt-auto"}>
          <Composer />
          {isEmpty && (
            <div className="mt-[18px] flex flex-wrap justify-center gap-2">
              {chat.quick.map((quick) => (
                <button
                  key={quick.label}
                  className="chip"
                  type="button"
                  onClick={() => send(quick.question)}
                >
                  {quick.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
