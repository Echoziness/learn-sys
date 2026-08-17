/**
 * REST 封装。base URL：浏览器侧走 NEXT_PUBLIC_API_URL（开发默认
 * http://localhost:8000，容器构建时经 build args 注入）；错误统一抛带状态码的 Error。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export const API_BASE_URL = API_BASE;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.json()).detail ?? "";
    } catch {
      detail = await resp.text().catch(() => "");
    }
    throw new Error(`HTTP ${resp.status}${detail ? `: ${String(detail).slice(0, 200)}` : ""}`);
  }
  return resp.json() as Promise<T>;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function getJson<T>(path: string): Promise<T> {
  return request<T>(path);
}

export const api = {
  createSession: (payload: unknown) => postJson<import("@/lib/types").CreateSessionResponse>("/api/sessions", payload),
  getSession: (id: string) => getJson<import("@/lib/types").SessionDetail>(`/api/sessions/${id}`),
  listSessions: (limit = 100) =>
    getJson<import("@/lib/types").SessionListItem[]>(`/api/sessions?limit=${limit}`),
  question: (id: string, entryId: string) =>
    postJson<import("@/lib/types").QuestionResponse>(`/api/sessions/${id}/topics/${entryId}/question`, {}),
  answer: (id: string, entryId: string, answer: string) =>
    postJson<import("@/lib/types").AnswerResponse>(`/api/sessions/${id}/answers`, {
      entry_id: entryId,
      answer,
    }),
  endSession: (id: string) => postJson<{ session_id: string; status: string }>(`/api/sessions/${id}/end`, {}),
  report: (id: string) => getJson<import("@/lib/types").ReportResponse>(`/api/sessions/${id}/report`),
  resources: (id: string) =>
    getJson<import("@/lib/types").ResourcePackage[]>(`/api/sessions/${id}/resources`),
  topicState: (id: string, entryId: string) =>
    getJson<import("@/lib/types").TopicStateResponse>(`/api/sessions/${id}/topics/${entryId}/state`),
  teachUrl: (id: string, entryId: string) => apiUrl(`/api/sessions/${id}/topics/${entryId}/teach`),
  streamUrl: (id: string, afterSeq = 0) => apiUrl(`/api/sessions/${id}/stream?after_seq=${afterSeq}`),
};
