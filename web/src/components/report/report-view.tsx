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
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { LevelBadge } from "@/components/shared/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { ReportResponse, ResourcePackage } from "@/lib/types";

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

function PackageBrowser({ packages }: { packages: ResourcePackage[] }) {
  if (packages.length === 0) {
    return <p className="p-6 text-center text-sm text-muted-foreground">暂无资源包（需完成主题教学后沉淀）</p>;
  }
  return (
    <div className="space-y-4">
      {packages.map((p) => (
        <Card key={p.entry_id}>
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="font-mono text-sm">{p.entry_id}</CardTitle>
              <Badge variant="outline" className="text-[10px]">层级 {p.difficulty_tier}</Badge>
              {p.challenge && <Badge variant="secondary" className="bg-violet-100 text-[10px] text-violet-800">进阶挑战</Badge>}
              {p.practice && <Badge variant="secondary" className="bg-cyan-100 text-[10px] text-cyan-800">实操指南</Badge>}
            </div>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="lecture">
              <TabsList className="h-8">
                <TabsTrigger value="lecture" className="text-xs">讲义（{p.lecture.length}）</TabsTrigger>
                <TabsTrigger value="questions" className="text-xs">分阶题（{p.questions.length}）</TabsTrigger>
                {p.practice && <TabsTrigger value="practice" className="text-xs">实操指南</TabsTrigger>}
                {p.challenge && <TabsTrigger value="challenge" className="text-xs">进阶挑战</TabsTrigger>}
              </TabsList>
              <TabsContent value="lecture" className="space-y-2 pt-2">
                {p.lecture.map((c, i) => (
                  <div key={i} className="rounded-md border p-2 text-sm">
                    <div className="mb-1 flex flex-wrap items-center gap-1">
                      <Badge variant="outline" className="text-[9px] font-mono">{c.claim_type}</Badge>
                      <span className="text-[10px] text-muted-foreground">第 {c.round} 轮</span>
                      <span className="ml-auto flex gap-1">
                        {c.evidence_ids.map((id) => (
                          <Badge key={id} variant="outline" className="font-mono text-[9px]">{id}</Badge>
                        ))}
                      </span>
                    </div>
                    <p className="leading-relaxed">{c.text}</p>
                  </div>
                ))}
              </TabsContent>
              <TabsContent value="questions" className="space-y-2 pt-2">
                {p.questions.map((q) => (
                  <div key={q.question_id} className="rounded-md border p-2 text-sm">
                    <div className="mb-1 flex items-center gap-2">
                      <Badge variant="outline" className="text-[9px] font-mono">{q.type}</Badge>
                      <span className="font-mono text-[10px] text-muted-foreground">{q.question_id}</span>
                    </div>
                    <p>{q.prompt}</p>
                    {q.options && (
                      <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                        {q.options.map((o) => (
                          <li key={o}>{o}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </TabsContent>
              {p.practice && (
                <TabsContent value="practice" className="space-y-2 pt-2 text-sm">
                  <p className="font-medium">步骤</p>
                  <ol className="list-inside list-decimal space-y-1 text-muted-foreground">
                    {p.practice.steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                  {p.practice.example && (
                    <>
                      <Separator />
                      <pre className="overflow-x-auto rounded bg-muted p-2 font-mono text-xs">{p.practice.example}</pre>
                    </>
                  )}
                  {p.practice.checkpoints && (
                    <>
                      <Separator />
                      <p className="font-medium">检查点</p>
                      <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                        {p.practice.checkpoints.map((c, i) => (
                          <li key={i}>{c}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </TabsContent>
              )}
              {p.challenge && (
                <TabsContent value="challenge" className="pt-2 text-sm">
                  <p className="font-medium">{p.challenge.title}</p>
                  {p.challenge.description && <p className="mt-1 text-muted-foreground">{p.challenge.description}</p>}
                </TabsContent>
              )}
            </Tabs>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function ReportView({ sessionId }: { sessionId: string }) {
  const report = useQuery<ReportResponse, Error>({
    queryKey: ["report", sessionId],
    queryFn: () => api.report(sessionId),
  });
  const resources = useQuery<ResourcePackage[], Error>({
    queryKey: ["resources", sessionId],
    queryFn: () => api.resources(sessionId),
  });

  if (report.isPending) return <Skeleton className="h-96 w-full" />;
  const r = report.data;
  if (report.isError || r === null || r === undefined) {
    return <p className="text-sm text-destructive">报告加载失败：{String(report.error?.message ?? "").slice(0, 200)}</p>;
  }
  const radarData = r.radar.map((x) => ({ subject: x.title.slice(0, 8), mastery: x.mastery }));
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
            <CardDescription className="text-xs">逐主题掌握度（门槛 0.7）</CardDescription>
          </CardHeader>
          <CardContent>
            {radarData.length >= 3 ? (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData}>
                    <PolarGrid />
                    <PolarAngleAxis dataKey="subject" tick={{ fontSize: 10 }} />
                    <Radar dataKey="mastery" stroke="#2563eb" fill="#2563eb" fillOpacity={0.35} />
                    <Tooltip />
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
            <CardDescription className="text-xs">资源层级与诊断层级匹配率（目标 ≥85%）</CardDescription>
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
    </div>
  );
}
