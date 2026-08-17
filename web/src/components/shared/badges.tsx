import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { ClaimType, Decision, Verdict } from "@/lib/types";

const CLAIM_META: Record<string, { label: string; className: string }> = {
  core: { label: "core · 条目覆盖", className: "bg-blue-100 text-blue-800" },
  extension: { label: "extension · 错因扩展", className: "bg-amber-100 text-amber-800" },
  procedure_guide: { label: "procedure · 实操指南", className: "bg-cyan-100 text-cyan-800" },
};

export function ClaimTypeBadge({ claimType }: { claimType: ClaimType | string }) {
  const meta = CLAIM_META[claimType] ?? { label: claimType, className: "bg-gray-100 text-gray-700" };
  return <Badge variant="secondary" className={cn("font-mono text-[10px]", meta.className)}>{meta.label}</Badge>;
}

const VERDICT_META: Record<string, { label: string; className: string }> = {
  supported: { label: "已支持", className: "bg-green-100 text-green-800" },
  partially_supported: { label: "部分支持", className: "bg-amber-100 text-amber-800" },
  unsupported: { label: "未支持", className: "bg-red-100 text-red-800" },
};

export function VerdictBadge({ verdict }: { verdict: Verdict | string }) {
  const meta = VERDICT_META[verdict] ?? { label: verdict, className: "bg-gray-100 text-gray-700" };
  return <Badge variant="secondary" className={cn("text-[10px]", meta.className)}>{meta.label}</Badge>;
}

const DECISION_META: Record<string, { label: string; className: string; detail: string }> = {
  advance: {
    label: "advance · 推进",
    className: "bg-green-100 text-green-800",
    detail: "掌握达标，沉淀资源包并进入下一主题",
  },
  retry: {
    label: "retry · 重教",
    className: "bg-amber-100 text-amber-800",
    detail: "针对薄弱点重新教学（错因回流注入生成 Agent）",
  },
  regress: {
    label: "regress · 回退",
    className: "bg-orange-100 text-orange-800",
    detail: "连续答错，回到前置主题降维重学",
  },
  scaffold: {
    label: "scaffold · 脚手架",
    className: "bg-violet-100 text-violet-800",
    detail: "先做一道镜像错误理解的过渡选择题",
  },
};

export function DecisionBadge({ decision }: { decision: Decision | string }) {
  const meta = DECISION_META[decision] ?? { label: decision, className: "bg-gray-100 text-gray-700", detail: "" };
  return <Badge variant="secondary" className={cn("text-[10px]", meta.className)}>{meta.label}</Badge>;
}

export function decisionDetail(decision: Decision | string): string {
  return DECISION_META[decision]?.detail ?? "";
}

const LEVEL_LABEL: Record<string, string> = {
  beginner: "入门",
  intermediate: "进阶",
  advanced: "高级",
};

export function LevelBadge({ level }: { level: string | null | undefined }) {
  if (!level) return null;
  return <Badge variant="outline" className="text-[10px]">{LEVEL_LABEL[level] ?? level}</Badge>;
}
