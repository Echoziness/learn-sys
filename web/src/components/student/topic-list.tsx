"use client";

import { cn } from "@/lib/utils";
import type { PlanTopic } from "@/lib/types";

export interface TopicStatus {
  status: "done" | "current" | "locked" | "regressed";
  mastery: number | null;
  rounds: number;
}

export function TopicList({
  topics,
  statuses,
  currentEntryId,
  onSelect,
}: {
  topics: PlanTopic[];
  statuses: Map<string, TopicStatus>;
  currentEntryId: string | null;
  onSelect: (entryId: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      {topics.map((topic, i) => {
        const st = statuses.get(topic.entry_id) ?? { status: "locked" as const, mastery: null, rounds: 0 };
        const isCurrent = topic.entry_id === currentEntryId;
        return (
          <button
            key={topic.entry_id}
            onClick={() => onSelect(topic.entry_id)}
            className={cn(
              "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors",
              isCurrent ? "border-primary bg-primary/5" : "border-transparent hover:border-border hover:bg-muted/50",
            )}
          >
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
                  st.status === "done" && "bg-green-600 text-white",
                  st.status === "current" && "bg-primary text-primary-foreground",
                  st.status === "regressed" && "bg-orange-600 text-white",
                  st.status === "locked" && "bg-muted text-muted-foreground",
                )}
              >
                {st.status === "done" ? "✓" : st.status === "regressed" ? "↩" : i + 1}
              </span>
              <span className="flex-1 truncate" title={topic.title}>
                {topic.title}
              </span>
              {!topic.target && <span className="text-[10px] text-muted-foreground">前置</span>}
            </div>
            {st.mastery !== null && (
              <div className="mt-1.5 ml-7 h-1 w-3/4 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", st.mastery >= 0.7 ? "bg-green-600" : "bg-amber-500")}
                  style={{ width: `${Math.round(st.mastery * 100)}%` }}
                />
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
