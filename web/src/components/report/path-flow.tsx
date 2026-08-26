"use client";

import { useMemo } from "react";
import {
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { cn } from "@/lib/utils";

type PathTopic = { entry_id: string; title: string; order: number; target: boolean };
type Regression = { entry_id: string; prereq_id: string | null; reason: string };

/** 学习路径有向图：节点=主题（切片顺序横向推进），灰边=前置推进，
 * 橙色虚线边=连错回退（回退节点下沉到第二行，回退轨迹清晰可辨） */
type PathNodeData = { index: number; label: string; target: boolean; regressed: boolean };

function PathNode({ data }: NodeProps<Node<PathNodeData>>) {
  const { index, label, target, regressed } = data;
  return (
    <div
      className={cn(
        "min-w-[128px] max-w-[150px] rounded-lg border bg-card px-3 py-2 shadow-xs transition-shadow",
        target && "border-foreground/60",
        regressed && "border-orange-400 bg-orange-50",
        !target && !regressed && "border-border"
      )}
      title={label}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-1.5 !w-1.5 !border-0 !bg-border"
      />
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-medium text-muted-foreground tabular-nums">{index}</span>
        <span className="truncate text-xs font-medium">{label.slice(0, 8)}</span>
      </div>
      <p className="mt-0.5 text-[9px] text-muted-foreground">
        {regressed ? "连错回退" : target ? "诊断命中" : "前置补入"}
      </p>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-1.5 !w-1.5 !border-0 !bg-border"
      />
    </div>
  );
}

const nodeTypes = { path: PathNode };

export function PathFlow({ topics, regressions }: { topics: PathTopic[]; regressions: Regression[] }) {
  const { nodes, edges, hasRegress } = useMemo(() => {
    const sorted = [...topics].sort((a, b) => a.order - b.order);
    const regressedIds = new Set(regressions.map((r) => r.entry_id));

    const nodes: Node<PathNodeData>[] = sorted.map((t, i) => ({
      id: t.entry_id,
      type: "path" as const,
      position: { x: i * 176, y: regressedIds.has(t.entry_id) ? 112 : 0 },
      data: {
        index: i + 1,
        label: t.title,
        target: t.target,
        regressed: regressedIds.has(t.entry_id),
      },
    }));

    const edges: Edge[] = [];
    for (let i = 1; i < sorted.length; i++) {
      edges.push({
        id: `chain-${i}`,
        source: sorted[i - 1].entry_id,
        target: sorted[i].entry_id,
        type: "smoothstep",
        style: { stroke: "var(--border)", strokeWidth: 1.5 },
        markerEnd: { type: "arrowclosed" as const, color: "var(--muted-foreground)", width: 14, height: 14 },
      });
    }
    for (const r of regressions) {
      if (!r.prereq_id) continue;
      edges.push({
        id: `regress-${r.entry_id}`,
        source: r.entry_id,
        target: r.prereq_id,
        type: "bezier",
        label: "回退",
        labelStyle: { fill: "#c2410c", fontSize: 10 },
        labelBgStyle: { fill: "white", fillOpacity: 0.9 },
        style: { stroke: "#ea580c", strokeWidth: 1.5, strokeDasharray: "6 3", opacity: 0.8 },
        markerEnd: { type: "arrowclosed" as const, color: "#ea580c", width: 14, height: 14 },
      });
    }
    return { nodes, edges, hasRegress: regressedIds.size > 0 };
  }, [topics, regressions]);

  if (topics.length === 0) {
    return <p className="p-4 text-center text-sm text-muted-foreground">暂无路径数据</p>;
  }

  // 画布宽度随主题数生长（每节点 176px 步进）：卡片横向滚动看全路径，
  // 不靠 fitView 硬缩——主题多时缩小会让节点裁切/小到不可读（旧版右侧被盖的根因）
  const contentWidth = Math.max(topics.length * 176 + 80, 480);

  return (
    <div className="overflow-x-auto rounded-lg border border-border/60 bg-muted/30">
      <div className={cn(hasRegress ? "h-64" : "h-40")} style={{ width: contentWidth, minWidth: "100%" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15, minZoom: 1, maxZoom: 1 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          panOnDrag={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
    </div>
  );
}
