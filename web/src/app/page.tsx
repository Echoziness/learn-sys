import Link from "next/link";
import {
  ArrowRight,
  Bot,
  FileText,
  History,
  ListChecks,
  Recycle,
  ShieldCheck,
  SlidersHorizontal,
  Terminal,
} from "lucide-react";

import { PipelineFlow } from "@/components/landing/pipeline-flow";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 段标题：眉标 + 大标题 + 副标——全站段落的统一节奏 */
function SectionHeading({ eyebrow, title, sub }: { eyebrow: string; title: string; sub?: string }) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <p className="text-xs font-semibold tracking-widest text-muted-foreground uppercase">{eyebrow}</p>
      <h2 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h2>
      {sub && <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{sub}</p>}
    </div>
  );
}

const FEATURES = [
  {
    icon: Bot,
    title: "多智能体协同",
    desc: "诊断 / 生成 / 审核 / 决策各司其职，LangGraph 编排「分析 → 生成 → 校验 → 决策」协同闭环，上下文隔离红线防放水。",
  },
  {
    icon: ShieldCheck,
    title: "审核防幻觉回路",
    desc: "每条论断必挂证据链，NLI 三分类逐条裁决；幻觉论断定向打回重写，裁决日志全程可溯——讲义只收「已支持」。",
  },
  {
    icon: SlidersHorizontal,
    title: "个性化适配",
    desc: "掌握度驱动题型阶梯：低掌握度识别式、高掌握度回忆式；连错降维回退、答对进阶挑战，全部确定性决策。",
  },
  {
    icon: Recycle,
    title: "资源沉淀闭环",
    desc: "资源包条目化导出为与知识库同构的 entries.jsonl，可被原样入库复用——系统生产的资源喂回系统。",
  },
];

const METRICS = [
  { value: "0.0%", label: "无溯源幻觉率", target: "赛题目标 <5%" },
  { value: "100%", label: "画像-资源适配率", target: "赛题目标 ≥85%" },
  { value: "100%", label: "知识点覆盖率", target: "赛题目标 ≥90%" },
];

