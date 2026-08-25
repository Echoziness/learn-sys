"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { LevelBadge } from "@/components/shared/badges";
import { ExportedEntryList, PackageBrowser } from "@/components/shared/resource-views";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ExportedEntry, ReportResponse, ResourcePackage } from "@/lib/types";

/** 学习路径：水平步进 + regress 回退标注（视频分镜用） */
function PathChart({ topics, regressions }: { topics: { entry_id: string; title: string; order: number; target: boolean }[]; regressions: { entry_id: string; prereq_id: string | null; reason: string }[] }) {
  const regressByEntry = useMemo(() => {
    const m = new Map<string, { prereq_id: string | null; reason: string }>();
    for (const r of regressions) m.set(r.entry_id, r);
    return m;
  }, [regressions]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1">
        {topics.map((t, i) => {
          const reg = regressByEntry.get(t.entry_id);
          return (
            <div key={t.entry_id} className="flex items-center gap-1">
              {i > 0 && <div className="h-px w-4 bg-border" />}
              <div
                title={`${t.title}${reg ? `（曾回退 → ${reg.prereq_id ?? "无"}）` : ""}`}
                className={`rounded-md border px-2 py-1 text-xs ${
                  reg ? "border-orange-400 bg-orange-50" : t.target ? "border-primary/40 bg-primary/5" : "border-border"
                }`}
              >
                <span className="text-muted-foreground">{i + 1}.</span> {t.title.slice(0, 8)}
                {reg && <span className="ml-1 text-orange-600">↩</span>}
                {!t.target && <Badge variant="outline" className="ml-1 text-[9px]">前置</Badge>}
              </div>
            </div>
          );
        })}
      </div>
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

export function ReportView({ sessionId }: { sessionId: string }) {
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">学情报告</h1>
          <p className="text-sm text-muted-foreground">
            会话 <span className="font-mono">{sessionId.slice(0, 8)}…</span>
          </p>
        </div>
        <LevelBadge level={r.difficulty_level} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">知识盲区雷达</CardTitle>
            <CardDescription className="text-xs">逐主题掌握度（门槛 0.7 为图中虚线参照，悬停看原值）</CardDescription>
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
            <CardDescription className="text-xs">资源层级与诊断层级匹配率</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-2 text-3xl font-bold tabular-nums">{tierRate}%</div>
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={r.radar.map((x, i) => ({ name: `${i + 1}`, mastery: x.mastery, attempts: x.attempts }))}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="mastery" name="掌握度" stroke="#2563eb" dot />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="text-xs text-muted-foreground">
              匹配 {r.tier_match.matched}/{r.tier_match.total} 个资源包
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">学习路径</CardTitle>
            <CardDescription className="text-xs">切片推进 + regress 回退</CardDescription>
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
        <h2 className="mb-1 text-lg font-semibold tracking-tight">知识库同构条目（可复用资源包）</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          资源包经条目化导出的最终形态——与知识库 entries.jsonl 同构（含误区提炼），可被原样入库复用；上方讲义/分阶题是其生产中间产物。
        </p>
        {exported.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : exported.data ? (
          <ExportedEntryList entries={exported.data} />
        ) : null}
      </div>
    </div>
  );
}
