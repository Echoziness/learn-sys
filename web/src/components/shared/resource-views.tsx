"use client";

/**
 * 资源展示共享组件：报告页（单会话）与资源库页（跨会话聚合）复用。
 * PackageBrowser = 资源包三形态浏览（讲义/分阶题/实操指南/进阶挑战）；
 * ExportedEntryList = 知识库同构条目卡片（可复用资源包的本体）。
 */

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { ExportedEntry, ResourcePackage } from "@/lib/types";

/** 资源包三形态浏览（报告页与资源库页共用） */
export function PackageBrowser({ packages }: { packages: ResourcePackage[] }) {
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
                      <li key={i}>
                        {s.text}
                        {s.evidence_ids.length > 0 &&
                          s.evidence_ids.map((id) => (
                            <Badge key={id} variant="outline" className="ml-1 font-mono text-[9px]">{id}</Badge>
                          ))}
                      </li>
                    ))}
                  </ol>
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

/** 条目化导出产物：知识库同构条目卡片（可复用资源包的本体，与资源包中间产物对照） */
export function ExportedEntryList({ entries }: { entries: ExportedEntry[] }) {
  if (entries.length === 0) {
    return <p className="p-6 text-center text-sm text-muted-foreground">暂无导出条目（会话完成后经条目化导出产出）</p>;
  }
  return (
    <div className="space-y-4">
      {entries.map((e) => (
        <Card key={e.id}>
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-sm">{e.title}</CardTitle>
              <Badge variant="outline" className="font-mono text-[10px]">{e.id}</Badge>
              <Badge variant="outline" className="text-[10px]">{e.knowledge_type}</Badge>
              <Badge variant="outline" className="text-[10px]">难度 {e.difficulty}</Badge>
              {e.source_entry_id && (
                <Badge variant="secondary" className="font-mono text-[10px]">源 {e.source_entry_id}</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {e.content.split("\n\n").map((para, i) => (
              <p key={i} className="text-sm leading-relaxed">{para}</p>
            ))}
            <Separator />
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-xs text-muted-foreground">关键词</span>
              {e.keywords.map((k) => (
                <Badge key={k} variant="outline" className="text-[10px]">{k}</Badge>
              ))}
            </div>
            {e.prerequisites.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-xs text-muted-foreground">前置</span>
                {e.prerequisites.map((p) => (
                  <Badge key={p} variant="outline" className="font-mono text-[10px]">{p}</Badge>
                ))}
              </div>
            )}
            <p className="text-xs text-muted-foreground">溯源：{e.source}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
