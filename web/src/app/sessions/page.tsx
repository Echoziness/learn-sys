"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { SessionListItem } from "@/lib/types";
import { LevelBadge } from "@/components/shared/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_META: Record<string, { label: string; className: string }> = {
  active: { label: "进行中", className: "bg-blue-100 text-blue-800" },
  finished: { label: "已完成", className: "bg-green-100 text-green-800" },
  aborted: { label: "已中止", className: "bg-gray-100 text-gray-600" },
};

function ActionLinks({ s }: { s: SessionListItem }) {
  const link = "inline-flex h-7 items-center rounded-md px-2 text-xs hover:bg-muted";
  return (
    <div className="flex justify-end gap-1">
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
            <TableRow key={s.session_id}>
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
                <ActionLinks s={s} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export default function SessionsPage() {
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(200),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">历史会话</h1>
          <p className="text-sm text-muted-foreground">裁判面回放 / 学情报告入口（无需 LLM key）</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void sessions.refetch()}>
          刷新
        </Button>
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
              <Link href="/" className="underline">
                新建一个
              </Link>
            </p>
          ) : (
            <SessionTable data={sessions.data} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
