"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { fetchReplayEvents, subscribeGetSse } from "@/lib/sse";
import { foldEvents, INITIAL_ORCHESTRATION, type OrchestrationState } from "@/lib/orchestration-reducer";
import type { TypedSessionEvent } from "@/lib/types";

import { EvidenceSidebar } from "@/components/orchestration/evidence-sidebar";
import { OrchestrationCanvas } from "@/components/orchestration/orchestration-canvas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

type Mode = "live" | "replay";

const SPEEDS = [
  { label: "1x", ms: 900 },
  { label: "2x", ms: 450 },
  { label: "4x", ms: 220 },
];

export function OrchestrationView({ sessionId, initialMode = "live" }: { sessionId: string; initialMode?: Mode }) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [events, setEvents] = useState<TypedSessionEvent[]>([]);
  const [cursor, setCursor] = useState(0); // 回放游标（live 模式 = events.length - 1）
  const [playing, setPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(1);
  const [loading, setLoading] = useState(true);
  const [liveError, setLiveError] = useState("");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 拉历史 + （live 模式）继续订阅
  useEffect(() => {
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    setLoading(true);
    setEvents([]);
    setCursor(0);
    setPlaying(false);
    setLiveError("");

    void (async () => {
      try {
        const all = await fetchReplayEvents(sessionId);
        if (cancelled) return;
        setEvents(all);
        setCursor(all.length);
        setLoading(false);
        if (mode === "live" && all.length > 0) {
          const lastSeq = all[all.length - 1]?.seq ?? 0;
          cleanup = subscribeGetSse(api.streamUrl(sessionId, lastSeq), {
            onEvent: (ev) => {
              setEvents((prev) => [...prev, { ...ev, seq: (prev[prev.length - 1]?.seq ?? 0) + 1 }]);
              setCursor((c) => c + 1);
            },
            onError: () => setLiveError("实时连接中断（会话可能已结束，可切换回放模式）"),
            onDone: () => setLiveError(""),
          });
        }
      } catch {
        if (!cancelled) {
          setLoading(false);
          setLiveError("事件流加载失败");
        }
      }
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [sessionId, mode]);

  // 回放播放器
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (mode !== "replay" || !playing) return;
    timerRef.current = setInterval(() => {
      setCursor((c) => {
        if (c >= events.length) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, SPEEDS[speedIdx].ms);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [mode, playing, speedIdx, events.length]);

  const visible = useMemo(() => events.slice(0, cursor), [events, cursor]);
  const state: OrchestrationState = useMemo(
    () => (visible.length ? foldEvents(visible, Number.MAX_SAFE_INTEGER) : INITIAL_ORCHESTRATION),
    [visible],
  );

  const step = useCallback(
    (delta: number) => setCursor((c) => Math.min(Math.max(c + delta, 0), events.length)),
    [events.length],
  );

  const recentEvents = useMemo(() => events.slice(Math.max(0, cursor - 14), cursor), [events, cursor]);
  const isEnd = cursor >= events.length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">多智能体协同调度</h1>
        <Badge variant={mode === "live" ? "default" : "secondary"} className="text-[10px]">
          {mode === "live" ? "实时跟随" : "回放"}
        </Badge>
        {mode === "live" && !liveError && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-600" />
            已消费 {events.length} 条事件
          </span>
        )}
        {liveError && <span className="text-xs text-amber-600">{liveError}</span>}
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant={mode === "replay" ? "default" : "outline"}
            onClick={() => setMode(mode === "live" ? "replay" : "live")}
          >
            {mode === "live" ? "切换回放" : "切回实时"}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-3">
          <OrchestrationCanvas agents={state.agents} edges={state.edges} />

          {mode === "replay" && !loading && (
            <div className="flex items-center gap-2 rounded-lg border p-2">
              <Button size="sm" variant="outline" onClick={() => step(-1)} disabled={cursor === 0}>
                ◀ 上一步
              </Button>
              <Button size="sm" onClick={() => setPlaying((p) => !p)} disabled={isEnd && !playing}>
                {playing ? "暂停" : isEnd ? "重播" : "播放"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => step(1)} disabled={isEnd}>
                下一步 ▶
              </Button>
              {isEnd && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setCursor(0);
                    setPlaying(true);
                  }}
                >
                  从头播放
                </Button>
              )}
              <Separator orientation="vertical" className="h-5" />
              <div className="flex items-center gap-1">
                {SPEEDS.map((s, i) => (
                  <Button
                    key={s.label}
                    size="sm"
                    variant={i === speedIdx ? "default" : "ghost"}
                    className="h-7 px-2 text-xs"
                    onClick={() => setSpeedIdx(i)}
                  >
                    {s.label}
                  </Button>
                ))}
              </div>
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {cursor} / {events.length}
              </span>
            </div>
          )}

          {/* 事件 ticker */}
          <div className="rounded-lg border p-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">事件流（最近 {recentEvents.length} 条）</p>
            <div className="max-h-32 space-y-0.5 overflow-y-auto font-mono text-[11px]">
              {recentEvents.length === 0 && <p className="text-muted-foreground">暂无事件</p>}
              {recentEvents.map((ev, i) => (
                <p key={`${ev.seq}-${i}`} className="truncate">
                  <span className="text-muted-foreground">#{ev.seq} </span>
                  <span
                    className={
                      ev.event_type === "error"
                        ? "text-red-600"
                        : ev.event_type === "review_done"
                          ? "text-amber-700"
                          : ev.event_type === "topic_advance" || ev.event_type === "package_saved"
                            ? "text-green-700"
                            : ""
                    }
                  >
                    {ev.event_type}
                  </span>{" "}
                  <span className="text-muted-foreground">{summarize(ev)}</span>
                </p>
              ))}
            </div>
          </div>
        </div>

        <EvidenceSidebar state={state} />
      </div>
    </div>
  );
}

function summarize(ev: TypedSessionEvent): string {
  const p = ev.payload as unknown as Record<string, unknown>;
  const bits: string[] = [];
  if (typeof p.entry_id === "string") bits.push(p.entry_id);
  if (typeof p.round_no === "number") bits.push(`r${p.round_no}`);
  if (ev.event_type === "teach_delivered" && Array.isArray(p.claims)) bits.push(`${p.claims.length} 论断`);
  if (ev.event_type === "review_done" && typeof p.unsupported_count === "number") bits.push(`未支持 ${p.unsupported_count}`);
  if (ev.event_type === "answer_graded") {
    bits.push(String(p.is_correct ? "✓" : "✗"), String(p.decision ?? ""));
  }
  if (ev.event_type === "topic_regress" && typeof p.prereq_id === "string") bits.push(`→ ${p.prereq_id}`);
  return bits.join(" · ");
}
