import { ArrowDown, Layers, Package, ScanSearch, UserRound } from "lucide-react";

/** Hero 管线视觉标识：多智能体导学管线的竖排流程示意。
 * 纯 div + SVG（不上 React Flow，样式完全可控）——品牌记忆点。 */

function StepCard({
  icon,
  title,
  desc,
  delay,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  delay: number;
}) {
  return (
    <div
      className="animate-in fade-in slide-in-from-bottom-3 flex items-center gap-3 rounded-xl border bg-card p-3.5 shadow-sm duration-700"
      style={{ animationDelay: `${delay}ms`, animationFillMode: "both" }}
    >
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title}</p>
        <p className="truncate text-xs text-muted-foreground">{desc}</p>
      </div>
    </div>
  );
}

function Connector() {
  return (
    <div className="flex justify-center py-1 text-muted-foreground/60">
      <ArrowDown className="size-3.5" aria-hidden />
    </div>
  );
}

export function PipelineFlow() {
  return (
    <div className="relative mx-auto w-full max-w-sm">
      {/* 背景光晕 */}
      <div
        className="absolute -inset-8 rounded-full bg-[radial-gradient(closest-side,oklch(0.6_0.08_255/0.08),transparent)] blur-xl"
        aria-hidden
      />
      <div className="relative space-y-0.5">
        <StepCard
          icon={<UserRound className="size-4.5" />}
          title="学习者画像"
          desc="学历 · 经验 · 目标 · 教学风格偏好"
          delay={0}
        />
        <Connector />
        <StepCard
          icon={<ScanSearch className="size-4.5" />}
          title="学情诊断 Agent"
          desc="锚定知识盲区，标定难度层级"
          delay={120}
        />
        <Connector />
        <StepCard
          icon={<Layers className="size-4.5" />}
          title="课程切片"
          desc="前置链闭包 + 拓扑排序，确定性生成学习路径"
          delay={240}
        />
        <Connector />

        {/* 教学子图：三个协同 Agent + 审核打回回环 */}
        <div
          className="animate-in fade-in slide-in-from-bottom-3 relative rounded-xl border-2 border-foreground/70 bg-card p-4 shadow-md duration-700"
          style={{ animationDelay: "360ms", animationFillMode: "both" }}
        >
          <span className="absolute -top-2.5 left-4 bg-card px-2 text-[10px] font-semibold tracking-wide text-foreground">
            教学子图 · 多智能体协同
          </span>
          <div className="grid grid-cols-3 gap-2 text-center">
            {[
              { name: "检索", sub: "FTS5 + 向量" },
              { name: "生成", sub: "论断 + 证据链" },
              { name: "审核", sub: "NLI 三分类" },
            ].map((a) => (
              <div key={a.name} className="rounded-lg border bg-muted/40 px-1 py-2.5">
                <p className="text-sm font-semibold">{a.name}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{a.sub}</p>
              </div>
            ))}
          </div>
          <div className="mt-2.5 flex items-center justify-center gap-1.5 rounded-md border border-dashed border-orange-400/70 bg-orange-50/60 px-2 py-1.5">
            <span className="size-1.5 rounded-full bg-orange-500" aria-hidden />
            <p className="text-[11px] font-medium text-orange-700">
              幻觉论断定向打回 · 重写后复审，全程裁决留痕
            </p>
          </div>
        </div>

        <Connector />
        <StepCard
          icon={<Package className="size-4.5" />}
          title="资源包沉淀"
          desc="讲义 · 分阶题 · 实操指南，可条目化入库复用"
          delay={480}
        />
      </div>
    </div>
  );
}
