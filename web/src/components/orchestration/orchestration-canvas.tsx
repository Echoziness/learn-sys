"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { cn } from "@/lib/utils";
import type { AgentStates, FlowEdgesState } from "@/lib/orchestration-reducer";

/** 泳道配色（与设计系统低饱和语义色对齐） */
const EDGE_GRAY = "#d6d3cd";
const EDGE_GREEN = "#16a34a";
const EDGE_AMBER = "#f59e0b";
const EDGE_ORANGE = "#ea580c";

/** Agent 节点自定义类型：标签 + 状态点 + 角标 + 角色说明
 * 每侧双锚点（上下分位，全部隐形）——正/反向边与多重回边各走各的锚点，避免叠线 */
type AgentNodeData = {
  label: string;
  role: string;
  state: AgentStates[keyof AgentStates];
};

const HANDLE_CLS = "!h-1.5 !w-1.5 !min-h-0 !min-w-0 !border-0 !bg-transparent";

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
      {/* 左侧：正向入 in / 回边入 in-back / 反向出 out-left / 第二回边入 in-low */}
      <Handle id="in" type="target" position={Position.Left} className={cn(HANDLE_CLS, "!top-[25%]")} />
      <Handle id="in-back" type="target" position={Position.Left} className={cn(HANDLE_CLS, "!top-[50%]")} />
      <Handle id="out-left" type="source" position={Position.Left} className={cn(HANDLE_CLS, "!top-[68%]")} />
      <Handle id="in-low" type="target" position={Position.Left} className={cn(HANDLE_CLS, "!top-[88%]")} />
      {/* 右侧：反向入 in-right / 正向出 out / 回边入 in-back / 第二回边出 out-back */}
      <Handle id="in-right" type="target" position={Position.Right} className={cn(HANDLE_CLS, "!top-[25%]")} />
      <Handle id="out" type="source" position={Position.Right} className={cn(HANDLE_CLS, "!top-[42%]")} />
      <Handle id="in-back-r" type="target" position={Position.Right} className={cn(HANDLE_CLS, "!top-[66%]")} />
      <Handle id="out-back" type="source" position={Position.Right} className={cn(HANDLE_CLS, "!top-[85%]")} />
      {/* 顶/底：纵向流（切片下行、交付下行、重试上行、决策上行） */}
      <Handle id="in-top" type="target" position={Position.Top} className={cn(HANDLE_CLS, "!left-[30%]")} />
      <Handle id="out-up" type="source" position={Position.Top} className={cn(HANDLE_CLS, "!left-[66%]")} />
      <Handle id="in-bottom" type="target" position={Position.Bottom} className={cn(HANDLE_CLS, "!left-[30%]")} />
      <Handle id="out-down" type="source" position={Position.Bottom} className={cn(HANDLE_CLS, "!left-[66%]")} />

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
    </div>
  );
}

/** 泳道标签节点：纯文字，不参与交互 */
function LaneLabel({ data }: NodeProps<Node<{ label: string }>>) {
  return (
    <div className="pointer-events-none text-[11px] font-medium tracking-widest text-muted-foreground/70">
      {data.label}
    </div>
  );
}

const nodeTypes = { agent: AgentNode, lane: LaneLabel };

