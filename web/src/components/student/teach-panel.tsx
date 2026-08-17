"use client";

import { ClaimTypeBadge, VerdictBadge } from "@/components/shared/badges";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type {
  ReviewDonePayload,
  RetrieveDonePayload,
  TeachDeliveredPayload,
  TypedSessionEvent,
} from "@/lib/types";

type Stage = "retrieve" | "generate" | "review" | "delivered";

const STAGES: { key: Stage; label: string; hint: string }[] = [
  { key: "retrieve", label: "检索", hint: "锚定条目 + FTS5/vec 混合检索" },
  { key: "generate", label: "生成", hint: "论断草稿（挂 evidence_ids）" },
  { key: "review", label: "审核", hint: "NLI 三分类裁决" },
  { key: "delivered", label: "交付", hint: "仅通过审核的论断进入讲义" },
];

function PipelineProgress({ done, active }: { done: Stage[]; active: Stage | null }) {
  return (
    <div className="flex items-center gap-1">
      {STAGES.map((s, i) => {
        const isDone = done.includes(s.key);
        const isActive = active === s.key;
        return (
          <div key={s.key} className="flex items-center gap-1">
            {i > 0 && <div className={cn("h-px w-4", isDone || isActive ? "bg-primary" : "bg-border")} />}
            <span
              title={s.hint}
              className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                isDone && "bg-primary/15 text-primary",
                isActive && "animate-pulse bg-primary text-primary-foreground",
                !isDone && !isActive && "bg-muted text-muted-foreground",
              )}
            >
              {s.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function TeachPanel({
  events,
  delivered,
  isTeaching,
  entryTitle,
}: {
  events: TypedSessionEvent[];
  delivered: TeachDeliveredPayload | null;
  isTeaching: boolean;
  entryTitle: string;
}) {
  const retrieve = events.find((e) => e.event_type === "retrieve_done")?.payload as RetrieveDonePayload | undefined;
  const review = events.findLast((e) => e.event_type === "review_done")?.payload as ReviewDonePayload | undefined;
  const errored = events.find((e) => e.event_type === "error");
  const active: Stage | null = isTeaching
    ? !retrieve
      ? "retrieve"
      : !events.some((e) => e.event_type === "generate_done")
        ? "generate"
        : !review
          ? "review"
          : null
    : null;
  const done: Stage[] = STAGES.filter((s) => {
    if (s.key === "retrieve") return Boolean(retrieve);
    if (s.key === "generate") return events.some((e) => e.event_type === "generate_done");
    if (s.key === "review") return Boolean(review);
    return Boolean(delivered);
  }).map((s) => s.key);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">{entryTitle}</CardTitle>
            <PipelineProgress done={done} active={active} />
          </div>
        </CardHeader>
        {(retrieve || review || errored) && (
          <CardContent className="space-y-2 text-xs text-muted-foreground">
            {retrieve && (
              <div>
                <span className="font-medium text-foreground">检索命中 {retrieve.entries.length} 条：</span>
                {retrieve.entries.map((e) => (
                  <Badge key={e.id} variant="outline" className="mr-1 font-mono text-[10px]">
                    {e.title} · {e.score}
                  </Badge>
                ))}
              </div>
            )}
            {review && (
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">审核：</span>
                <span className="text-green-700">已支持 {review.verdicts.filter((v) => v.verdict === "supported").length}</span>
                <span className="text-amber-700">部分 {review.verdicts.filter((v) => v.verdict === "partially_supported").length}</span>
                <span className="text-red-700">未支持 {review.unsupported_count}</span>
                {review.review_round > 0 && <span>（第 {review.review_round + 1} 轮，打回重写后）</span>}
              </div>
            )}
            {errored && (
              <p className="text-destructive">执行出错：{String((errored.payload as { message?: string }).message ?? "")}</p>
            )}
          </CardContent>
        )}
      </Card>

      {delivered && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">本轮讲义（{delivered.claims.length} 条论断）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {delivered.claims.map((claim) => {
              const verdict = delivered.verdicts[String(claim.claim_index)];
              return (
                <div key={claim.claim_index} className="rounded-md border p-3">
                  <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                    <ClaimTypeBadge claimType={claim.claim_type} />
                    {verdict && <VerdictBadge verdict={verdict} />}
                    <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                      #{claim.claim_index + 1}
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed">{claim.text}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-muted-foreground">证据溯源：</span>
                    {claim.evidence_ids.map((id) => (
                      <Badge key={id} variant="outline" className="font-mono text-[10px]">
                        {id}
                      </Badge>
                    ))}
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
