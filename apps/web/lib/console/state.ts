import type { Attachment, Fixtures, Reply, ReplyBlock, Status } from "../fixtures";

export type UserMessage = {
  id: number;
  role: "user";
  text: string;
  files: Attachment[];
};

export type BotMessage = {
  id: number;
  role: "bot";
  text: string;
  steps: string[];
  block?: ReplyBlock;
  rows?: string[][];
  fixes?: string[];
  agentKey?: string;
  source?: string;
  actionLabel?: string;
  action?: string;
  /** A canned reply from sample data, shown with a "Preview" pill. */
  preview?: boolean;
  /** One bar per row of a live answer split one way. */
  bars?: { label: string; display: string; share: number }[];
};

export type Message = UserMessage | BotMessage;
export type BotDraft = Omit<BotMessage, "id" | "role">;

export type ConsoleState = {
  status: Record<string, Status>;
  autonomy: Record<string, number>;
  messages: Message[];
  nextId: number;
  draft: string;
  thinking: boolean;
  thinkingText: string;
  plusOpen: boolean;
  skillsOpen: boolean;
  attach: Attachment[];
  notifOpen: boolean;
  unread: boolean;
  routineSaved: boolean;
  skillSaved: boolean;
  paused: Record<string, boolean>;
  ran: Record<string, boolean>;
  wfSimulated: boolean;
  wfPublished: boolean;
  mcpUrl: string;
  customAdded: boolean;
  editKey: string;
  agentSimulated: boolean;
  pinned: boolean;
  hover: boolean;
};

export type Action =
  | { type: "setStatus"; key: string; status: Status }
  | { type: "setAutonomy"; key: string; level: number }
  | { type: "userSent"; text: string; thinkingText: string; fresh?: boolean }
  | { type: "botReplied"; message: BotDraft }
  | { type: "setThinkingText"; text: string }
  | { type: "post"; message: BotDraft; fresh?: boolean }
  | { type: "newChat" }
  | { type: "setDraft"; draft: string }
  | { type: "startRoutine"; draft: string }
  | { type: "togglePlus" }
  | { type: "toggleSkills" }
  | { type: "addAttach"; item: Attachment }
  | { type: "removeAttach"; name: string }
  | { type: "toggleNotif" }
  | { type: "markRead" }
  | { type: "closeOverlays" }
  | { type: "saveRoutine" }
  | { type: "saveSkill" }
  | { type: "togglePaused"; id: string }
  | { type: "runRoutine"; id: string }
  | { type: "simulateWf" }
  | { type: "publishWf" }
  | { type: "setMcpUrl"; url: string }
  | { type: "connectMcp" }
  | { type: "toggleEdit"; key: string }
  | { type: "simulateAgent" }
  | { type: "togglePin" }
  | { type: "setPinned"; pinned: boolean }
  | { type: "setHover"; hover: boolean };

export function initialState(fixtures: Fixtures): ConsoleState {
  return {
    status: Object.fromEntries(fixtures.agents.map((a) => [a.key, a.initial_status])),
    autonomy: Object.fromEntries(
      fixtures.agents.map((a) => [a.key, a.initial_autonomy]),
    ),
    messages: [],
    nextId: 1,
    draft: "",
    thinking: false,
    thinkingText: "",
    plusOpen: false,
    skillsOpen: false,
    attach: [],
    notifOpen: false,
    unread: true,
    routineSaved: false,
    skillSaved: false,
    paused: {},
    ran: {},
    wfSimulated: false,
    wfPublished: false,
    mcpUrl: fixtures.custom_mcp.default_url,
    customAdded: false,
    editKey: "",
    agentSimulated: false,
    pinned: true,
    hover: false,
  };
}

export function replyToMessage(reply: Reply, skill: string): BotDraft {
  return {
    text: reply.text,
    steps: [...(skill ? [`skill: ${skill}`] : []), ...reply.steps],
    block: reply.block,
    rows: reply.rows,
    agentKey: reply.agent_key,
    source: reply.source,
    actionLabel: reply.action_label,
    action: reply.action,
    preview: true,
  };
}

