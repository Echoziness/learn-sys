/**
 * 事件流 → 裁判面节点状态的归约（纯函数）。
 * 节点：diagnose / plan / retrieve / generate / review / deliver / question / answer / decision。
 * topic_start 开启新一轮：教学子图（retrieve→generate→review→deliver）状态重置。
 */

import type {
  AnswerGradedPayload,
  PackageSavedPayload,
  ReviewDonePayload,
  TeachDeliveredPayload,
  TopicRegressPayload,
  TypedSessionEvent,
} from "@/lib/types";

export type AgentKey =
  | "diagnose"
  | "plan"
  | "retrieve"
  | "generate"
  | "review"
  | "deliver"
  | "question"
  | "answer"
  | "decision";

export type AgentStatus = "idle" | "active" | "done" | "warn";

export interface AgentNodeState {
  status: AgentStatus;
  badge?: string;
}

export type AgentStates = Record<AgentKey, AgentNodeState>;

export const INITIAL_AGENT_STATES: AgentStates = {
  diagnose: { status: "idle" },
  plan: { status: "idle" },
  retrieve: { status: "idle" },
  generate: { status: "idle" },
  review: { status: "idle" },
  deliver: { status: "idle" },
  question: { status: "idle" },
  answer: { status: "idle" },
  decision: { status: "idle" },
};

/** 打回回路高亮窗口：review 裁决 unsupported≥2 时 generate⇄review 边进入回流态 */
export interface FlowEdgesState {
  rewriteLoop: boolean;
  retryLoop: boolean;
  regressLoop: boolean;
  advanceForward: boolean;
}

export const INITIAL_FLOW_EDGES: FlowEdgesState = {
  rewriteLoop: false,
  retryLoop: false,
  regressLoop: false,
  advanceForward: false,
};

export interface OrchestrationState {
  agents: AgentStates;
  edges: FlowEdgesState;
  /** 当前轮次信息（topic_start 更新） */
  currentTopic: { entryId: string; title: string; roundNo: number; isRetry: boolean } | null;
  /** 最后一次审核（侧栏裁决列表） */
  lastReview: ReviewDonePayload | null;
  /** 最后一次交付（侧栏溯源） */
  lastDelivered: TeachDeliveredPayload | null;
  /** 最后一次判分（侧栏决策） */
  lastGraded: AnswerGradedPayload | null;
  lastPackage: PackageSavedPayload | null;
  lastRegress: TopicRegressPayload | null;
  /** 事件计数（标题栏活跃信号） */
  eventCount: number;
}

export const INITIAL_ORCHESTRATION: OrchestrationState = {
  agents: INITIAL_AGENT_STATES,
  edges: INITIAL_FLOW_EDGES,
  currentTopic: null,
  lastReview: null,
  lastDelivered: null,
  lastGraded: null,
  lastPackage: null,
  lastRegress: null,
  eventCount: 0,
};

function resetTeachingSubgraph(agents: AgentStates): AgentStates {
  return {
    ...agents,
    retrieve: { status: "idle" },
    generate: { status: "idle" },
    review: { status: "idle" },
    deliver: { status: "idle" },
  };
}

export function reduceOrchestration(prev: OrchestrationState, ev: TypedSessionEvent): OrchestrationState {
  const agents = { ...prev.agents };
  const edges = { ...prev.edges };
  let { currentTopic, lastReview, lastDelivered, lastGraded, lastPackage, lastRegress } = prev;

  switch (ev.event_type) {
    case "session_start":
      break;
    case "diagnose_done":
      agents.diagnose = { status: "done" };
      break;
    case "plan_done":
      agents.plan = { status: "done" };
      break;
    case "topic_start": {
      const p = ev.payload as { entry_id: string; title: string; round_no: number; is_retry: boolean };
      currentTopic = { entryId: p.entry_id, title: p.title, roundNo: p.round_no, isRetry: p.is_retry };
      // 新一轮：教学子图重置；打回/回流标记清零（新一轮重新计）
      Object.assign(agents, resetTeachingSubgraph(agents));
      agents.retrieve = { status: "active" };
      edges.rewriteLoop = false;
      edges.retryLoop = false;
      break;
    }
    case "retrieve_done": {
      const p = ev.payload as { entries: unknown[]; uncovered: string[] };
      agents.retrieve = {
        status: "done",
        badge: p.uncovered.length > 0 ? `未覆盖 ${p.uncovered.length}` : undefined,
      };
      agents.generate = { status: "active" };
      break;
    }
    case "generate_done": {
      const p = ev.payload as { claims_count: number; cited: string[] };
      agents.generate = { status: "done", badge: `${p.claims_count} 论断` };
      agents.review = { status: "active" };
      break;
    }
    case "review_done": {
      const p = ev.payload as ReviewDonePayload;
      lastReview = p;
      // 打回回流：unsupported ≥ 阈值且 review_round 深入（事件序列上 review 完成后若紧接
      // generate_done 则是回流重写——以 review_round>0 且后续 generate_done 为准，
      // 此处先记录裁决，回边由 review_round 与 unsupported 共同显示）
      agents.review = {
        status: p.unsupported_count > 0 ? "warn" : "done",
        badge: p.unsupported_count > 0 ? `未支持 ${p.unsupported_count}` : "通过",
      };
      if (p.review_round > 0) edges.rewriteLoop = true;
      break;
    }
    case "teach_delivered": {
      lastDelivered = ev.payload as TeachDeliveredPayload;
      agents.deliver = { status: "done", badge: `${lastDelivered.claims.length} 论断交付` };
      agents.question = { status: "active" };
      break;
    }
    case "question_built":
      agents.question = { status: "done" };
      agents.answer = { status: "active" };
      break;
    case "scaffold_offered":
      agents.question = { status: "done", badge: "脚手架" };
      agents.answer = { status: "active" };
      break;
    case "answer_graded": {
      const p = ev.payload as AnswerGradedPayload;
      lastGraded = p;
      agents.answer = { status: p.is_correct ? "done" : "warn", badge: p.is_correct ? "正确" : "未通过" };
      agents.decision = { status: "active", badge: p.decision };
      // 决策回边标记
      edges.advanceForward = p.decision === "advance";
      edges.retryLoop = p.decision === "retry";
      edges.regressLoop = p.decision === "regress";
      break;
    }
    case "topic_advance":
      agents.decision = { status: "done", badge: "advance" };
      break;
    case "topic_regress":
      lastRegress = ev.payload as TopicRegressPayload;
      agents.decision = { status: "done", badge: "regress" };
      break;
    case "package_saved": {
      const p = ev.payload as PackageSavedPayload;
      lastPackage = p;
      break;
    }
    case "session_end":
      break;
    case "error":
      break;
  }

  return {
    agents,
    edges,
    currentTopic,
    lastReview,
    lastDelivered,
    lastGraded,
    lastPackage,
    lastRegress,
    eventCount: prev.eventCount + 1,
  };
}

/** 逐条重放事件得到最终状态（回放模式直接用；实时模式逐条 fold） */
export function foldEvents(events: TypedSessionEvent[], upto: number): OrchestrationState {
  let state = INITIAL_ORCHESTRATION;
  for (const ev of events) {
    if (ev.seq !== undefined && ev.seq > upto) break;
    state = reduceOrchestration(state, ev);
  }
  return state;
}
