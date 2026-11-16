"use client";

import { useConsole } from "@/components/console/ConsoleProvider";
import { Pill, Star } from "@/components/ui";
import type { BotMessage, UserMessage } from "@/lib/console/state";
import { attachmentTone } from "@/lib/console/view";

import { AgentCard } from "./AgentCard";
import {
  FixList,
  HourlyChart,
  LeakChart,
  LiveBars,
  RoutineCard,
  RoutineList,
  RowsTable,
  SkillDraft,
  WorkflowDraft,
} from "./blocks";

export function UserBubble({ message }: { message: UserMessage }) {
  return (
    <div
      className="rise flex max-w-[80%] flex-col items-end gap-[6px] self-end"
      data-testid="user-message"
    >
      {message.files.length > 0 && (
        <div className="flex flex-wrap justify-end gap-[6px]">
          {message.files.map((file) => (
            <span key={file.name} className="att !pr-[10px]">
              <Pill tone={attachmentTone[file.kind]} className="mono !px-[6px] !py-[2px]">
                {file.kind}
              </Pill>
              {file.name}
            </span>
          ))}
        </div>
      )}
      <div className="rounded-[18px] bg-[#F0EDE5] px-4 py-[11px] text-[15px] leading-normal text-[#1F1E1D]">
        {message.text}
      </div>
    </div>
  );
}

export function BotReply({ message }: { message: BotMessage }) {
  const { agents, runAction } = useConsole();
  const agent = message.agentKey
    ? agents.find((a) => a.key === message.agentKey)
    : undefined;
  const steps = message.steps;
  const action = message.action;

  return (
    <div className="rise flex items-start gap-[14px]" data-testid="bot-message">
      <Star size={22} className="mt-1 flex-none" />
      <div className="flex min-w-0 flex-auto flex-col gap-4">
        {steps.length > 0 && (
          <details className="text-[13px] text-[#6B6A63]">
            <summary className="min-h-6 cursor-pointer">
              Used {steps.length} {steps.length === 1 ? "tool" : "tools"}
            </summary>
            <div className="mt-2 flex flex-wrap gap-[6px]">
              {steps.map((step) => (
                <span
                  key={step}
                  className="pill mono bg-[#F4F2EC] !font-normal text-[#3D3C38]"
                >
                  {step}
                </span>
              ))}
            </div>
          </details>
        )}
        {message.preview && (
          <div>
            <Pill tone="neutral">Preview</Pill>
          </div>
        )}
        <div className="serif text-[18.5px] leading-[1.65] text-[#1F1E1D]">
          {message.text}
        </div>

        {message.bars && message.bars.length > 0 && <LiveBars bars={message.bars} />}
        {message.block === "chart" && <LeakChart />}
        {message.block === "hourly" && <HourlyChart />}
        {message.rows && message.rows.length > 0 && <RowsTable rows={message.rows} />}
        {message.fixes && message.fixes.length > 0 && <FixList keys={message.fixes} />}
        {agent && <AgentCard agent={agent} messageId={message.id} />}
        {message.block === "workflow" && <WorkflowDraft />}
        {message.block === "routine" && <RoutineCard />}
        {message.block === "routines" && <RoutineList />}
        {message.block === "skill" && <SkillDraft messageId={message.id} />}

        {message.source && (
          <div className="muted mono text-[11.5px]">{message.source}</div>
        )}
        {action && (
          <div>
            <button className="btn" type="button" onClick={() => runAction(action)}>
              {message.actionLabel}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
