"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PathFlow } from "@/components/report/path-flow";
import { LevelBadge } from "@/components/shared/badges";
import { PageHeader } from "@/components/shared/page-header";
import { ExportedEntryList, PackageBrowser } from "@/components/shared/resource-views";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ExportedEntry, ReportResponse, ResourcePackage } from "@/lib/types";

/** 学习路径：有向图（节点=主题，灰边=推进，橙虚线=连错回退）+ 回退明细 */
function PathChart({ topics, regressions }: { topics: { entry_id: string; title: string; order: number; target: boolean }[]; regressions: { entry_id: string; prereq_id: string | null; reason: string }[] }) {
  return (
    <div className="space-y-3">
      <PathFlow topics={topics} regressions={regressions} />
      {regressions.length > 0 ? (
        <div className="space-y-1 text-xs text-orange-700">
          {regressions.map((r, i) => (
            <p key={i}>
              ↩ {r.entry_id} 连续答错 → 回退前置 {r.prereq_id ?? "（无，标记未达标继续）"}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">无回退——全程正向推进</p>
      )}
    </div>
  );
}

/** 掌握度门槛（数值事实源在 core/mastery.py，此处仅展示层常量保持一致） */
const MASTERY_GATE = 0.7;

/** 雷达图展示层映射：以门槛为视觉中点——原始 0~1 轴下 0.7 达标只占半径 70%，
 * 视觉上显得"远未掌握"；压缩为 [0,0.7]→[0,0.5]、[0.7,1]→[0.5,1] 后达标值居中、
 * 达标后差异按比例放大；Tooltip 仍展示原始掌握度。 */
function visualMastery(mastery: number): number {
  if (mastery <= MASTERY_GATE) return (mastery / MASTERY_GATE) * 0.5;
  return 0.5 + ((mastery - MASTERY_GATE) / (1 - MASTERY_GATE)) * 0.5;
}

const MASTERY_GATE_VISUAL = visualMastery(MASTERY_GATE);

/** 指标卡：赛题目标值达标与否用状态徽章明示，数字大而不喽——排版节奏：标签/大数/口径注释 */
function MetricTile({ label, value, ok, note }: { label: string; value: string; ok: boolean; note: string }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm transition-shadow duration-200 hover:shadow-md">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <Badge
          variant="outline"
          className={cn(
            "border-transparent text-[10px]",
            ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"
          )}
        >
          {ok ? "达标" : "未达标"}
        </Badge>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">{value}</p>
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{note}</p>
    </div>
  );
}

/** 三指标总览：口径与批量评测（evals/run.py）逐组结果完全一致，SSOT 在 evals/metrics.py */
function MetricsOverview({ r }: { r: ReportResponse }) {
  const m = r.metrics;
  return (
    <section>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold tracking-tight">赛题三指标（本会话）</h2>
        <p className="text-xs text-muted-foreground">
          口径与批量评测逐组结果一致（单一事实源在评测指标模块）：幻觉率目标 &lt;5%，其余两项 ≥85% / ≥90%
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <MetricTile
          label="无溯源幻觉率"
          value={`${(m.hallucination_rate * 100).toFixed(1)}%`}
          ok={m.hallucination_rate < 0.05}
          note={`最终裁决 unsupported 论断 / 全部交付论断（${m.claims_total} 条），审核回流后的交付质量`}
        />
        <MetricTile
          label="画像-资源适配率"
          value={`${(m.tier_match.rate * 100).toFixed(1)}%`}
          ok={m.tier_match.rate >= 0.85}
          note={`资源层级落在诊断层级容忍带（上限+1）内 ${m.tier_match.matched}/${m.tier_match.total} 包`}
        />
        <MetricTile
          label="知识点覆盖率"
          value={`${(m.keyword_coverage.rate * 100).toFixed(1)}%`}
          ok={m.keyword_coverage.rate >= 0.9}
          note={`目标条目关键词在讲义中命中 ${m.keyword_coverage.hit}/${m.keyword_coverage.total} 个`}
        />
      </div>
    </section>
  );
}

export function ReportView({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient();
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState("");
  const [exportErr, setExportErr] = useState("");

  const report = useQuery<ReportResponse, Error>({
    queryKey: ["report", sessionId],
    queryFn: () => api.report(sessionId),
  });
  const resources = useQuery<ResourcePackage[], Error>({
    queryKey: ["resources", sessionId],
    queryFn: () => api.resources(sessionId),
  });
  const exported = useQuery<ExportedEntry[], Error>({
    queryKey: ["exports", sessionId],
    queryFn: () => api.exports(sessionId),
  });

  if (report.isPending) return <Skeleton className="h-96 w-full" />;
  const r = report.data;
  if (report.isError || r === null || r === undefined) {
    return <p className="text-sm text-destructive">报告加载失败：{String(report.error?.message ?? "").slice(0, 200)}</p>;
  }
  const radarData = r.radar.map((x) => ({
    subject: x.title.slice(0, 8),
    mastery: visualMastery(x.mastery),
    gate: MASTERY_GATE_VISUAL,
    raw: x.mastery,
  }));
  const tierRate = r.tier_match.total > 0 ? Math.round((r.tier_match.matched / r.tier_match.total) * 100) : 0;

  /** 主动触发条目化导出（含误区提炼，走 LLM 管线）→ 成功后刷新导出条目列表 */
  const triggerExport = async () => {
    setExporting(true);
    setExportMsg("");
    setExportErr("");
    try {
      const res = await api.triggerExport(sessionId);
      await queryClient.invalidateQueries({ queryKey: ["exports", sessionId] });
      setExportMsg(`导出成功：产出 ${res.count} 条知识库同构条目，可直接下载入库。`);
    } catch (err) {
      setExportErr(`导出失败：${String((err as Error).message ?? err).slice(0, 200)}`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="学情报告"
        description={
          <>
            会话 <span className="font-mono">{sessionId.slice(0, 8)}…</span> ·
            多智能体导学全程的学情画像与个性化产出
          </>
        }
        actions={<LevelBadge level={r.difficulty_level} />}
      />

      <MetricsOverview r={r} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">知识盲区雷达</CardTitle>
            <CardDescription className="text-xs">
              逐主题作答掌握度（蓝色区域）；虚线为达标门槛 0.7 的参照位，悬停看原始值。
              掌握度由历次作答按新近加权累积（口径在掌握度模块），追问/脚手架不计入。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {radarData.length >= 3 ? (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData}>
                    <PolarGrid />
                    <PolarAngleAxis dataKey="subject" tick={{ fontSize: 10 }} />
                    {/* 显式半径域：防自动域把映射后的 0~1 值压缩到中心 */}
                    <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
                    <Radar name="gate" dataKey="gate" stroke="#94a3b8" fill="none" strokeDasharray="4 2" />
                    <Radar name="mastery" dataKey="mastery" stroke="#2563eb" fill="#2563eb" fillOpacity={0.35} />
                    <Tooltip
                      formatter={(value, name, item) =>
                        name === "mastery"
                          ? [`${(item?.payload as { raw?: number })?.raw?.toFixed(2) ?? value}`, "掌握度"]
                          : [`${MASTERY_GATE}`, "门槛"]
                      }
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="space-y-1 p-2 text-sm">
                {r.radar.map((x) => (
                  <div key={x.entry_id} className="flex justify-between">
                    <span>{x.title}</span>
                    <span className="tabular-nums">{x.mastery.toFixed(2)}</span>
                  </div>
                ))}
                {r.radar.length === 0 && <p className="text-muted-foreground">暂无作答记录</p>}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">难度匹配</CardTitle>
            <CardDescription className="text-xs">
              各资源包难度层级与诊断层级对照：层级容忍带 = 诊断上限 + 1
              （诊断层级为单次推断，带 ±1 不确定带；前置链拉入的相邻难度条目属正常教学），超出才记失配。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {r.tier_match.total > 0 ? (
              <>
                <div className="mb-2 text-3xl font-bold tabular-nums">
                  {tierRate}%
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    匹配 {r.tier_match.matched}/{r.tier_match.total} 个资源包
                  </span>
                </div>
                <div className="space-y-1.5">
                  {r.tiers.map((t) => (
                    <div key={t.entry_id} className="flex items-center gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate" title={t.title}>{t.title}</span>
                      <Badge
                        variant="outline"
                        className={t.matched ? "border-green-300 bg-green-50 text-green-700" : "border-orange-300 bg-orange-50 text-orange-700"}
                      >
                        {t.tier}{t.matched ? " · 匹配" : " · 超出容忍带"}
                      </Badge>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="p-4 text-center text-sm text-muted-foreground">
                暂无资源包（完成至少一个主题的教学后产出）
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">学习路径</CardTitle>
            <CardDescription className="text-xs">
              课程切片顺序推进；连续答错触发回退前置主题（降维重教）。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PathChart topics={r.path} regressions={r.regressions} />
          </CardContent>
        </Card>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-semibold tracking-tight">个性化资源包（{resources.data?.length ?? 0} 个主题）</h2>
        {resources.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : resources.data ? (
          <PackageBrowser packages={resources.data} />
        ) : null}
      </div>

      <div>
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold tracking-tight">知识库同构条目（可复用资源包）</h2>
          <div className="flex items-center gap-2">
            {exported.data && exported.data.length > 0 && (
              <a
                href={api.exportDownloadUrl(sessionId)}
                download
                className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
              >
                下载 entries.jsonl
              </a>
            )}
            <Button size="sm" onClick={() => void triggerExport()} disabled={exporting}>
              {exporting ? "导出中…（误区提炼约 10-30s）" : "触发条目化导出"}
            </Button>
          </div>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          资源包经条目化导出的最终形态——与知识库 entries.jsonl 同构（讲义 supported 论断 +
          错题/脚手架原料提炼的误区知识），可被原样入库复用（&ldquo;系统生产的资源喂回系统&rdquo;）；
          上方讲义/分阶题是其生产中间产物。支持会话进行中随时导出，重跑会重新提炼。
        </p>
        {exportMsg && <p className="mb-2 text-xs text-green-700">{exportMsg}</p>}
        {exportErr && <p className="mb-2 text-xs text-destructive">{exportErr}</p>}
        {exported.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : exported.data ? (
          <ExportedEntryList entries={exported.data} />
        ) : null}
      </div>
    </div>
  );
}
