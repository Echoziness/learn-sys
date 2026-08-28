"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { LearnerProfileInput, ProfileBackground } from "@/lib/types";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

/** 双画像对照演示（PRD §2）：一键填充两端典型画像 */
const PRESETS: { name: string; hint: string; value: LearnerProfileInput }[] = [
  {
    name: "转行零基础",
    hint: "餐饮服务员转行数据分析",
    value: {
      learner_id: "demo-xiaowang",
      background: {
        education: "中专",
        major: "酒店管理",
        goal: "半年内转行数据分析岗",
        experience: "餐饮服务员 5 年，没用过数据库和编程工具",
      },
      style_tags: ["类比教学", "场景实例", "零基础讲解"],
      mastery: {},
    },
  },
  {
    name: "计算机在读",
    hint: "有 Python/SQL 基础的学生",
    value: {
      learner_id: "demo-cs-student",
      background: {
        education: "本科在读（大三）",
        major: "计算机科学与技术",
        goal: "找一份数据分析实习",
        experience: "学过 Python 和 SQL 基础，写过简单查询",
      },
      style_tags: ["简洁直接", "步骤化"],
      mastery: { "BDA-SQL-001": 0.7, "BDA-SQL-002": 0.5 },
    },
  },
];

const EMPTY_BACKGROUND: ProfileBackground = { education: "", major: "", goal: "", experience: "" };

export function ProfileForm() {
  const router = useRouter();
  const [learnerId, setLearnerId] = useState("");
  const [background, setBackground] = useState<ProfileBackground>(EMPTY_BACKGROUND);
  const [styleTags, setStyleTags] = useState("");
  const [masteryText, setMasteryText] = useState("");
  const [formError, setFormError] = useState("");

  // 教学领域（seeds 子目录）：后端 init_db 加载全部域，建会话时自选——
  // 多域时展示选择器，单域时静默默认（不占表单空间）
  const domains = useQuery({ queryKey: ["domains"], queryFn: api.listDomains });
  const [domain, setDomain] = useState<string>("");
  const domainOptions = domains.data ?? [];
  useEffect(() => {
    if (!domain && domainOptions.length > 0) {
      setDomain(domainOptions[0].id);
    }
  }, [domain, domainOptions]);

  const createSession = useMutation({
    mutationFn: (payload: LearnerProfileInput & { domain: string }) =>
      api.createSession(payload),
    onSuccess: (resp) => router.push(`/sessions/${resp.session_id}`),
  });

  const applyPreset = (value: LearnerProfileInput) => {
    setLearnerId(value.learner_id);
    setBackground(value.background);
    setStyleTags(value.style_tags.join("，"));
    setMasteryText(
      Object.keys(value.mastery).length ? JSON.stringify(value.mastery, null, 2) : "",
    );
    setFormError("");
  };

  const submit = () => {
    if (!learnerId.trim()) {
      setFormError("请填写学习者 ID");
      return;
    }
    let mastery: Record<string, number> = {};
    const text = masteryText.trim();
    if (text) {
      try {
        const parsed: unknown = JSON.parse(text);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("不是对象");
        }
        mastery = Object.fromEntries(
          Object.entries(parsed).map(([k, v]) => [k, Number(v)] as const),
        );
      } catch {
        setFormError("初始掌握度必须是 JSON 对象，如 { \"BDA-SQL-001\": 0.7 }");
        return;
      }
    }
    setFormError("");
    createSession.mutate({
      learner_id: learnerId.trim(),
      background,
      style_tags: styleTags
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      mastery,
      domain: domain || "bigdata-analysis",
    });
  };

  return (
    <div className="space-y-6">
      {/* 次要入口：演示画像一键填充，视觉重量低于表单本体 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">快速填充演示画像</span>
        {PRESETS.map((p) => (
          <Button key={p.name} variant="outline" size="sm" onClick={() => applyPreset(p.value)}>
            {p.name}
            <span className="ml-2 text-xs font-normal text-muted-foreground">{p.hint}</span>
          </Button>
        ))}
      </div>

      {/* 表单主体：不用 Card 包裹，字段直接落在纸感底上（字段自带白底描边即轮廓），
          页面焦点全部让位给输入本身 */}
      <div className="space-y-5">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">学习者画像</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            诊断 Agent 将据此产出盲区定位（gap_ids）、难度层级与教学摘要
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="learner-id">学习者 ID *</Label>
            <Input
              id="learner-id"
              value={learnerId}
              onChange={(e) => setLearnerId(e.target.value)}
              placeholder="如 xiaowang-01"
              className="bg-card"
            />
          </div>
          {domainOptions.length > 1 && (
            <div className="space-y-2">
              <Label>教学领域</Label>
              <Select value={domain} onValueChange={setDomain}>
                <SelectTrigger className="bg-card">
                  <SelectValue placeholder="选择领域" />
                </SelectTrigger>
                <SelectContent>
                  {domainOptions.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.id}（{d.entry_count} 条）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                领域 = 知识库切片目录，切换后诊断/教学/资源全部基于所选领域
              </p>
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="education">教育背景</Label>
            <Input
              id="education"
              value={background.education}
              onChange={(e) => setBackground({ ...background, education: e.target.value })}
              placeholder="如 中专 / 本科在读"
              className="bg-card"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="major">专业</Label>
            <Input
              id="major"
              value={background.major}
              onChange={(e) => setBackground({ ...background, major: e.target.value })}
              placeholder="如 酒店管理"
              className="bg-card"
            />
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="style-tags">风格标签（逗号分隔）</Label>
          <Input
            id="style-tags"
            value={styleTags}
            onChange={(e) => setStyleTags(e.target.value)}
            placeholder="如 类比教学，场景实例"
            className="bg-card"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="goal">学习目标</Label>
          <Input
            id="goal"
            value={background.goal}
            onChange={(e) => setBackground({ ...background, goal: e.target.value })}
            placeholder="如 半年内转行数据分析岗"
            className="bg-card"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="experience">相关经验</Label>
          <Textarea
            id="experience"
            value={background.experience}
            onChange={(e) => setBackground({ ...background, experience: e.target.value })}
            placeholder="用过的工具、学过的课程、工作中的相关经历"
            rows={2}
            className="bg-card"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="mastery">初始掌握度（可选 JSON）</Label>
          <Textarea
            id="mastery"
            value={masteryText}
            onChange={(e) => setMasteryText(e.target.value)}
            placeholder='{ "BDA-SQL-001": 0.7 }'
            rows={3}
            className="bg-card font-mono text-xs"
          />
          <p className="text-xs text-muted-foreground">
            条目 ID 见知识库切片（BDA-DB / BDA-SQL / BDA-PANDAS / BDA-VIZ 系列）；留空表示全部从零诊断
          </p>
        </div>

        {(formError || createSession.isError) && (
          <p className="text-sm text-destructive">
            {formError || `建会话失败：${String(createSession.error?.message ?? "").slice(0, 200)}`}
          </p>
        )}

        {/* 提交行：发丝线分隔，主操作是全页唯一重色按钮 */}
        <Separator />
        <div className="flex items-center gap-3">
          <Button size="lg" onClick={submit} disabled={createSession.isPending}>
            {createSession.isPending ? "诊断中…（LLM 调用约 10-20s）" : "开始会话"}
          </Button>
          {createSession.isPending && (
            <span className="text-sm text-muted-foreground">正在生成学情诊断与课程切片</span>
          )}
        </div>
      </div>
    </div>
  );
}
