"use client";

import { useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { cn } from "@/lib/utils";
import type { AgentStates, FlowEdgesState } from "@/lib/orchestration-reducer";

/** Agent 节点自定义类型：标签 + 状态点 + 角标 + 角色说明 */
type AgentNodeData = {
  label: string;
  role: string;
  state: AgentStates[keyof AgentStates];
};

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  const { label, role, state } = data;
  return (
    <div
      className={cn(
        "min-w-[130px] rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-all",
        state.status === "idle" && "border-border opacity-60",
        state.status === "active" && "border-primary shadow-md",
        state.status === "done" && "border-green-600",
        state.status === "warn" && "border-amber-500",
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2" />
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            state.status === "idle" && "bg-muted-foreground/40",
            state.status === "active" && "animate-pulse bg-primary",
            state.status === "done" && "bg-green-600",
            state.status === "warn" && "bg-amber-500",
          )}
        />
        <span className="text-sm font-semibold">{label}</span>
        {state.badge && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{state.badge}</span>
        )}
      </div>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{role}</p>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2" />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

/** 静态拓扑坐标（三层泳道：诊断切片 / 教学子图 / 检验决策） */
function buildNodes(agents: AgentStates): Node[] {
  const defs: { key: keyof AgentStates; label: string; role: string; x: number; y: number }[] = [
    { key: "diagnose", label: "诊断 Agent", role: "画像 → 盲区 + 难度", x: 0, y: 0 },
    { key: "plan", label: "课程切片", role: "前置链闭包 + 拓扑排序", x: 220, y: 0 },
    { key: "retrieve", label: "检索 Agent", role: "锚定 + FTS5/vec 混合", x: 0, y: 170 },
    { key: "generate", label: "生成 Agent", role: "论断（evidence_ids）", x: 220, y: 170 },
    { key: "review", label: "审核 Agent", role: "NLI 三分类裁决", x: 440, y: 170 },
    { key: "deliver", label: "教学交付", role: "仅通过论断进讲义", x: 660, y: 170 },
    { key: "question", label: "出题", role: "掌握度驱动题型", x: 440, y: 360 },
    { key: "answer", label: "判分复核", role: "规则预筛 → LLM 裁决", x: 220, y: 360 },
    { key: "decision", label: "决策", role: "advance/retry/regress", x: 0, y: 360 },
  ];
  return defs.map((d) => ({
    id: d.key,
    type: "agent" as const,
    position: { x: d.x, y: d.y },
    data: { label: d.label, role: d.role, state: agents[d.key] } satisfies AgentNodeData,
  }));
}

export function OrchestrationCanvas({
  agents,
  edges: edgeState,
}: {
  agents: AgentStates;
  edges: FlowEdgesState;
}) {
  const nodes = useMemo(() => buildNodes(agents), [agents]);

  const edges = useMemo<Edge[]>(() => {
    const base = (id: string, source: string, target: string, extra?: Partial<Edge>): Edge => ({
      id,
      source,
      target,
      animated: false,
      style: { strokeWidth: 1.5 },
      ...extra,
    });
    return [
      base("e-diag-plan", "diagnose", "plan"),
      base("e-plan-retrieve", "plan", "retrieve"),
      base("e-retrieve-generate", "retrieve", "generate"),
      base("e-generate-review", "generate", "review"),
      // 打回回边：审核否决 → 生成重写（回流）
      base("e-review-generate", "review", "generate", {
        label: "打回重写",
        animated: edgeState.rewriteLoop,
        style: { strokeWidth: 1.5, stroke: "#f59e0b" },
        labelStyle: { fontSize: 10, fill: "#b45309" },
        labelBgStyle: { fill: "#fffbeb" },
      }),
      base("e-review-deliver", "review", "deliver", {
        label: "通过",
        style: { strokeWidth: 1.5, stroke: "#16a34a" },
        labelStyle: { fontSize: 10, fill: "#15803d" },
        labelBgStyle: { fill: "#f0fdf4" },
      }),
      base("e-deliver-question", "deliver", "question"),
      base("e-question-answer", "question", "answer"),
      base("e-answer-decision", "answer", "decision"),
      // 决策回边
      base("e-decision-retrieve", "decision", "retrieve", {
        label: "retry 重教",
        animated: edgeState.retryLoop,
        style: { strokeWidth: 1.5, stroke: "#f59e0b", strokeDasharray: "5 3" },
        labelStyle: { fontSize: 10, fill: "#b45309" },
        labelBgStyle: { fill: "#fffbeb" },
      }),
      base("e-decision-plan", "decision", "plan", {
        label: "regress 回退",
        animated: edgeState.regressLoop,
        style: { strokeWidth: 1.5, stroke: "#ea580c", strokeDasharray: "5 3" },
        labelStyle: { fontSize: 10, fill: "#c2410c" },
        labelBgStyle: { fill: "#fff7ed" },
      }),
      base("e-decision-plan-advance", "decision", "plan", {
        label: "advance 下一主题",
        animated: edgeState.advanceForward,
        style: { strokeWidth: 1.5, stroke: "#16a34a" },
        labelStyle: { fontSize: 10, fill: "#15803d" },
        labelBgStyle: { fill: "#f0fdf4" },
      }),
    ];
  }, [edgeState]);

  const onInit = useCallback(() => {}, []);

  return (
    <div className="h-[560px] w-full rounded-lg border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onInit={onInit}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
