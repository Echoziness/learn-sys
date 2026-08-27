"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { consumePostSse } from "@/lib/sse";
import type {
  AnswerResponse,
  PlanTopic,
  QuestionResponse,
  SessionDetail,
  TeachDeliveredPayload,
  TopicStateResponse,
  TypedSessionEvent,
} from "@/lib/types";

import { LevelBadge } from "@/components/shared/badges";
import { PageHeader } from "@/components/shared/page-header";
import { FollowupPanel } from "@/components/student/followup-panel";
import { QuestionCard } from "@/components/student/question-card";
import { TeachPanel } from "@/components/student/teach-panel";
import { TopicList, type TopicStatus } from "@/components/student/topic-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

/** 交互阶段：教学 → 出题 → 已判分（等用户继续）→（advance/regress 由驱动器自动推进） */
type Phase = "loading" | "teaching" | "questioning" | "graded" | "ending" | "finished" | "error";

export function SessionWorkbench({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const session = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId),
  });

  const [currentIdx, setCurrentIdx] = useState(0);
  const [phase, setPhase] = useState<Phase>("loading");
  const [topicState, setTopicState] = useState<TopicStateResponse | null>(null);
  const [teachEvents, setTeachEvents] = useState<TypedSessionEvent[]>([]);
  const [delivered, setDelivered] = useState<TeachDeliveredPayload | null>(null);
  const [question, setQuestion] = useState<QuestionResponse | null>(null);
  const [feedback, setFeedback] = useState<AnswerResponse | null>(null);
  const [consolidating, setConsolidating] = useState(false);
  const [statusMap, setStatusMap] = useState<Map<string, TopicStatus>>(new Map());
  const [errorMsg, setErrorMsg] = useState("");
  const startedRef = useRef(false);

  const topics: PlanTopic[] = useMemo(() => session.data?.plan.topics ?? [], [session.data]);
  const currentTopic = topics[currentIdx] ?? null;
  const isFinished = session.data?.status === "finished";

  /** 进入主题：巩固模式判定 →（必要时）教学 SSE → 出题。CLI 循环的 Web 镜像。 */
  const startTopic = useCallback(
    async (entryId: string) => {
      setPhase("loading");
      setTeachEvents([]);
      setDelivered(null);
      setQuestion(null);
      setFeedback(null);
      setConsolidating(false);
      setErrorMsg("");
      setStatusMap((m) => new Map(m).set(entryId, { status: "current", mastery: m.get(entryId)?.mastery ?? null, rounds: m.get(entryId)?.rounds ?? 0 }));

      try {
        const state = await api.topicState(sessionId, entryId);
        setTopicState(state);

        if (state.needs_teaching) {
          setPhase("teaching");
          await consumePostSse(api.teachUrl(sessionId, entryId), {}, {
            onEvent: (ev) => {
              setTeachEvents((prev) => [...prev, ev]);
              if (ev.event_type === "teach_delivered") {
                setDelivered(ev.payload as TeachDeliveredPayload);
              }
            },
            onError: (err) => {
              setErrorMsg(`教学失败：${String((err as Error).message ?? err).slice(0, 200)}`);
              setPhase("error");
            },
          });
          if (errorMsgRef.current) return;
        } else {
          setConsolidating(true);
        }

        setPhase("questioning");
        const q = await api.question(sessionId, entryId);
        setQuestion(q);
      } catch (err) {
        setErrorMsg(String((err as Error).message ?? err).slice(0, 200));
        setPhase("error");
      }
    },
    [sessionId],
  );
  const errorMsgRef = useRef("");
  useEffect(() => {
    errorMsgRef.current = errorMsg;
  }, [errorMsg]);

  /** 首载：会话详情就绪后进入第一个未完成主题 */
  useEffect(() => {
    if (!session.data || startedRef.current || isFinished) return;
    startedRef.current = true;
    const done = new Set<string>();
    void (async () => {
      // 恢复逻辑：找第一个尚未出过题的主题（刷新安全）
      for (let i = 0; i < topics.length; i++) {
        const state = await api.topicState(sessionId, topics[i].entry_id);
        if (!state.has_answered) {
          setCurrentIdx(i);
          void startTopic(topics[i].entry_id);
          return;
        }
        done.add(topics[i].entry_id);
      }
      // 全部主题已作答 → 会话已完成
      const m = new Map<string, TopicStatus>();
      done.forEach((id) => m.set(id, { status: "done", mastery: null, rounds: 0 }));
      setStatusMap(m);
      setPhase("finished");
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.data]);

  /** 作答提交 → 反馈 → 决策驱动 */
  const submitAnswer = useCallback(
    async (answer: string) => {
      if (!currentTopic) return;
      try {
        const result = await api.answer(sessionId, currentTopic.entry_id, answer);
        setFeedback(result);
        setPhase("graded");
        setStatusMap((m) => {
          const next = new Map(m);
          const prev = next.get(currentTopic.entry_id);
          next.set(currentTopic.entry_id, {
            status: result.decision === "advance" ? "done" : result.decision === "regress" ? "regressed" : "current",
            mastery: result.mastery,
            rounds: (prev?.rounds ?? 0) + 1,
          });
          return next;
        });
      } catch (err) {
        setErrorMsg(`判分失败：${String((err as Error).message ?? err).slice(0, 200)}`);
        setPhase("error");
      }
    },
    [currentTopic, sessionId],
  );

  /** 反馈卡"继续"：advance → 下一主题；regress → 前置主题；其余 → 本主题再走一轮 */
  const proceed = useCallback(async () => {
    if (!feedback || !currentTopic) return;
    if (feedback.decision === "advance") {
      const nextIdx = currentIdx + 1;
      if (nextIdx >= topics.length) {
        setPhase("ending");
        try {
          await api.endSession(sessionId);
        } catch {
          /* 会话收尾失败不阻塞报告 */
        }
        setPhase("finished");
        router.push(`/sessions/${sessionId}/report`);
        return;
      }
      setCurrentIdx(nextIdx);
      void startTopic(topics[nextIdx].entry_id);
      return;
    }
    if (feedback.decision === "regress") {
      const prereq = topicState?.prereq_id;
      const prereqIdx = topics.findIndex((t) => t.entry_id === prereq);
      if (prereqIdx >= 0 && prereqIdx < currentIdx) {
        setCurrentIdx(prereqIdx);
        void startTopic(topics[prereqIdx].entry_id);
        return;
      }
      // 无前置可退：服务端仍会推进（轮次上限），留在本主题继续
    }
    void startTopic(currentTopic.entry_id);
  }, [feedback, currentTopic, currentIdx, topics, sessionId, router, startTopic, topicState]);

  const nextLabel = useMemo(() => {
    if (!feedback) return "继续";
    if (feedback.decision === "advance") {
      return currentIdx + 1 >= topics.length ? "完成会话 · 查看报告" : "进入下一主题";
    }
    if (feedback.decision === "regress") return "回前置主题重学";
    if (feedback.is_scaffold && feedback.is_correct) return "回到回答题";
    if (feedback.is_correct) return "下一题（巩固确认）";
    return "重新教学";
  }, [feedback, currentIdx, topics.length]);

  if (session.isPending) {
    return <p className="text-sm text-muted-foreground">加载会话…</p>;
  }
  if (session.isError || !session.data) {
    return <p className="text-sm text-destructive">会话加载失败：{String(session.error?.message ?? "")}</p>;
  }
  if (isFinished && phase !== "finished" && startedRef.current === false) {
    return (
      <div className="mx-auto max-w-xl">
        <Card className="shadow-md">
          <CardContent className="flex flex-col items-center px-8 py-12 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-success/10">
              <svg viewBox="0 0 24 24" className="size-6 text-success" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </div>
            <h1 className="mt-4 text-xl font-semibold tracking-tight">导学会话已完成</h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {session.data.plan?.topics?.length ?? 0} 个主题的教学与检验已全部走完，个性化资源包已沉淀入库。
            </p>
            <div className="mt-6 flex gap-2">
              <Button onClick={() => router.push(`/sessions/${sessionId}/report`)}>查看学情报告</Button>
              <Button variant="outline" onClick={() => router.push(`/sessions/${sessionId}/orchestration`)}>
                裁判面回放
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const detail: SessionDetail = session.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="导学工作台"
        description={`学习者 ${detail.learner_id} · 会话 ${sessionId.slice(0, 8)}… · 课程切片 ${topics.length} 个主题`}
        actions={
          <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-1.5 text-xs shadow-xs">
            <span className="text-muted-foreground">主题进度</span>
            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-foreground transition-all duration-500"
                style={{ width: `${topics.length > 0 ? ((currentIdx + 1) / topics.length) * 100 : 0}%` }}
              />
            </div>
            <span className="font-semibold tabular-nums">
              {currentIdx + 1}/{topics.length}
            </span>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_360px]">
      {/* 左：诊断 + 主题列表（伴随性上下文，视觉重量最轻：背景微差分区，不上卡片描边） */}
      <aside className="space-y-3">
        <div className="rounded-xl bg-muted/40 p-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">学情诊断</h2>
            <LevelBadge level={detail.difficulty_level} />
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{detail.profile_summary}</p>
          {detail.gap_ids.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {detail.gap_ids.map((g) => (
                <span key={g} className="rounded bg-card px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground shadow-xs">
                  {g}
                </span>
              ))}
            </div>
          )}
          <Separator className="my-4" />
          <h2 className="text-sm font-semibold">课程切片（{topics.length} 个主题）</h2>
          <div className="mt-2">
            <TopicList
              topics={topics}
              statuses={statusMap}
              currentEntryId={currentTopic?.entry_id ?? null}
              onSelect={() => {
                /* 教学由决策驱动，列表仅展示 */
              }}
            />
          </div>
        </div>
      </aside>

      {/* 中：教学 */}
      <section className="min-w-0 space-y-4">
        {phase === "loading" && !currentTopic && <p className="text-sm text-muted-foreground">定位教学主题…</p>}
        {currentTopic && (
          <TeachPanel
            events={teachEvents}
            delivered={delivered}
            isTeaching={phase === "teaching"}
            entryTitle={topicState?.title ?? currentTopic.title}
          />
        )}
        {consolidating && question && phase !== "loading" && (
          <p className="rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
            巩固模式：上一题已答对——跳过教学，直接出题确认掌握。
          </p>
        )}
        {phase === "error" && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive">
            {errorMsg}
            <div className="mt-2">
              <Button size="sm" variant="outline" onClick={() => currentTopic && startTopic(currentTopic.entry_id)}>
                重试本主题
              </Button>
            </div>
          </div>
        )}
      </section>

      {/* 右：答题卡 + 追问 */}
      <section className="min-w-0 space-y-4">
        {question && (phase === "questioning" || phase === "graded") && (
          <QuestionCard
            question={question}
            feedback={feedback}
            submitting={false}
            onSubmit={submitAnswer}
            onNext={proceed}
            nextLabel={nextLabel}
          />
        )}
        {currentTopic && (phase === "questioning" || phase === "graded") && (
          <FollowupPanel
            sessionId={sessionId}
            entryId={currentTopic.entry_id}
            last={topicState?.followup_last ?? null}
          />
        )}
        {phase === "teaching" && (
          <p className="rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
            教学进行中（检索 → 生成 → 审核，约 10-30s）…
          </p>
        )}
        {phase === "ending" && (
          <p className="rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground">会话收尾中，正在生成报告…</p>
        )}
      </section>
      </div>
    </div>
  );
}