/** What a reply's action button posts into the chat. */
export function actionMessage(
  action: string,
  state: ConsoleState,
  fixtures: Fixtures,
  /** Agents waiting for a decision. Defaults to the sample data's own state. */
  pendingKeys?: string[],
): BotDraft | null {
  if (action === "fixes") {
    const keys =
      pendingKeys ??
      fixtures.agents.filter((a) => state.status[a.key] === "pending").map((a) => a.key);
    const copy = fixtures.chat.actions.fixes;
    return {
      steps: [copy.step],
      fixes: keys,
      text: keys.length ? copy.text : copy.empty_text,
    };
  }
  if (action.startsWith("card:")) {
    const key = action.slice("card:".length);
    return {
      steps: [`get_proposal(${key})`],
      agentKey: key,
      text: fixtures.chat.actions.card.text,
    };
  }
  return null;
}

export function reducer(state: ConsoleState, action: Action): ConsoleState {
  switch (action.type) {
    case "setStatus":
      return { ...state, status: { ...state.status, [action.key]: action.status } };
    case "setAutonomy":
      return {
        ...state,
        autonomy: { ...state.autonomy, [action.key]: action.level },
      };
    case "userSent": {
      const earlier = action.fresh ? [] : state.messages;
      return {
        ...state,
        messages: [
          ...earlier,
          {
            id: state.nextId,
            role: "user",
            text: action.text,
            files: state.attach.filter((a) => a.kind !== "SKILL"),
          },
        ],
        nextId: state.nextId + 1,
        draft: "",
        attach: [],
        plusOpen: false,
        skillsOpen: false,
        thinking: true,
        thinkingText: action.thinkingText,
        notifOpen: false,
        // The first message of a conversation folds the sidebar away.
        pinned: earlier.length === 0 ? false : state.pinned,
      };
    }
    case "botReplied":
      return {
        ...state,
        messages: [
          ...state.messages,
          { ...action.message, id: state.nextId, role: "bot" },
        ],
        nextId: state.nextId + 1,
        thinking: false,
      };
    case "setThinkingText":
      return state.thinking ? { ...state, thinkingText: action.text } : state;
    case "post":
      return {
        ...state,
        messages: [
          ...(action.fresh ? [] : state.messages),
          { ...action.message, id: state.nextId, role: "bot" },
        ],
        nextId: state.nextId + 1,
      };
    case "newChat":
      return {
        ...state,
        messages: [],
        attach: [],
        plusOpen: false,
        notifOpen: false,
        thinking: false,
      };
    case "setDraft":
      return { ...state, draft: action.draft };
    case "startRoutine":
      return { ...state, plusOpen: false, draft: action.draft };
    case "togglePlus":
      return { ...state, plusOpen: !state.plusOpen, skillsOpen: false };
    case "toggleSkills":
      return { ...state, skillsOpen: !state.skillsOpen };
    case "addAttach":
      return {
        ...state,
        attach: [
          ...state.attach.filter((a) => a.name !== action.item.name),
          action.item,
        ],
        plusOpen: false,
        skillsOpen: false,
      };
    case "removeAttach":
      return {
        ...state,
        attach: state.attach.filter((a) => a.name !== action.name),
      };
    case "toggleNotif":
      return { ...state, notifOpen: !state.notifOpen };
    case "markRead":
      return { ...state, notifOpen: false, unread: false };
    case "closeOverlays":
      return { ...state, plusOpen: false, skillsOpen: false, notifOpen: false };
    case "saveRoutine":
      return { ...state, routineSaved: true };
    case "saveSkill":
      return { ...state, skillSaved: true };
    case "togglePaused":
      return {
        ...state,
        paused: { ...state.paused, [action.id]: !state.paused[action.id] },
      };
    case "runRoutine":
      return { ...state, ran: { ...state.ran, [action.id]: true } };
    case "simulateWf":
      return { ...state, wfSimulated: true };
    case "publishWf":
      return { ...state, wfPublished: true };
    case "setMcpUrl":
      return { ...state, mcpUrl: action.url };
    case "connectMcp":
      return { ...state, customAdded: true };
    case "toggleEdit":
      return {
        ...state,
        editKey: state.editKey === action.key ? "" : action.key,
        agentSimulated: false,
      };
    case "simulateAgent":
      return { ...state, agentSimulated: true };
    case "togglePin":
      return { ...state, pinned: !state.pinned, hover: false };
    case "setPinned":
      return { ...state, pinned: action.pinned };
    case "setHover":
      return state.pinned && action.hover ? state : { ...state, hover: action.hover };
  }
}
