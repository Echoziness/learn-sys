/**
 * API 响应与会话事件 payload 的 TS 类型。
 * 事实源：api/models.py（REST 响应）+ core/teach_loop.py emit 调用（事件 payload，
 * 协议见 docs/架构设计文档.md §4）。expected 判分要点永不进学生视野——类型层同样不定义。
 */

// ---------- 通用 ----------

export type DifficultyLevel = "beginner" | "intermediate" | "advanced";
export type ClaimType = "core" | "extension" | "procedure_guide";
export type Verdict = "supported" | "partially_supported" | "unsupported";
export type Decision = "advance" | "retry" | "regress" | "scaffold";
export type QuestionType = "choice" | "answer" | "scaffold";

export interface ProfileBackground {
  education: string;
  major: string;
  goal: string;
  experience: string;
}

export interface LearnerProfileInput {
  learner_id: string;
  background: ProfileBackground;
  style_tags: string[];
  mastery: Record<string, number>;
  /** 教学领域（seeds 子目录名；多域库自选，缺省默认域） */
  domain?: string;
}

// ---------- REST 响应 ----------

/** 可选教学领域（GET /api/sessions/domains） */
export interface Domain {
  id: string;
  entry_count: number;
}

export interface PlanTopic {
  entry_id: string;
  title: string;
  order: number;
  target: boolean;
}

export interface CreateSessionResponse {
  session_id: string;
  learner_id: string;
  difficulty_level: DifficultyLevel | string;
  profile_summary: string;
  gap_ids: string[];
  topics: PlanTopic[];
  uncovered_gaps: string[];
}

export interface QuestionResponse {
  question_id: string;
  entry_id: string;
  question_type: QuestionType;
  prompt: string;
  options: string[];
}

export interface AnswerResponse {
  is_correct: boolean;
  coverage: number;
  evaluation: string;
  missed_requirements: string[];
  decision: Decision;
  mastery: number;
  round_no: number;
  is_scaffold: boolean;
}

/** 动态追问判定结果（POST /topics/{entry}/followup）：无效只有理由，有效携带困惑解答 */
export interface FollowupAskResponse {
  valid: boolean;
  reason: string;
  round_no: number;
  /** 针对疑问的直接解答（已记录，下一轮教学针对性强化） */
  answer: string;
}

/** 导出触发结果（POST /api/sessions/{id}/export） */
export interface ExportTriggerResponse {
  session_id: string;
  count: number;
  entry_ids: string[];
}

export interface SessionListItem {
  session_id: string;
  learner_id: string;
  difficulty_level: DifficultyLevel | string | null;
  status: string;
  created_at: string;
  finished_at: string | null;
  topic_count: number;
  event_count: number;
  package_count: number;
}

export interface SessionDetail {
  session_id: string;
  learner_id: string;
  profile: { background?: ProfileBackground; style_tags?: string[]; mastery?: Record<string, number> };
  gap_ids: string[];
  difficulty_level: DifficultyLevel | string | null;
  profile_summary: string | null;
  plan: { topics?: PlanTopic[] };
  status: string;
  created_at: string;
  finished_at: string | null;
}

export interface RadarItem {
  entry_id: string;
  title: string;
  mastery: number;
  attempts: number;
}

export interface TierMatch {
  matched: number;
  total: number;
}

/** 逐包难度层级明细（报告页徽章展示用） */
export interface TierDetail {
  entry_id: string;
  title: string;
  tier: string;
  matched: boolean;
}

/** 赛题三指标总览（与 evals/run.py 逐组结果同口径，SSOT 在 evals/metrics.py） */
export interface ReportMetrics {
  hallucination_rate: number;
  claims_total: number;
  tier_match: { rate: number; matched: number; total: number };
  keyword_coverage: { rate: number; hit: number; total: number };
}

export interface ReportResponse {
  session_id: string;
  difficulty_level: DifficultyLevel | string | null;
  radar: RadarItem[];
  tier_match: TierMatch;
  tiers: TierDetail[];
  metrics: ReportMetrics;
  path: PlanTopic[];
  regressions: { entry_id: string; prereq_id: string | null; reason: string }[];
}

export interface LectureClaim {
  text: string;
  evidence_ids: string[];
  claim_type: ClaimType | string;
  round: number;
}

export interface ArchivedQuestion {
  question_id: string;
  type: QuestionType | string;
  prompt: string;
  options?: string[];
  round: number;
}

export interface PracticeStep {
  text: string;
  evidence_ids: string[];
}

export interface PracticeGuide {
  steps: PracticeStep[];
  checkpoints?: string[];
}

export interface ResourcePackage {
  session_id: string;
  learner_id: string;
  entry_id: string;
  lecture: LectureClaim[];
  questions: ArchivedQuestion[];
  practice: PracticeGuide | null;
  challenge: { title: string; description?: string } | null;
  difficulty_tier: string;
  created_at: string;
}

/** 条目化导出产物（知识库同构条目，FR-23）：字段与 entries.jsonl 同构 + 溯源与导出时间 */
export interface ExportedEntry {
  id: string;
  source_entry_id: string;
  knowledge_type: "memory" | "concept" | "procedure" | string;
  title: string;
  content: string;
  prerequisites: string[];
  difficulty: number;
  keywords: string[];
  source: string;
  exported_at: string;
}

