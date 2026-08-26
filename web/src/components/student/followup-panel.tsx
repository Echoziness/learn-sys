"use client";

import { MessageCircleQuestion } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import type { TopicStateResponse } from "@/lib/types";

/** 面板内部状态机：输入 → 判定中 → 无效（理由）/ 确认题 → 已判分 */
type FollowupUiState =
  | { kind: "idle" }
  | { kind: "judging" }
  | { kind: "invalid"; reason: string }
  | { kind: "question"; question: { question_id: string; prompt: string; options: string[] } }
  | { kind: "answering" }
  | { kind: "graded"; isCorrect: boolean; evaluation: string; correctLabel: string };

/**
 * 动态追问面板（与错题→脚手架同构的澄清管线）：
 * 学生提出疑问 → 服务端判定真实性 → 有效则生成确认型选择题，作答确认理解。
 * 追问不计入掌握度（澄清工具非测评）；未作答确认题经 topic state 刷新恢复。
 */
export function FollowupPanel({
  sessionId,
  entryId,
  pending,
}: {
  sessionId: string;
  entryId: string;
  /** 未作答的追问确认题（刷新恢复用，来自 topic state） */
  pending: TopicStateResponse["followup_pending"];
}) {
  const [state, setState] = useState<FollowupUiState>({ kind: "idle" });
  const [text, setText] = useState("");
  const [choice, setChoice] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [open, setOpen] = useState(false);

  // 主题切换 / 重教后服务端作废追问轮 → 与服务端状态对齐；
  // 存在未作答确认题（刷新恢复）时自动展开浮层，待办不藏死在入口里
  const pendingId = pending?.question_id ?? null;
  useEffect(() => {
    setChoice("");
    setErrorMsg("");
    if (pending) {
      setState({ kind: "question", question: pending });
      setOpen(true);
    } else {
      setState((s) => (s.kind === "question" || s.kind === "answering" ? { kind: "idle" } : s));
    }
  }, [pendingId, entryId]); // eslint-disable-line react-hooks/exhaustive-deps

  const submitQuestion = async () => {
    const question = text.trim();
    if (!question) return;
    setState({ kind: "judging" });
    setErrorMsg("");
    try {
      const res = await api.askFollowup(sessionId, entryId, question);
      if (res.valid && res.prompt && res.question_id) {
        setState({
          kind: "question",
          question: { question_id: res.question_id, prompt: res.prompt, options: res.options },
        });
      } else {
        setState({ kind: "invalid", reason: res.reason });
      }
    } catch (err) {
      setState({ kind: "idle" });
      setErrorMsg(`提问失败：${String((err as Error).message ?? err).slice(0, 160)}`);
    }
  };

  const submitAnswer = async () => {
    if (!choice) return;
    setState({ kind: "answering" });
    setErrorMsg("");
    try {
      const res = await api.answerFollowup(sessionId, entryId, choice);
      setState({
        kind: "graded",
        isCorrect: res.is_correct,
        evaluation: res.evaluation,
        correctLabel: res.correct_label,
      });
    } catch (err) {
      setState({ kind: "idle" });
      setErrorMsg(`判分失败：${String((err as Error).message ?? err).slice(0, 160)}`);
    }
  };

  const reset = () => {
    setState({ kind: "idle" });
    setText("");
    setChoice("");
    setErrorMsg("");
  };

  return (
    // 追问是低频辅助工具（澄清非测评）：收起为单行触发入口，交互全部在 Popover 浮层内，
    // 不占固定布局高度——右栏纵向预算只留作答卡一个重块
    <div className="border-t border-border/70 pt-3">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" className="w-full justify-between gap-2 font-normal">
            <span className="flex items-center gap-2 text-muted-foreground">
              <MessageCircleQuestion className="size-4" />
              有疑问？提问
              {state.kind === "question" && !pending && (
                <span className="size-1.5 rounded-full bg-primary" aria-hidden />
              )}
            </span>
            <Badge variant="secondary" className="bg-muted text-[10px] text-muted-foreground">
              澄清工具 · 不计掌握度
            </Badge>
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="bottom"
          sideOffset={8}
          className="max-h-[60vh] w-(--radix-popover-trigger-width) overflow-y-auto p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">提问 / 追问</h3>
            <Badge variant="outline" className="text-[10px]">不计掌握度</Badge>
          </div>
          <div className="mt-3 space-y-3">
            {state.kind === "idle" && (
              <>
                <p className="text-xs text-muted-foreground">
                  对刚学的内容有疑问？提出来，若与当前主题相关，会生成一道确认题帮你澄清。
                </p>
                <Textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={3}
                  placeholder="例如：LIMIT 和 ORDER BY 的执行顺序是怎样的？"
                />
                <Button size="sm" variant="outline" className="w-full" onClick={() => void submitQuestion()} disabled={!text.trim()}>
                  提交疑问
                </Button>
              </>
            )}

            {state.kind === "judging" && (
              <p className="text-sm text-muted-foreground">判定疑问是否真实有效，并生成确认题…（约 5-15s）</p>
            )}

            {state.kind === "invalid" && (
              <>
                <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
                  <p className="mb-1 font-medium">这个提问未进入澄清管线：</p>
                  <p>{state.reason}</p>
                </div>
                <Button size="sm" variant="outline" className="w-full" onClick={reset}>
                  换个问法再试
                </Button>
              </>
            )}

            {state.kind === "question" && (
              <>
                <p className="text-sm font-medium leading-relaxed">{state.question.prompt}</p>
                <RadioGroup value={choice} onValueChange={setChoice} className="gap-2">
                  {state.question.options.map((opt) => (
                    <div key={opt} className="flex items-center space-x-2 rounded-md border px-3 py-2">
                      <RadioGroupItem value={opt.slice(0, 1)} id={`fu-${opt}`} />
                      <Label htmlFor={`fu-${opt}`} className="cursor-pointer font-normal">
                        {opt}
                      </Label>
                    </div>
                  ))}
                </RadioGroup>
                <Button
                  size="sm"
                  className="w-full"
                  onClick={() => void submitAnswer()}
                  disabled={!choice}
                >
                  确认理解
                </Button>
              </>
            )}
            {state.kind === "answering" && (
              <p className="text-sm text-muted-foreground">判分中…</p>
            )}

            {state.kind === "graded" && (
              <>
                <div className="flex items-center gap-2">
                  <Badge
                    variant="secondary"
                    className={state.isCorrect ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"}
                  >
                    {state.isCorrect ? "理解确认" : "还需澄清"}
                  </Badge>
                  {!state.isCorrect && (
                    <span className="text-xs text-muted-foreground">正确选项是 {state.correctLabel}</span>
                  )}
                </div>
                <p className="text-sm leading-relaxed">{state.evaluation}</p>
                <Button size="sm" variant="outline" className="w-full" onClick={reset}>
                  继续提问
                </Button>
              </>
            )}

            {errorMsg && <p className="text-xs text-destructive">{errorMsg}</p>}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
