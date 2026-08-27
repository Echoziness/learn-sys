"use client";

import { MessageCircleQuestion } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import type { TopicStateResponse } from "@/lib/types";

/** 面板内部状态机：输入 → 判定中 → 无效（理由）/ 已解答（记录困惑 + 给出解答） */
type FollowupUiState =
  | { kind: "idle" }
  | { kind: "judging" }
  | { kind: "invalid"; reason: string }
  | { kind: "answered"; question: string; answer: string };

/**
 * 动态追问面板（记录困惑 → 解答 → 下一轮教学针对性强化，2026-08-28 设计回归）：
 * 学生提出疑问 → 服务端判定真实性 → 有效则记录困惑并直接给出解答，不即时出题
 * （学生正因困惑而提问，此刻需要的是答案）。困惑记录注入下一轮教学生成端，
 * 并与错题同管道进入误区提炼。不计掌握度（澄清非测评）；最近一条记录刷新可恢复。
 */
export function FollowupPanel({
  sessionId,
  entryId,
  last,
}: {
  sessionId: string;
  entryId: string;
  /** 最近一条困惑记录（刷新恢复用，来自 topic state） */
  last: TopicStateResponse["followup_last"];
}) {
  const [state, setState] = useState<FollowupUiState>({ kind: "idle" });
  const [text, setText] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [open, setOpen] = useState(false);

  // 主题切换 / 服务端状态变化时对齐：有最近困惑记录则恢复到解答展示态
  const lastRound = last?.round_no ?? null;
  useEffect(() => {
    setErrorMsg("");
    if (last) {
      setState({ kind: "answered", question: last.question, answer: last.answer });
    } else {
      setState((s) => (s.kind === "answered" ? { kind: "idle" } : s));
    }
  }, [lastRound, entryId]); // eslint-disable-line react-hooks/exhaustive-deps

  const submitQuestion = async () => {
    const question = text.trim();
    if (!question) return;
    setState({ kind: "judging" });
    setErrorMsg("");
    try {
      const res = await api.askFollowup(sessionId, entryId, question);
      if (res.valid && res.answer) {
        setState({ kind: "answered", question, answer: res.answer });
        setText("");
      } else {
        setState({ kind: "invalid", reason: res.reason });
      }
    } catch (err) {
      setState({ kind: "idle" });
      setErrorMsg(`提问失败：${String((err as Error).message ?? err).slice(0, 160)}`);
    }
  };

  const reset = () => {
    setState({ kind: "idle" });
    setText("");
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
            </span>
            <Badge variant="secondary" className="bg-muted text-[10px] text-muted-foreground">
              记录困惑 · 下轮针对性讲解
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
                  对刚学的内容有疑问？提出来——与当前主题相关的疑问会被记录，
                  先给你解答，下一轮教学还会针对性强化。
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
              <p className="text-sm text-muted-foreground">判定疑问是否真实有效，并生成解答…（约 5-15s）</p>
            )}

            {state.kind === "invalid" && (
              <>
                <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
                  <p className="mb-1 font-medium">这个提问未被记录：</p>
                  <p>{state.reason}</p>
                </div>
                <Button size="sm" variant="outline" className="w-full" onClick={reset}>
                  换个问法再试
                </Button>
              </>
            )}

            {state.kind === "answered" && (
              <>
                <div className="rounded-md border border-border/70 bg-muted/40 p-2 text-xs text-muted-foreground">
                  <p className="mb-1 font-medium text-foreground">你的疑问</p>
                  <p>{state.question}</p>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">解答</p>
                  <p className="text-sm leading-relaxed">{state.answer}</p>
                </div>
                <p className="text-xs text-muted-foreground">
                  ✓ 困惑已记录，下一轮教学会针对性讲解。
                </p>
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