export default function HomePage() {
  return (
    <div className="-mx-4 -my-8 sm:-mx-6">
      {/* ① Hero */}
      <section className="relative overflow-hidden">
        {/* 底纹：极淡点阵 + 顶部径向光 */}
        <div
          className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,oklch(0.6_0.01_80/0.12)_1px,transparent_0)] [background-size:22px_22px]"
          aria-hidden
        />
        <div
          className="absolute inset-x-0 top-0 h-[480px] bg-[radial-gradient(60%_100%_at_50%_0%,oklch(0.6_0.08_255/0.07),transparent)]"
          aria-hidden
        />
        <div className="relative mx-auto grid max-w-7xl items-center gap-16 px-4 py-20 sm:px-6 lg:grid-cols-[1.1fr_0.9fr] lg:py-28">
          <div className="max-w-xl">
            <div
              className="animate-in fade-in slide-in-from-bottom-3 inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground shadow-xs duration-700"
              style={{ animationFillMode: "both" }}
            >
              <span className="size-1.5 rounded-full bg-success" aria-hidden />
              挑战杯揭榜挂帅 · 赛题 XH-202630
            </div>
            <h1 className="mt-6 text-4xl leading-[1.15] font-semibold tracking-tight text-balance sm:text-5xl">
              个性化教学暨
              <br />
              学习资源生产引擎
            </h1>
            <p className="mt-5 text-base leading-relaxed text-muted-foreground">
              输入学习者画像，多智能体协同完成诊断、教学与审核，
              沉淀可溯源、分难度的学习资源包（讲义 / 分阶题 / 实操指南）与学情报告。
            </p>
            <div
              className="animate-in fade-in slide-in-from-bottom-3 mt-8 flex flex-wrap items-center gap-3 duration-700"
              style={{ animationDelay: "150ms", animationFillMode: "both" }}
            >
              <Link href="/sessions/new" className={cn(buttonVariants({ size: "lg" }), "group")}>
                开始导学会话
                <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" />
              </Link>
              <Link href="/sessions" className={cn(buttonVariants({ variant: "outline", size: "lg" }))}>
                <History />
                查看历史会话
              </Link>
            </div>
          </div>
          <PipelineFlow />
        </div>
      </section>

      {/* ② 机制能力 */}
      <section className="border-t bg-card/50">
        <div className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
          <SectionHeading
            eyebrow="Mechanism"
            title="多智能体协同，而非单体大模型包打天下"
            sub="赛题要求的核心技术路径：通过多角色的博弈与协同决策，兼顾个性化适配与专业质量把控。"
          />
          <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="group rounded-xl border bg-card p-6 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md"
              >
                <div className="flex size-10 items-center justify-center rounded-lg bg-muted transition-colors duration-200 group-hover:bg-foreground group-hover:text-background">
                  <f.icon className="size-5" />
                </div>
                <h3 className="mt-4 text-[15px] font-semibold">{f.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ③ 实测指标带（全站唯一重色块） */}
      <section className="mx-auto max-w-7xl px-4 sm:px-6">
        <div className="rounded-2xl bg-foreground px-8 py-12 text-background shadow-lg">
          <div className="grid gap-10 text-center sm:grid-cols-3">
            {METRICS.map((m) => (
              <div key={m.label}>
                <p className="text-5xl font-semibold tracking-tight tabular-nums">{m.value}</p>
                <p className="mt-2 text-sm font-medium opacity-90">{m.label}</p>
                <p className="mt-0.5 text-xs opacity-60">{m.target}</p>
              </div>
            ))}
          </div>
          <p className="mt-10 border-t border-background/15 pt-5 text-center text-xs opacity-60">
            口径与批量评测同源（单一事实源在评测指标模块）；50 组差异化画像全量批跑归档中，此为代表性画像实测值。
          </p>
        </div>
      </section>

      {/* ④ 资源三形态 */}
      <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
        <SectionHeading
          eyebrow="Deliverables"
          title="一次导学会话，三种资源形态"
          sub="导学会话是资源的生产过程：教学论断沉淀为讲义，作答轨迹归档为分阶题，步骤技能提炼为实操指南。"
        />
        <div className="mt-12 grid gap-5 md:grid-cols-3">
          {[
            {
              icon: FileText,
              title: "定制讲义",
              desc: "只收审核「已支持」的论断，逐条可溯源到知识条目；跨轮合并、重教增量不重复。",
              mock: (
                <div className="space-y-1.5" aria-hidden>
                  <div className="h-2 w-11/12 rounded-full bg-muted-foreground/20" />
                  <div className="h-2 w-full rounded-full bg-muted-foreground/20" />
                  <div className="h-2 w-4/5 rounded-full bg-muted-foreground/20" />
                  <div className="mt-2 flex gap-1.5">
                    <div className="h-4 w-16 rounded bg-success/15" />
                    <div className="h-4 w-20 rounded bg-sky-50" />
                  </div>
                </div>
              ),
            },
            {
              icon: ListChecks,
              title: "分阶测试题",
              desc: "掌握度驱动题型阶梯：识别式选择 → 回忆式回答；脚手架与错题全程留档，按轮次天然去重。",
              mock: (
                <div className="space-y-1.5" aria-hidden>
                  {["A", "B", "C"].map((o, i) => (
                    <div key={o} className={cn("flex items-center gap-2 rounded-md border px-2 py-1", i === 1 && "border-foreground/40 bg-muted/50")}>
                      <span className="flex size-4 items-center justify-center rounded-full border text-[9px]">{o}</span>
                      <div className="h-1.5 flex-1 rounded-full bg-muted-foreground/15" />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              icon: Terminal,
              title: "实操指南",
              desc: "procedure 条目专属：步骤 + 可运行示例 + 检查点，步骤同样挂证据链，审核降标不降锚。",
              mock: (
                <div className="space-y-1.5 font-mono text-[10px] text-muted-foreground" aria-hidden>
                  <div className="flex gap-2"><span className="text-foreground">1</span><div className="h-2 w-3/4 rounded-full bg-muted-foreground/20" /></div>
                  <div className="flex gap-2"><span className="text-foreground">2</span><div className="h-2 w-2/3 rounded-full bg-muted-foreground/20" /></div>
                  <div className="mt-1 rounded bg-foreground/85 px-2 py-1.5 text-background/80">$ SELECT DISTINCT …</div>
                </div>
              ),
            },
          ].map((r) => (
            <div
              key={r.title}
              className="group rounded-xl border bg-card p-6 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md"
            >
              <div className="rounded-lg border bg-muted/30 p-4">{r.mock}</div>
              <div className="mt-5 flex items-center gap-2">
                <r.icon className="size-4" />
                <h3 className="text-[15px] font-semibold">{r.title}</h3>
              </div>
              <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{r.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ⑤ Footer */}
      <footer className="border-t bg-card/50">
        <div className="mx-auto flex max-w-7xl flex-col items-center gap-4 px-4 py-10 text-center sm:px-6">
          <p className="text-sm font-semibold tracking-tight">准备好开始一次导学会话了吗？</p>
          <Link href="/sessions/new" className={cn(buttonVariants())}>
            新建导学会话
            <ArrowRight />
          </Link>
          <p className="mt-4 text-xs text-muted-foreground">
            LangGraph · FastAPI + SSE · SQLite（FTS5 + sqlite-vec）· BGE-M3 · Next.js 15 — 完整文档见仓库 docs/
          </p>
        </div>
      </footer>
    </div>
  );
}
