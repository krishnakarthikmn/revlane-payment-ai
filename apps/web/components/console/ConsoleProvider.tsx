"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";

import { fetchMe, signOut, type Me } from "@/lib/auth";
import { thinkingFor } from "@/lib/console/routing";
import {
  actionMessage,
  initialState,
  reducer,
  replyToMessage,
  type Action,
  type ConsoleState,
} from "@/lib/console/state";
import {
  fetchFixtures,
  type Fixtures,
  type FixturesResult,
  type Status,
} from "@/lib/fixtures";
import {
  askChat,
  chatMessage,
  currentProposal,
  decideProposal,
  fetchConsole,
  liveStatus,
  mergeAgents,
  previewKind,
  startAudit,
  uploadFile,
  uploadSummary,
  type ConsoleAgent,
  type LiveConsole,
  type UploadReport,
} from "@/lib/live";

const THINKING_MS = 1200;
const POLL_MS = 1500;
const AUDIT_STARTED =
  "The audit has started. It matches every record and checks every fee; the agents then make their proposals.";
const PINNED_FROM_WIDTH = 1280;

type Console = {
  fixtures: Fixtures;
  state: ConsoleState;
  dispatch: Dispatch<Action>;
  /** The person signed in. */
  me: Me;
  /** Ends the session and returns to the sign-in page. */
  leave: () => Promise<void>;
  /** What the API says about the real surfaces. Null until it has answered. */
  live: LiveConsole | null;
  /** Every agent: the two real ones carry live data, the rest are previews. */
  agents: ConsoleAgent[];
  statusOf: (agent: ConsoleAgent) => Status;
  /** Keys of the agents waiting for a decision. */
  pendingKeys: string[];
  /** Approves or rejects what an agent proposes. For a real agent this calls the API. */
  decide: (agent: ConsoleAgent, decision: "approved" | "rejected") => Promise<void>;
  /** Queues the audit and keeps the screens fresh until it has finished. */
  runAudit: () => Promise<string | null>;
  refresh: () => Promise<void>;
  /** Uploads a file and attaches it to the next message. */
  attachFile: (file: File) => Promise<void>;
  /** Sends a question to chat. `fresh` starts a new conversation first. */
  send: (text: string, fresh?: boolean) => void;
  /** Runs a reply's action ("fixes" or "card:<agent>") and shows it in chat. */
  runAction: (action: string, fresh?: boolean) => void;
  newChat: () => void;
};

const ConsoleContext = createContext<Console | null>(null);

export function useConsole(): Console {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole must be used inside ConsoleProvider");
  return value;
}

function Notice({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-2 px-6 text-center">
      <h1 className="serif m-0 text-[28px]">{title}</h1>
      {children && <p className="muted m-0 text-[15px]">{children}</p>}
    </main>
  );
}

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [result, setResult] = useState<FixturesResult | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const router = useRouter();

  // Nothing is shown to anyone who is not signed in.
  useEffect(() => {
    let current = true;
    void fetchMe().then((who) => {
      if (!current) return;
      if (!who.ok) {
        router.replace("/login");
        return;
      }
      setMe(who.value);
      void fetchFixtures().then((next) => {
        if (current) setResult(next);
      });
    });
    return () => {
      current = false;
    };
  }, [router]);

  if (result === null || me === null) return <Notice title="Loading Revlane…" />;
  if (result.state === "down") {
    return (
      <Notice title="Revlane can't reach its API">
        {result.message}. Start it with <code className="mono">make dev</code>.
      </Notice>
    );
  }
  return (
    <Ready fixtures={result.fixtures} me={me}>
      {children}
    </Ready>
  );
}

function startState(fixtures: Fixtures): ConsoleState {
  // Below the minimum supported width the sidebar starts collapsed.
  return { ...initialState(fixtures), pinned: window.innerWidth >= PINNED_FROM_WIDTH };
}

