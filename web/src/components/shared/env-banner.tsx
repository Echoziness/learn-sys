"use client";

import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";

import { api } from "@/lib/api";

/**
 * 环境状态横幅（降级启动提示，2026-08-30）：
 * 后端按环境探测分级装配，缺向量模型 / 缺 LLM 配置 / 缺数据库时照常启动——
 * 横幅展示"缺什么 + 哪些功能受影响 + 怎么补"，回放与报告不受影响。
 * 环境完整时不渲染。
 */
export function EnvBanner() {
  const status = useQuery({ queryKey: ["env-status"], queryFn: api.getStatus });
  const missing = status.data?.missing ?? [];
  if (missing.length === 0) return null;
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900">
      <p className="flex items-center gap-2 text-sm font-medium">
        <TriangleAlert className="size-4 shrink-0" />
        环境组件不完整——回放与报告功能照常可用，以下功能受限
      </p>
      <ul className="mt-1.5 list-disc space-y-0.5 pl-6 text-xs text-amber-800">
        {missing.map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
    </div>
  );
}
