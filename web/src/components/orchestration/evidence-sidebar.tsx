"use client";

import { ClaimTypeBadge, VerdictBadge, DecisionBadge } from "@/components/shared/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { OrchestrationState } from "@/lib/orchestration-reducer";

export function EvidenceSidebar({ state }: { state: OrchestrationState }) {
  const { lastDelivered, lastReview, lastGraded, lastPackage, currentTopic } = state;
  return (
    <ScrollArea className="h-[560px] rounded-lg border">
      <div className="space-y-3 p-3">
        {currentTopic && (
          <Card>
            <CardHeader className="px-3 py-2">
              <CardTitle className="flex items-center gap-2 text-xs">
                当前主题
                {currentTopic.isRetry && <Badge variant="secondary" className="bg-amber-100 text-[10px] text-amber-800">重教轮</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 pb-2 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">{currentTopic.title}</p>
              <p className="mt-0.5">
                第 {currentTopic.roundNo} 轮 · <span className="font-mono">{currentTopic.entryId}</span>
              </p>
            </CardContent>
          </Card>
        )}

        {lastReview && (
          <Card>
            <CardHeader className="px-3 py-2">
              <CardTitle className="text-xs">
                审核裁决（第 {lastReview.review_round + 1} 轮 · 未支持 {lastReview.unsupported_count}）
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 px-3 pb-2">
              {lastReview.verdicts.map((v) => (
                <div key={v.claim_index} className="rounded border px-2 py-1 text-xs">
                  <div className="flex items-center gap-1.5">
                    <VerdictBadge verdict={v.verdict} />
                    <span className="font-mono text-[10px] text-muted-foreground">#{v.claim_index + 1}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-muted-foreground" title={v.reason}>
                    {v.reason}
                  </p>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {lastDelivered && (
          <Card>
            <CardHeader className="px-3 py-2">
              <CardTitle className="text-xs">证据溯源（交付论断 × 知识条目）</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 px-3 pb-2">
              {lastDelivered.claims.map((c) => (
                <div key={c.claim_index} className="rounded border px-2 py-1 text-xs">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <ClaimTypeBadge claimType={c.claim_type} />
                    {lastDelivered.verdicts[String(c.claim_index)] && (
                      <VerdictBadge verdict={lastDelivered.verdicts[String(c.claim_index)]} />
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2">{c.text}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {c.evidence_ids.map((id) => (
                      <Badge key={id} variant="outline" className="font-mono text-[9px]">
                        {id}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {lastGraded && (
          <Card>
            <CardHeader className="px-3 py-2">
              <CardTitle className="flex items-center gap-2 text-xs">
                判分与决策 <DecisionBadge decision={lastGraded.decision} />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-3 pb-2 text-xs text-muted-foreground">
              <p>
                {lastGraded.is_correct ? "✓ 正确" : "✗ 未通过"} · 覆盖率 {Math.round(lastGraded.coverage * 100)}% ·
                掌握度 {lastGraded.mastery_after.toFixed(2)}
              </p>
              {lastGraded.missed_requirements.length > 0 && (
                <p className="text-amber-700">遗漏要点：{lastGraded.missed_requirements.join("；")}</p>
              )}
            </CardContent>
          </Card>
        )}

        {lastPackage && (
          <Card>
            <CardHeader className="px-3 py-2">
              <CardTitle className="text-xs">资源包沉淀</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-3 pb-2 text-xs text-muted-foreground">
              <p>
                讲义 {lastPackage.lecture_count} 条 · 题目 {lastPackage.question_count} 道
                {lastPackage.has_practice && " · 实操指南"}
                {lastPackage.has_challenge && " · 进阶挑战"}
              </p>
              <p className="font-mono text-[10px]">
                {lastPackage.entry_id} · 层级 {lastPackage.difficulty_tier}
              </p>
            </CardContent>
          </Card>
        )}

        {!currentTopic && !lastReview && !lastDelivered && (
          <p className="p-4 text-center text-xs text-muted-foreground">等待事件流入…</p>
        )}
      </div>
    </ScrollArea>
  );
}
