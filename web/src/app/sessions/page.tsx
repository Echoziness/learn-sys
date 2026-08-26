"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { SessionListItem } from "@/lib/types";
import { LevelBadge } from "@/components/shared/badges";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_META: Record<string, { label: string; className: string }> = {
  active: { label: "进行中", className: "bg-sky-50 text-sky-700" },
  finished: { label: "已完成", className: "bg-success/10 text-success" },
  aborted: { label: "已中止", className: "bg-muted text-muted-foreground" },
};

function ActionLinks({ s, children }: { s: SessionListItem; children?: React.ReactNode }) {
  const link = "inline-flex h-7 items-center rounded-md px-2 text-xs hover:bg-muted";
  return (
    <div className="flex flex-wrap justify-end gap-1">
      {s.status === "active" && (
        <Link href={`/sessions/${s.session_id}`} className={link}>
          继续
        </Link>
      )}
      <Link href={`/sessions/${s.session_id}/orchestration?mode=replay`} className={`${link} border`}>
        回放
      </Link>
      <Link href={`/sessions/${s.session_id}/report`} className={link}>
        报告
      </Link>
      {children}
    </div>
  );
}

/** 删除会话两步确认：先选保留策略再确认，防误删；过程数据（事件/轮次/掌握度）必删，
 * 资源包与导出条目可选择额外保留（孤儿产物仍在资源库聚合展示） */
function DeleteControl({ s }: { s: SessionListItem }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [keep, setKeep] = useState("none");
  const del = useMutation({
    mutationFn: () =>
      api.deleteSession(s.session_id, { keep_packages: keep !== "none", keep_exports: keep !== "none" }),
    onSuccess: () => {
      setConfirming(false);
      setKeep("none");
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      void queryClient.invalidateQueries({ queryKey: ["resource-library"] });
    },
  });

  if (!confirming) {
    return (
      <button
        type="button"
        className="inline-flex h-7 items-center rounded-md px-2 text-xs text-destructive hover:bg-destructive/10"
        onClick={() => setConfirming(true)}
      >
        删除…
      </button>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-end gap-1">
      <Select value={keep} onValueChange={setKeep}>
        <SelectTrigger className="h-7 w-36 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none">全部删除</SelectItem>
          <SelectItem value="keep">保留资源包与导出条目</SelectItem>
        </SelectContent>
      </Select>
      <Button
        size="sm"
        variant="destructive"
        className="h-7 text-xs"
        disabled={del.isPending}
        title={`删除会话 ${s.learner_id}（${s.session_id.slice(0, 8)}…）：事件/轮次/掌握度等过程数据一并删除${
          keep !== "none" ? "，资源包与导出条目额外保留" : ""
        }`}
        onClick={() => del.mutate()}
      >
        {del.isPending ? "删除中…" : "确认删除"}
      </Button>
      <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setConfirming(false)}>
        取消
      </Button>
      {del.isError && (
        <p className="w-full text-right text-[10px] text-destructive">{String(del.error?.message ?? "").slice(0, 60)}</p>
      )}
    </div>
  );
}

function SessionTable({ data }: { data: SessionListItem[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>学习者</TableHead>
          <TableHead>难度</TableHead>
          <TableHead>状态</TableHead>
          <TableHead className="text-right">主题</TableHead>
          <TableHead className="text-right">事件</TableHead>
          <TableHead className="text-right">资源包</TableHead>
          <TableHead>创建时间</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((s) => {
          const st = STATUS_META[s.status] ?? { label: s.status, className: "" };
          return (
            <TableRow key={s.session_id} className="transition-colors hover:bg-muted/50 [&>td]:py-3">
              <TableCell className="font-medium">{s.learner_id}</TableCell>
              <TableCell>
                <LevelBadge level={s.difficulty_level} />
              </TableCell>
              <TableCell>
                <Badge variant="secondary" className={`text-[10px] ${st.className}`}>
                  {st.label}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{s.topic_count}</TableCell>
              <TableCell className="text-right tabular-nums">{s.event_count}</TableCell>
              <TableCell className="text-right tabular-nums">{s.package_count}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {s.created_at.slice(0, 16).replace("T", " ")}
              </TableCell>
              <TableCell className="text-right">
                <ActionLinks s={s}>
                  <DeleteControl s={s} />
                </ActionLinks>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export default function SessionsPage() {
  const [statusFilter, setStatusFilter] = useState("all");
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(200),
  });

  const all = sessions.data ?? [];
  const counts = {
    active: all.filter((s) => s.status === "active").length,
    finished: all.filter((s) => s.status === "finished").length,
  };
  const filtered = statusFilter === "all" ? all : all.filter((s) => s.status === statusFilter);

  return (
    <div className="space-y-6">
      <PageHeader
        title="历史会话"
        description="裁判面回放 / 学情报告入口（无需 LLM key）；删除会话时资源包与导出条目可额外保留"
        actions={
          <>
            <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-1.5 text-xs shadow-xs">
              <span className="text-muted-foreground">
                共 <span className="font-semibold text-foreground tabular-nums">{all.length}</span> 个会话
              </span>
              <span className="h-3 w-px bg-border" aria-hidden />
              <span className="text-muted-foreground">
                进行中 <span className="font-semibold text-sky-700 tabular-nums">{counts.active}</span>
              </span>
              <span className="text-muted-foreground">
                已完成 <span className="font-semibold text-success tabular-nums">{counts.finished}</span>
              </span>
            </div>
            <Button variant="outline" size="sm" onClick={() => void sessions.refetch()}>
              刷新
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="状态筛选" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="active">进行中</SelectItem>
            <SelectItem value="finished">已完成</SelectItem>
            <SelectItem value="aborted">已中止</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="py-2">
          {sessions.isPending ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : sessions.isError ? (
            <p className="p-4 text-sm text-destructive">
              加载失败：{String(sessions.error?.message ?? "").slice(0, 200)}
            </p>
          ) : sessions.data.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">
              暂无会话。
              <Link href="/sessions/new" className="underline">
                新建一个
              </Link>
            </p>
          ) : filtered.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">该状态下暂无会话</p>
          ) : (
            <SessionTable data={filtered} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