/** 删除会话响应（DELETE /api/sessions/{id}） */
export interface DeleteSessionResponse {
  session_id: string;
  deleted: Record<string, number>;
  kept_packages: boolean;
  kept_exports: boolean;
}

/** 跨会话聚合的资源包（资源库页面）：包字段 + 来源会话状态（会话已删时为 null） */
export interface AggregatedPackage extends ResourcePackage {
  session_status: string | null;
}

/** 跨会话聚合的导出条目：条目字段 + 来源会话信息 */
export interface AggregatedExportEntry extends ExportedEntry {
  session_id: string;
  learner_id?: string | null;
  session_status: string | null;
}

/** 资源库聚合响应（GET /api/resources） */
export interface ResourceLibraryResponse {
  packages: AggregatedPackage[];
  exports: AggregatedExportEntry[];
}

/** 主题进度状态（GET /topics/{entry}/state）——学生面驱动循环与刷新恢复的依据 */
export interface TopicStateResponse {
  entry_id: string;
  title: string;
  needs_teaching: boolean;
  next_round_no: number;
  scaffold_pending: boolean;
  prereq_id: string | null;
  has_answered: boolean;
  /** 最近一条困惑记录（刷新恢复展示用；无则 null） */
  followup_last: {
    round_no: number;
    question: string;
    answer: string;
  } | null;
  /** 尚未被教学消化的困惑数（>0 时下一轮强制教学） */
  pending_followup_count: number;
}

// ---------- 会话事件（协议 17 类 + packages_exported + error）----------

export interface SessionEvent<T = Record<string, unknown>> {
  seq?: number;
  event_type: string;
  payload: T;
}

export interface SessionStartPayload {
  learner_id: string;
  profile: { background?: ProfileBackground; style_tags?: string[]; mastery?: Record<string, number> };
}

export interface DiagnoseDonePayload {
  gap_ids: string[];
  gaps: string[];
  difficulty_level: DifficultyLevel | string;
  summary: string;
}

export interface PlanDonePayload {
  topics: PlanTopic[];
}

export interface TopicStartPayload {
  entry_id: string;
  title: string;
  round_no: number;
  is_retry: boolean;
}

export interface RetrieveDonePayload {
  entry_id: string;
  round_no: number;
  entries: { id: string; title: string; score: number }[];
  uncovered: string[];
}

export interface GenerateDonePayload {
  entry_id: string;
  round_no: number;
  claims_count: number;
  cited: string[];
}

export interface ReviewDonePayload {
  entry_id: string;
  round_no: number;
  verdicts: { claim_index: number; verdict: Verdict | string; reason: string }[];
  unsupported_count: number;
  review_round: number;
}

export interface DeliveredClaim {
  claim_index: number;
  text: string;
  evidence_ids: string[];
  claim_type: ClaimType | string;
}

export interface TeachDeliveredPayload {
  entry_id: string;
  round_no: number;
  claims: DeliveredClaim[];
  verdicts: Record<string, Verdict | string>;
}

export interface QuestionBuiltPayload {
  entry_id: string;
  round_no: number;
  question_id: string;
  question_type: QuestionType;
  prompt: string;
  options: string[];
}

export interface AnswerGradedPayload {
  entry_id: string;
  round_no: number;
  question_id: string;
  is_scaffold: boolean;
  is_correct: boolean;
  coverage: number;
  evaluation: string;
  missed_requirements: string[];
  decision: Decision;
  mastery_after: number;
}

export interface ScaffoldOfferedPayload {
  entry_id: string;
  round_no: number;
  mirror: string;
}

export interface FollowupAskedPayload {
  entry_id: string;
  round_no: number;
  student_question: string;
  valid: boolean;
  reason: string;
}

export interface FollowupOfferedPayload {
  entry_id: string;
  round_no: number;
  question_id: string;
  prompt: string;
  options: string[];
}

export interface FollowupGradedPayload {
  entry_id: string;
  round_no: number;
  question_id: string;
  is_correct: boolean;
  correct_label: string;
  evaluation: string;
}

export interface TopicAdvancePayload {
  entry_id: string;
  mastery: number;
  reached_gate: boolean;
}

export interface TopicRegressPayload {
  entry_id: string;
  prereq_id: string | null;
  reason: string;
}

export interface PackageSavedPayload {
  entry_id: string;
  lecture_count: number;
  question_count: number;
  has_practice: boolean;
  has_challenge: boolean;
  difficulty_tier: string;
}

export interface SessionEndPayload {
  topics_taught: number;
  packages: string[];
}

export interface ErrorPayload {
  stage: string;
  message: string;
}

export type SessionEventPayload =
  | SessionStartPayload
  | DiagnoseDonePayload
  | PlanDonePayload
  | TopicStartPayload
  | RetrieveDonePayload
  | GenerateDonePayload
  | ReviewDonePayload
  | TeachDeliveredPayload
  | QuestionBuiltPayload
  | AnswerGradedPayload
  | ScaffoldOfferedPayload
  | FollowupAskedPayload
  | FollowupOfferedPayload
  | FollowupGradedPayload
  | TopicAdvancePayload
  | TopicRegressPayload
  | PackageSavedPayload
  | SessionEndPayload
  | ErrorPayload;

/** 带 seq 与类型的完整事件（回放/实时统一形状） */
export interface TypedSessionEvent {
  seq: number;
  event_type: string;
  payload: SessionEventPayload;
  received_at?: number;
}
