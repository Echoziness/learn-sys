"use client";

/**
 * 资源库：跨会话聚合展示所有会话产出的资源包与条目化导出条目。
 * 会话被删除但产物选择保留时，此处仍可浏览（来源标注"会话已删除"）。
 * 渲染复用报告页同款组件（shared/resource-views）。
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AggregatedExportEntry, AggregatedPackage } from "@/lib/types";
import { ExportedEntryList, PackageBrowser } from "@/components/shared/resource-views";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const STATUS_LABEL: Record<string, string> = {
  active: "进行中",
  finished: "已完成",
  aborted: "已中止",
};

/** 来源会话元信息：会话被删后保留的产物标注"会话已删除" */
function SessionMeta({
  sessionId,
  learnerId,
  status,
}: {
  sessionId: string;
  learnerId?: string | null;
  status: string | null;
}) {
  return (
    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="font-mono">会话 {sessionId.slice(0, 8)}…</span>
      {learnerId && <Badge variant="outline" className="text-[10px]">{learnerId}</Badge>}
      <Badge variant="secondary" className="text-[10px]">
        {status === null ? "会话已删除（产物保留）" : (STATUS_LABEL[status] ?? status)}
      </Badge>
    </div>
  );
}

export default function ResourcesPage() {
  const [sessionFilter, setSessionFilter] = useState("all");
  const [entryFilter, setEntryFilter] = useState("");
  // 资源包嵌套 Tabs 渲染成本高（95 包×多 tab），默认收起 + 分块展开避免主线程阻塞
  const [packagesExpanded, setPackagesExpanded] = useState(false);
  const [packagesVisible, setPackagesVisible] = useState(10);

  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.listSessions(200) });
  const library = useQuery({
    queryKey: ["resource-library", sessionFilter, entryFilter],
    queryFn: () =>
      api.allResources({
        session_id: sessionFilter === "all" ? undefined : sessionFilter,
        entry_id: entryFilter.trim() || undefined,
      }),
  });

  // 筛选下拉项：现存会话 + 数据中出现但会话已删的孤儿来源
  const sessionOptions = useMemo(() => {
    const list = (sessions.data ?? []).map((s) => ({ session_id: s.session_id, learner_id: s.learner_id }));
    const known = new Set(list.map((s) => s.session_id));
    const orphans: { session_id: string; learner_id: string }[] = [];
    for (const p of library.data?.packages ?? []) {
      if (!known.has(p.session_id)) {
        known.add(p.session_id);
        orphans.push({ session_id: p.session_id, learner_id: p.learner_id });
      }
    }
    for (const e of library.data?.exports ?? []) {
      if (!known.has(e.session_id)) {
        known.add(e.session_id);
        orphans.push({ session_id: e.session_id, learner_id: e.learner_id ?? "" });
      }
    }
    return [...list, ...orphans];
  }, [sessions.data, library.data]);

  const groupExports = (entries: AggregatedExportEntry[]) => {
    const map = new Map<string, AggregatedExportEntry[]>();
    for (const e of entries) map.set(e.session_id, [...(map.get(e.session_id) ?? []), e]);
    return map;
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="资源库"
        description="跨会话聚合的资源包（教学沉淀中间产物）与条目化导出条目（可复用资源包本体）"
        actions={
          <>
            {library.data && (
              <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-1.5 text-xs shadow-xs">
                <span className="text-muted-foreground">
                  资源包 <span className="font-semibold text-foreground tabular-nums">{library.data.packages.length}</span>
                </span>
                <span className="h-3 w-px bg-border" aria-hidden />
                <span className="text-muted-foreground">
                  导出条目 <span className="font-semibold text-foreground tabular-nums">{library.data.exports.length}</span>
                </span>
              </div>
            )}
            <Button variant="outline" size="sm" onClick={() => void library.refetch()}>
              刷新
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Select value={sessionFilter} onValueChange={setSessionFilter}>
          <SelectTrigger className="w-64">
            <SelectValue placeholder="按来源会话筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部会话</SelectItem>
            {sessionOptions.map((s) => (
              <SelectItem key={s.session_id} value={s.session_id}>
                {s.learner_id || "（未知学习者）"} · {s.session_id.slice(0, 8)}…
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="w-64"
          placeholder="条目 ID（源条目或生成条目）"
          value={entryFilter}
          onChange={(e) => setEntryFilter(e.target.value)}
        />
        {(sessionFilter !== "all" || entryFilter) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSessionFilter("all");
              setEntryFilter("");
            }}
          >
            清除筛选
          </Button>
        )}
      </div>

      <Card>
        <CardContent className="py-2">
          {library.isPending ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : library.isError ? (
            <p className="p-4 text-sm text-destructive">
              加载失败：{String(library.error?.message ?? "").slice(0, 200)}
            </p>
          ) : (
            <Tabs defaultValue="packages">
              <TabsList className="mt-2 h-8">
                <TabsTrigger value="packages" className="text-xs">
                  资源包（{library.data.packages.length}）
                </TabsTrigger>
                <TabsTrigger value="exports" className="text-xs">
                  导出条目（{library.data.exports.length}）
                </TabsTrigger>
              </TabsList>
              <TabsContent value="packages" className="space-y-3 pt-3">
                {library.data.packages.length === 0 ? (
                  <p className="p-6 text-center text-sm text-muted-foreground">暂无资源包</p>
                ) : !packagesExpanded ? (
                  <div className="space-y-2 p-4 text-center">
                    <p className="text-sm text-muted-foreground">
                      共 {library.data.packages.length} 个资源包，每个含讲义/分阶题等多形态嵌套视图（展开渲染较重，分块加载）。
                    </p>
                    <Button size="sm" onClick={() => setPackagesExpanded(true)}>
                      展开资源包列表
                    </Button>
                  </div>
                ) : (
                  <>
                    {library.data.packages.slice(0, packagesVisible).map((p) => (
                      <div key={`${p.session_id}-${p.entry_id}`}>
                        <SessionMeta sessionId={p.session_id} learnerId={p.learner_id} status={p.session_status} />
                        <PackageBrowser packages={[p as AggregatedPackage]} />
                      </div>
                    ))}
                    {packagesVisible < library.data.packages.length && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => setPackagesVisible((v) => v + 10)}
                      >
                        加载更多（已显示 {Math.min(packagesVisible, library.data.packages.length)}/
                        {library.data.packages.length}）
                      </Button>
                    )}
                  </>
                )}
              </TabsContent>
              <TabsContent value="exports" className="space-y-3 pt-3">
                {library.data.exports.length === 0 ? (
                  <p className="p-6 text-center text-sm text-muted-foreground">
                    暂无导出条目（会话完成后经条目化导出产出）
                  </p>
                ) : (
                  [...groupExports(library.data.exports).entries()].map(([sid, entries]) => (
                    <div key={sid}>
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                        <SessionMeta
                          sessionId={sid}
                          learnerId={entries[0]?.learner_id}
                          status={entries[0]?.session_status ?? null}
                        />
                        <a
                          href={api.exportDownloadUrl(sid)}
                          download
                          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
                        >
                          下载 entries.jsonl
                        </a>
                      </div>
                      <ExportedEntryList entries={entries} />
                    </div>
                  ))
                )}
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
