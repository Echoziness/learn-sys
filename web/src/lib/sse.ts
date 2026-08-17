/**
 * SSE 消费：POST 教学流（fetch + ReadableStream 手写帧解析，浏览器
 * EventSource 不支持 POST）与 GET 订阅/回放（EventSource）双模式。
 * 帧协议：`event: <type>\ndata: <json>\n\n`（api/sse.py，实时与回放同构）。
 */

import type { TypedSessionEvent } from "@/lib/types";

import { apiUrl } from "@/lib/api";

export interface SseHandlers {
  onEvent: (event: TypedSessionEvent) => void;
  onError?: (error: unknown) => void;
  onDone?: () => void;
}

interface ParsedFrame {
  event: string | null;
  data: string | null;
}

/**
 * 增量帧解析器：喂任意分块文本，吐完整帧。
 * SSE 以空行（\n\n）分帧；容忍 \r\n 与跨 chunk 撕裂。
 */
export function createFrameParser() {
  let buffer = "";
  const frames: ParsedFrame[] = [];
  return {
    push(chunk: string): ParsedFrame[] {
      buffer += chunk.replace(/\r\n/g, "\n");
      frames.length = 0;
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event: string | null = null;
        let data: string | null = null;
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data = line.slice(5).trim();
        }
        if (data !== null) frames.push({ event, data });
      }
      return frames;
    },
    flush(): ParsedFrame[] {
      const rest = this.push("\n\n");
      buffer = "";
      return rest;
    },
  };
}

function toTyped(event: string | null, data: string, seqHint: number): TypedSessionEvent {
  let payload: unknown = {};
  try {
    payload = JSON.parse(data);
  } catch {
    payload = { message: data };
  }
  return {
    seq: seqHint,
    event_type: event ?? "message",
    payload: payload as TypedSessionEvent["payload"],
  };
}

/** POST SSE（教学执行流）：fetch 流式读取，终止帧（teach_delivered/error）后 resolve。 */
export async function consumePostSse(
  url: string,
  body: unknown,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let seq = 0;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!resp.ok || !resp.body) {
      const text = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}${text ? `: ${text.slice(0, 200)}` : ""}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    const parser = createFrameParser();
    let done = false;
    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        if (frame.data === null) continue;
        const typed = toTyped(frame.event, frame.data, ++seq);
        handlers.onEvent(typed);
        if (frame.event === "teach_delivered" || frame.event === "error") done = true;
      }
    }
    handlers.onDone?.();
  } catch (error) {
    handlers.onError?.(error);
  }
}

/** GET SSE 订阅（实时 /stream 与回放 /replay）：EventSource，返回关闭函数。 */
export function subscribeGetSse(
  url: string,
  handlers: SseHandlers,
  { terminateOn = ["session_end", "error"], seqStart = 0 }: { terminateOn?: string[]; seqStart?: number } = {},
): () => void {
  const source = new EventSource(url);
  let seq = seqStart;
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      source.close();
    }
  };
  source.onmessage = () => {};
  source.onerror = () => {
    // EventSource 对流结束（非断连）也走 onerror——已收到终止帧则视为正常完成
    if (!closed) {
      if (seq > 0) handlers.onDone?.();
      else handlers.onError?.(new Error("SSE 连接失败"));
      close();
    }
  };
  const forward = (type: string) => {
    source.addEventListener(type, (ev: MessageEvent) => {
      const typed = toTyped(type, (ev as MessageEvent<string>).data, ++seq);
      handlers.onEvent(typed);
      if (terminateOn.includes(type)) {
        handlers.onDone?.();
        close();
      }
    });
  };
  for (const type of [
    "session_start",
    "diagnose_done",
    "plan_done",
    "topic_start",
    "retrieve_done",
    "generate_done",
    "review_done",
    "teach_delivered",
    "question_built",
    "answer_graded",
    "scaffold_offered",
    "topic_advance",
    "topic_regress",
    "package_saved",
    "session_end",
    "error",
  ]) {
    forward(type);
  }
  return close;
}

/** 回放：一次性拉全部历史事件（GET /replay?format=json，带 seq——播放器步进/进度用）。 */
export async function fetchReplayEvents(sessionId: string): Promise<TypedSessionEvent[]> {
  const resp = await fetch(apiUrl(`/api/sessions/${sessionId}/replay?format=json`));
  if (!resp.ok) throw new Error(`回放拉取失败: HTTP ${resp.status}`);
  const rows = (await resp.json()) as { seq: number; event_type: string; payload: unknown }[];
  return rows.map((r) => ({
    seq: r.seq,
    event_type: r.event_type,
    payload: r.payload as TypedSessionEvent["payload"],
  }));
}
