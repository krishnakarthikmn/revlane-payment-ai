"use client";

import { useRef } from "react";

import { useConsole } from "@/components/console/ConsoleProvider";
import { ClockIcon, Pill, StrokeIcon } from "@/components/ui";
import { attachmentTone } from "@/lib/console/view";

function Divider() {
  return <div className="mx-2 my-1 h-px bg-[#F1EFE8]" />;
}

function PlusMenu() {
  const { fixtures, state, dispatch, send, attachFile } = useConsole();
  const { attachments, prompts } = fixtures.chat;
  const picker = useRef<HTMLInputElement>(null);
  const skills = [
    ...fixtures.skills,
    ...(state.skillSaved ? [fixtures.skill_draft.name] : []),
  ];

  return (
    <div className="menu" role="group" aria-label="Add to your message">
      <input
        ref={picker}
        type="file"
        accept=".csv,.jsonl"
        className="hidden"
        aria-label="File to upload"
        data-testid="chat-file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void attachFile(file);
          event.target.value = "";
        }}
      />
      <button className="mi" type="button" onClick={() => picker.current?.click()}>
        <span className="ic">
          <StrokeIcon d="M14 3 H7 A2 2 0 0 0 5 5 V19 A2 2 0 0 0 7 21 H17 A2 2 0 0 0 19 19 V8 Z M14 3 V8 H19" />
        </span>
        <span className="flex flex-col">
          <span>Upload a file</span>
          <span className="muted text-xs">Statements, settlement reports, contracts</span>
        </span>
      </button>
      <button
        className="mi"
        type="button"
        onClick={() => dispatch({ type: "addAttach", item: attachments.connection })}
      >
        <span className="ic">
          <StrokeIcon d="M9 7 H7 A5 5 0 0 0 7 17 H9 M15 7 H17 A5 5 0 0 1 17 17 H15 M8 12 H16" />
        </span>
        <span className="flex flex-col">
          <span>Add from connections</span>
          <span className="muted text-xs">Shopify, Zoho Books, BigQuery, your MCP</span>
        </span>
      </button>
      <Divider />
      <button
        className="mi"
        type="button"
        aria-expanded={state.skillsOpen}
        onClick={() => dispatch({ type: "toggleSkills" })}
      >
        <span className="ic">
          <StrokeIcon d="M5 4 H15 L19 8 V20 H5 Z M9 12 H15 M9 16 H13" />
        </span>
        <span className="flex-auto">Use a skill</span>
        <span aria-hidden="true" className="muted">
          ›
        </span>
      </button>
      {state.skillsOpen && (
        <div className="mb-1 ml-[52px] border-l border-[#EAE7DE] pl-[6px]">
          {skills.map((name) => (
            <button
              key={name}
              className="mi !min-h-[34px] !text-[13.5px]"
              type="button"
              onClick={() =>
                dispatch({ type: "addAttach", item: { name, kind: "SKILL" } })
              }
            >
              {name}
            </button>
          ))}
        </div>
      )}
      <button className="mi" type="button" onClick={() => send(prompts.create_skill)}>
        <span className="ic">
          <StrokeIcon d="M12 5 V19 M5 12 H19" />
        </span>
        <span className="flex flex-col">
          <span>Create a skill</span>
          <span className="muted text-xs">Teach Revlane your way of working</span>
        </span>
      </button>
      <Divider />
      <button
        className="mi"
        type="button"
        onClick={() => dispatch({ type: "startRoutine", draft: prompts.start_routine })}
      >
        <span className="ic">
          <ClockIcon size={16} />
        </span>
        <span className="flex flex-col">
          <span>Create a routine</span>
          <span className="muted text-xs">Run a question or agent on a schedule</span>
        </span>
      </button>
      <button className="mi" type="button" onClick={() => send(prompts.manage_routines)}>
        <span className="ic">
          <StrokeIcon d="M5 7 H19 M5 12 H19 M5 17 H13" />
        </span>
        <span>Manage routines</span>
      </button>
    </div>
  );
}

export function Composer() {
  const { fixtures, state, dispatch, send } = useConsole();
  const chat = fixtures.chat;
  const hasCsv = state.attach.some((a) => a.kind === "CSV");

  return (
    <div className="composer">
      {state.attach.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-[6px]" data-testid="attachments">
          {state.attach.map((item) => (
            <span key={item.name} className="att">
              <Pill tone={attachmentTone[item.kind]} className="mono !px-[6px] !py-[2px]">
                {item.kind}
              </Pill>
              {item.name}
              <button
                className="x"
                type="button"
                aria-label={`Remove ${item.name}`}
                onClick={() => dispatch({ type: "removeAttach", name: item.name })}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <label htmlFor="chatin" className="sr-label">
        Message Revlane
      </label>
      <input
        id="chatin"
        className="main"
        autoComplete="off"
        placeholder={hasCsv ? chat.placeholder_with_file : chat.placeholder}
        value={state.draft}
        onChange={(event) => dispatch({ type: "setDraft", draft: event.target.value })}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.nativeEvent.isComposing) send(state.draft);
        }}
      />
      <div className="mt-2 flex items-center gap-1">
        <button
          className="icon !h-[34px] !w-[34px] border border-solid border-[#E3E0D6]"
          type="button"
          aria-label="Add files, skills or routines"
          aria-expanded={state.plusOpen}
          onClick={() => dispatch({ type: "togglePlus" })}
        >
          <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M8 2 V14 M2 8 H14"
              stroke="#3D3C38"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>
        <button
          className="icon !h-[34px] !w-[34px]"
          type="button"
          aria-label="Routines"
          title="Routines"
          onClick={() => send(chat.prompts.manage_routines)}
        >
          <ClockIcon size={17} />
        </button>
        <span className="flex-auto" />
        <span className="px-[10px] text-[13px] text-[#4A4944]">
          Revlane · {fixtures.merchant.name.split(" ")[0]} data
        </span>
        <button
          className="send"
          type="button"
          aria-label="Send"
          onClick={() => send(state.draft)}
        >
          <StrokeIcon
            d="M12 19 L12 5 M5 12 L12 5 L19 12"
            color="#FFFFFF"
            width={2.4}
          />
        </button>
      </div>
      {state.plusOpen && <PlusMenu />}
    </div>
  );
}
