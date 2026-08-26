"use client";

import { useCallback, useEffect, useState } from "react";

import { ClaimTypeBadge, VerdictBadge } from "@/components/shared/badges";
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

/** 讲义轮播：拟物化卡牌堆叠——当前论断为主卡，底下微露下一张卡边缘作堆叠暗示。
 * 切换通道：键盘 ←/→ + 点击主卡左右边缘区（无显式箭头按钮）。 */
function ClaimStack({ delivered }: { delivered: TeachDeliveredPayload }) {
  const claims = delivered.claims;
  const total = claims.length;
  const [idx, setIdx] = useState(0);
  const [dir, setDir] = useState<"next" | "prev">("next");
  const safeIdx = Math.min(idx, Math.max(total - 1, 0));

  // 新一轮讲义（重教/换主题）→ 回到第一张
  useEffect(() => {
    setIdx(0);
  }, [delivered]);

  const goto = useCallback(
    (next: number, direction: "next" | "prev") => {
      if (next < 0 || next >= total) return;
      setDir(direction);
      setIdx(next);
    },
    [total],
  );

  // 键盘通道：焦点在输入控件内时让位（不与右栏作答 textarea 的光标移动冲突）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const el = e.target as HTMLElement | null;
      if (el && el.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "ArrowLeft") goto(safeIdx - 1, "prev");
      else goto(safeIdx + 1, "next");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goto, safeIdx]);

  if (total === 0) return null;
  const claim = claims[safeIdx];
  const verdict = delivered.verdicts[String(claim.claim_index)];

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">本轮讲义</h3>
        {/* 页码指示弱化：小字号低对比，仅作辅助信息 */}
        <span className="text-[11px] tabular-nums text-muted-foreground/60">
          {safeIdx + 1} / {total} · 仅审核通过项
        </span>
      </div>

      <div className="relative mt-3 min-h-[260px]">
        {/* 内层容器高度 = 主卡实际高度（随论断文字浮动）：暗示卡锚定它而非外层，
            无论主卡多高都稳定微露 ~8px；外层 min-h 只负责预留高度预算稳定布局 */}
        <div className="relative">
          {/* 下一张卡的堆叠暗示：下方微露 ~8px 错开边缘，纯视觉线索不可交互；最后一张不显示 */}
          {safeIdx < total - 1 && (
            <div aria-hidden className="absolute inset-x-3 top-3 -bottom-2 rounded-xl border bg-card/60 shadow-xs" />
          )}

          {/* 主卡：key 变化触发横向位移 + 透明度渐变（350ms ease-out） */}
          <div
            key={claim.claim_index}
            className={cn(
              "relative z-10 flex max-h-[400px] flex-col rounded-xl border bg-card p-5 shadow-sm",
              dir === "next" ? "animate-in fade-in slide-in-from-right-4" : "animate-in fade-in slide-in-from-left-4",
            )}
            style={{ animationDuration: "350ms", animationTimingFunction: "ease-out" }}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <ClaimTypeBadge claimType={claim.claim_type} />
              {verdict && <VerdictBadge verdict={verdict} />}
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                #{claim.claim_index + 1}
              </span>
            </div>
            <p className="mt-3 overflow-y-auto text-[15px] leading-relaxed">{claim.text}</p>
            <div className="mt-3 flex flex-wrap items-center gap-1">
              <span className="text-[10px] text-muted-foreground">证据溯源：</span>
              {claim.evidence_ids.map((id) => (
                <Badge key={id} variant="outline" className="font-mono text-[10px]">
                  {id}
                </Badge>
              ))}
            </div>

            {/* 边缘点击区：默认隐形，hover 时浮现渐变高亮提示可点击 */}
            {safeIdx > 0 && (
              <button
                type="button"
                aria-label="上一条论断"
                onClick={() => goto(safeIdx - 1, "prev")}
                className="absolute inset-y-0 left-0 w-16 cursor-pointer rounded-l-xl bg-gradient-to-r from-foreground/6 to-transparent opacity-0 transition-opacity duration-200 hover:opacity-100"
              />
            )}
            {safeIdx < total - 1 && (
              <button
                type="button"
                aria-label="下一条论断"
                onClick={() => goto(safeIdx + 1, "next")}
                className="absolute inset-y-0 right-0 w-16 cursor-pointer rounded-r-xl bg-gradient-to-l from-foreground/6 to-transparent opacity-0 transition-opacity duration-200 hover:opacity-100"
              />
            )}
          </div>
        </div>
      </div>
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
    <div className="space-y-6">
      {/* 主题头：裸内容不包卡片——中栏是阅读区，视觉重量让位给右栏作答焦点 */}
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold tracking-tight">{entryTitle}</h2>
          <PipelineProgress done={done} active={active} />
        </div>
        {(retrieve || review || errored) && (
          <div className="mt-3 space-y-2 border-t border-border/70 pt-3 text-xs text-muted-foreground">
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
          </div>
        )}
      </div>

      {delivered && <ClaimStack delivered={delivered} />}
    </div>
  );
}
