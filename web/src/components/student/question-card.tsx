"use client";

import { useState } from "react";

import { DecisionBadge, decisionDetail } from "@/components/shared/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { AnswerResponse, QuestionResponse } from "@/lib/types";

const TYPE_LABEL: Record<string, string> = {
  choice: "选择题 · 识别式",
  answer: "回答题 · 回忆式",
  scaffold: "脚手架选择题 · 镜像你的错误理解",
};

export function QuestionCard({
  question,
  feedback,
  submitting,
  onSubmit,
  onNext,
  nextLabel,
}: {
  question: QuestionResponse;
  feedback: AnswerResponse | null;
  submitting: boolean;
  onSubmit: (answer: string) => void;
  onNext: () => void;
  nextLabel: string;
}) {
  const [choice, setChoice] = useState<string>("");
  const [text, setText] = useState<string>("");

  const submit = () => {
    const answer = question.question_type === "choice" || question.question_type === "scaffold" ? choice : text;
    if (!answer.trim()) return;
    onSubmit(answer.trim());
  };

  return (
    // 作答卡 = 全页唯一操作焦点：三栏中视觉重量最重（深一号阴影 + 微强化描边）
    <Card className="border-foreground/15 shadow-md">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{TYPE_LABEL[question.question_type] ?? question.question_type}</CardTitle>
          <Badge variant="outline" className="font-mono text-[10px]">
            {question.question_id}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed">{question.prompt}</p>

        {!feedback && (
          <>
            {(question.question_type === "choice" || question.question_type === "scaffold") ? (
              <RadioGroup value={choice} onValueChange={setChoice} className="gap-2">
                {question.options.map((opt) => {
                  const selected = choice === opt.slice(0, 1);
                  return (
                    <div
                      key={opt}
                      className={cn(
                        "flex items-center space-x-2 rounded-lg border px-3 py-2 transition-all duration-150",
                        selected
                          ? "border-foreground/50 bg-muted/60 shadow-xs"
                          : "hover:border-foreground/25 hover:bg-muted/40"
                      )}
                    >
                      <RadioGroupItem value={opt.slice(0, 1)} id={`opt-${opt}`} />
                      <Label htmlFor={`opt-${opt}`} className="cursor-pointer font-normal">
                        {opt}
                      </Label>
                    </div>
                  );
                })}
              </RadioGroup>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="answer-text">用你自己的话回答（实例和通俗说法都可以）</Label>
                <Textarea
                  id="answer-text"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={4}
                  placeholder="写下你的理解…"
                />
              </div>
            )}
            <Button onClick={submit} disabled={submitting || (!choice && !text.trim())} className="w-full">
              {submitting ? "判分中…（LLM 复核约 5-15s）" : "提交作答"}
            </Button>
          </>
        )}

        {feedback && (
          <div className="animate-in fade-in slide-in-from-bottom-2 space-y-3 duration-300">
            <div className="flex items-center gap-2">
              <Badge
                variant="secondary"
                className={feedback.is_correct ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive"}
              >
                {feedback.is_correct ? "回答正确" : "未通过"}
              </Badge>
              <DecisionBadge decision={feedback.decision} />
              {feedback.is_scaffold && <Badge variant="outline" className="text-[10px]">脚手架 · 不计掌握度</Badge>}
            </div>
            <p className="text-sm leading-relaxed">{feedback.evaluation}</p>
            {feedback.missed_requirements.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs">
                <p className="mb-1 font-medium text-amber-800">题目要求中遗漏的要点：</p>
                <ul className="list-inside list-disc space-y-0.5 text-amber-800">
                  {feedback.missed_requirements.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="space-y-1">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>主题掌握度</span>
                <span>
                  {feedback.mastery.toFixed(2)}（门槛 0.7）
                  {question.question_type === "answer" && ` · 关键词覆盖 ${Math.round(feedback.coverage * 100)}%`}
                </span>
              </div>
              <Progress value={feedback.mastery * 100} className="h-2" />
            </div>
            <p className="text-xs text-muted-foreground">{decisionDetail(feedback.decision)}</p>
            <Button onClick={onNext} className="w-full">
              {nextLabel}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