function Ready({
  fixtures,
  me,
  children,
}: {
  fixtures: Fixtures;
  me: Me;
  children: ReactNode;
}) {
  const [state, dispatch] = useReducer(reducer, fixtures, startState);
  const [live, setLive] = useState<LiveConsole | null>(null);
  const uploads = useRef<Record<string, UploadReport>>({});
  const router = useRouter();
  const pathname = usePathname();

  const refresh = useCallback(async () => {
    const result = await fetchConsole();
    if (result.ok) setLive(result.value);
  }, []);

  useEffect(() => {
    let current = true;
    void fetchConsole().then((result) => {
      if (current && result.ok) setLive(result.value);
    });
    return () => {
      current = false;
    };
  }, []);

  // While the audit runs, ask again until it has finished.
  const running = live?.audit.running ?? false;
  useEffect(() => {
    if (!running) return;
    const poll = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(poll);
  }, [running, refresh]);

  const agents = useMemo(() => mergeAgents(fixtures.agents, live), [fixtures, live]);
  const statusOf = useCallback(
    (agent: ConsoleAgent): Status =>
      agent.live ? liveStatus(agent.live) : (state.status[agent.key] ?? "pending"),
    [state.status],
  );
  const pendingKeys = useMemo(
    () => agents.filter((agent) => statusOf(agent) === "pending").map((agent) => agent.key),
    [agents, statusOf],
  );

  const latest = useRef({ state, pathname, pendingKeys });
  useEffect(() => {
    latest.current = { state, pathname, pendingKeys };
  });

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") dispatch({ type: "closeOverlays" });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const goChat = useCallback(() => {
    if (latest.current.pathname !== "/") router.push("/");
  }, [router]);

  const runAudit = useCallback(async (): Promise<string | null> => {
    const started = await startAudit();
    await refresh();
    return started.ok ? null : started.message;
  }, [refresh]);

  const decide = useCallback(
    async (agent: ConsoleAgent, decision: "approved" | "rejected") => {
      const proposal = agent.live ? currentProposal(agent.live) : undefined;
      if (!agent.live) {
        dispatch({ type: "setStatus", key: agent.key, status: decision });
      } else if (proposal) {
        await decideProposal(proposal.id, decision);
        await refresh();
      }
    },
    [refresh],
  );

  const attachFile = useCallback(
    async (file: File) => {
      dispatch({ type: "closeOverlays" });
      const result = await uploadFile(file);
      if (!result.ok) {
        dispatch({ type: "post", message: { steps: ["upload"], text: result.message } });
        return;
      }
      uploads.current[file.name] = result.value;
      dispatch({ type: "addAttach", item: { name: file.name, kind: "CSV" } });
      await refresh();
    },
    [refresh],
  );

  const send = useCallback(
    (text: string, fresh = false) => {
      const question = text.trim();
      const current = latest.current.state;
      if (!question || current.thinking) return;

      const chat = fixtures.chat;
      const kind = previewKind(question, current.attach, chat.routing);
      const skill = current.attach.find((a) => a.kind === "SKILL")?.name ?? "";
      const files = current.attach
        .map((a) => uploads.current[a.name])
        .filter((report): report is UploadReport => report !== undefined);
      dispatch({
        type: "userSent",
        text: question,
        thinkingText: kind ? thinkingFor(kind, chat.thinking) : "Reading the question…",
        fresh,
      });
      goChat();
      if (kind) {
        // A preview surface: the sample reply, after the prototype's pause.
        timer.current = setTimeout(() => {
          dispatch({
            type: "botReplied",
            message: replyToMessage(chat.replies[kind] ?? chat.replies.none, skill),
          });
        }, THINKING_MS);
      } else if (files.length > 0) {
        dispatch({
          type: "botReplied",
          message: {
            steps: ["ingest"],
            text: `${files.map(uploadSummary).join(" ")} Run the audit to match them against everything else on file.`,
            actionLabel: "Run the audit",
            action: "audit",
          },
        });
      } else {
        void askChat(question, (event) => {
          if (event.event === "step" && typeof event.data.detail === "string") {
            dispatch({ type: "setThinkingText", text: `${event.data.detail}…` });
          }
        }).then((events) => {
          const message = chatMessage(events);
          if (skill) message.steps.unshift(`skill: ${skill}`);
          dispatch({ type: "botReplied", message });
        });
      }
    },
    [fixtures, goChat],
  );

  const runAction = useCallback(
    (action: string, fresh = false) => {
      if (action === "audit") {
        void runAudit().then((problem) =>
          dispatch({
            type: "post",
            message: {
              steps: ["run_audit"],
              text: problem ?? AUDIT_STARTED,
              ...(problem ? {} : { actionLabel: "Review fixes", action: "fixes" }),
            },
          }),
        );
        goChat();
        return;
      }
      const message = actionMessage(
        action,
        latest.current.state,
        fixtures,
        latest.current.pendingKeys,
      );
      if (!message) return;
      dispatch({ type: "post", message, fresh });
      goChat();
    },
    [fixtures, goChat, runAudit],
  );

  const newChat = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    dispatch({ type: "newChat" });
    goChat();
  }, [goChat]);

  const leave = useCallback(async () => {
    await signOut();
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () => ({
      fixtures,
      me,
      leave,
      state,
      dispatch,
      live,
      agents,
      statusOf,
      pendingKeys,
      decide,
      runAudit,
      refresh,
      attachFile,
      send,
      runAction,
      newChat,
    }),
    [
      fixtures,
      me,
      leave,
      state,
      live,
      agents,
      statusOf,
      pendingKeys,
      decide,
      runAudit,
      refresh,
      attachFile,
      send,
      runAction,
      newChat,
    ],
  );

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