/** 静态拓扑坐标（三层泳道：诊断切片 / 教学子图 / 检验决策）+ 泳道标签 */
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
  const lanes: { id: string; label: string; x: number; y: number }[] = [
    { id: "lane-1", label: "① 诊断与切片", x: 0, y: -52 },
    { id: "lane-2", label: "② 教学子图", x: 0, y: 118 },
    { id: "lane-3", label: "③ 检验与决策", x: 0, y: 308 },
  ];
  return [
    ...lanes.map((l) => ({
      id: l.id,
      type: "lane" as const,
      position: { x: l.x, y: l.y },
      data: { label: l.label },
      draggable: false,
      selectable: false,
    })),
    ...defs.map((d) => ({
      id: d.key,
      type: "agent" as const,
      position: { x: d.x, y: d.y },
      data: { label: d.label, role: d.role, state: agents[d.key] } satisfies AgentNodeData,
    })),
  ];
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
    /** smoothstep 直角布线 + 同色箭头；回边用上下分位锚点与正向边平行分离 */
    const base = (
      id: string,
      source: string,
      target: string,
      opts: {
        sourceHandle?: string;
        targetHandle?: string;
        color?: string;
        label?: string;
        labelFill?: string;
        labelBg?: string;
        dashed?: boolean;
        animated?: boolean;
      } = {},
    ): Edge => {
      const color = opts.color ?? EDGE_GRAY;
      return {
        id,
        source,
        target,
        type: "smoothstep",
        sourceHandle: opts.sourceHandle ?? "out",
        targetHandle: opts.targetHandle ?? "in",
        animated: opts.animated ?? false,
        label: opts.label,
        style: { strokeWidth: 1.5, stroke: color, ...(opts.dashed ? { strokeDasharray: "5 3" } : {}) },
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
        labelStyle: opts.label ? { fontSize: 10, fill: opts.labelFill ?? "#57534e" } : undefined,
        labelBgStyle: opts.label ? { fill: opts.labelBg ?? "#faf9f8" } : undefined,
      };
    };
    return [
      // ── 正向主流（灰）──
      base("e-diag-plan", "diagnose", "plan"),
      base("e-plan-retrieve", "plan", "retrieve", { sourceHandle: "out-down", targetHandle: "in-top" }),
      base("e-retrieve-generate", "retrieve", "generate"),
      base("e-generate-review", "generate", "review"),
      base("e-deliver-question", "deliver", "question", { sourceHandle: "out-down", targetHandle: "in-top" }),
      base("e-question-answer", "question", "answer", { sourceHandle: "out-left", targetHandle: "in-right" }),
      base("e-answer-decision", "answer", "decision", { sourceHandle: "out-left", targetHandle: "in-right" }),
      // ── 审核回边：与正向 generate→review 分锚点平行，不再叠线 ──
      base("e-review-generate", "review", "generate", {
        sourceHandle: "out-left",
        targetHandle: "in-back-r",
        label: "打回重写",
        color: EDGE_AMBER,
        labelFill: "#b45309",
        labelBg: "#fffbeb",
        animated: edgeState.rewriteLoop,
      }),
      base("e-review-deliver", "review", "deliver", {
        label: "通过",
        color: EDGE_GREEN,
        labelFill: "#15803d",
        labelBg: "#f0fdf4",
      }),
      // ── 决策回边：重试纵向直达；advance / regress 分锚点平行，不再叠线 ──
      base("e-decision-retrieve", "decision", "retrieve", {
        sourceHandle: "out-up",
        targetHandle: "in-bottom",
        label: "retry 重教",
        color: EDGE_AMBER,
        labelFill: "#b45309",
        labelBg: "#fffbeb",
        dashed: true,
        animated: edgeState.retryLoop,
      }),
      base("e-decision-plan-advance", "decision", "plan", {
        targetHandle: "in-back",
        label: "advance 下一主题",
        color: EDGE_GREEN,
        labelFill: "#15803d",
        labelBg: "#f0fdf4",
        animated: edgeState.advanceForward,
      }),
      base("e-decision-plan-regress", "decision", "plan", {
        sourceHandle: "out-back",
        targetHandle: "in-low",
        label: "regress 回退",
        color: EDGE_ORANGE,
        labelFill: "#c2410c",
        labelBg: "#fff7ed",
        dashed: true,
        animated: edgeState.regressLoop,
      }),
    ];
  }, [edgeState]);

  return (
    <div className="rounded-lg border bg-background">
      <div className="h-[560px] w-full">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.12 }}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {/* 图例：线型语义与画布同源，读图无需猜色 */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t px-3 py-2 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 rounded-full" style={{ background: EDGE_GRAY }} />
          正向流
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 rounded-full" style={{ background: EDGE_GREEN }} />
          通过 / advance
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 border-t-2 border-dashed" style={{ borderColor: EDGE_AMBER }} />
          打回重写 / retry 重教
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 border-t-2 border-dashed" style={{ borderColor: EDGE_ORANGE }} />
          regress 回退
        </span>
      </div>
    </div>
  );
}
